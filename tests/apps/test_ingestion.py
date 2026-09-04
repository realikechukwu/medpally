"""apps.ingestion.services — the DB <-> engine adapter.

Local Postgres only (see tests/conftest and config/settings/test); ArrayField
and the partial indexes rule out SQLite for good.
"""

from __future__ import annotations

import threading
from datetime import date, timedelta

import pytest
from django.utils import timezone

from apps.catalog.models import Journal, JournalAlias, Specialty, SpecialtyJournal
from apps.ingestion import services
from apps.papers.models import Paper, PaperSpecialty, PaperSummary
from engine.classify import classify_article
from engine.pubmed.models import FetchedArticle, JournalIdentity
from engine.summarise.client import FakeSummariser

pytestmark = pytest.mark.django_db


def make_article(
    pmid: str,
    *,
    journal_title: str = "Circulation",
    title: str = "A randomized trial of things",
    abstract: str = "x" * 250,
    pub_types: tuple[str, ...] = ("Randomized Controlled Trial",),
    mesh: tuple[str, ...] = (),
    doi: str = "",
    entrez: date = date(2026, 7, 20),
    issn_electronic: str = "",
    nlm_unique_id: str = "",
) -> FetchedArticle:
    identity = JournalIdentity(
        title=journal_title, issn_electronic=issn_electronic, nlm_unique_id=nlm_unique_id
    )
    return FetchedArticle(
        pmid=pmid,
        title=title,
        abstract=abstract,
        journal=identity,
        pub_date_raw=entrez.isoformat(),
        pub_date=entrez,
        entrez_date=entrez,
        doi=doi,
        publication_types=pub_types,
        mesh_terms=mesh,
        category=classify_article(pub_types, bool(abstract), title),
    )


@pytest.fixture
def cardiology() -> Specialty:
    return Specialty.objects.create(
        slug="cardiology",
        name="Cardiology",
        mesh_terms=["Heart Failure"],
        title_keywords=["cardi*"],
        abstract_keywords=["ejection fraction"],
    )


@pytest.fixture
def circulation(cardiology: Specialty) -> Journal:
    journal = Journal.objects.create(
        slug="circulation",
        pubmed_name="Circulation",
        display_name="Circulation",
        short_name="Circ",
        is_general=False,
    )
    JournalAlias.objects.create(journal=journal, value="Circulation")
    SpecialtyJournal.objects.create(specialty=cardiology, journal=journal)
    return journal


@pytest.fixture
def nejm(cardiology: Specialty) -> Journal:
    journal = Journal.objects.create(
        slug="nejm",
        pubmed_name="The New England journal of medicine",
        display_name="NEJM",
        short_name="NEJM",
        is_general=True,
    )
    JournalAlias.objects.create(journal=journal, value="The New England journal of medicine")
    SpecialtyJournal.objects.create(specialty=cardiology, journal=journal)
    return journal


# ---------------------------------------------------------------- resolve_journal


def test_resolve_journal_matches_on_title_alias(circulation: Journal):
    identity = JournalIdentity(title="Circulation")
    assert services.resolve_journal(identity) == circulation


def test_resolve_journal_prefers_issn_over_title(circulation: Journal):
    other = Journal.objects.create(
        slug="other", pubmed_name="Other Journal", display_name="Other", short_name="Other"
    )
    JournalAlias.objects.create(journal=other, value="1524-4539", kind=JournalAlias.Kind.ISSN)
    # Title matches `circulation`, but a real ISSN match should win.
    identity = JournalIdentity(title="Circulation", issn_electronic="1524-4539")
    assert services.resolve_journal(identity) == other


def test_resolve_journal_returns_none_on_miss():
    identity = JournalIdentity(title="Some Unlisted Journal")
    assert services.resolve_journal(identity) is None


# ---------------------------------------------------------------- upsert_articles


def test_upsert_creates_paper_with_resolved_journal(circulation: Journal):
    stats, pmids = services.upsert_articles([make_article("1")])
    paper = Paper.objects.get(pmid="1")
    assert paper.journal == circulation
    assert stats.papers_created == 1
    assert stats.journals_unresolved == 0
    assert pmids == ["1"]


def test_upsert_allows_same_doi_for_distinct_pubmed_records(circulation: Journal):
    doi = "10.1001/jama.2026.8878"

    stats, pmids = services.upsert_articles(
        [make_article("1", doi=doi), make_article("2", doi=doi)]
    )

    assert Paper.objects.filter(doi=doi).count() == 2
    assert stats.papers_created == 2
    assert pmids == ["1", "2"]


def test_upsert_leaves_journal_null_when_unresolved():
    stats, _ = services.upsert_articles([make_article("1", journal_title="Nowhere Journal")])
    paper = Paper.objects.get(pmid="1")
    assert paper.journal is None
    assert paper.journal_name_raw == "Nowhere Journal"
    assert stats.journals_unresolved == 1


def test_upsert_excludes_editorial_category(circulation: Journal):
    article = make_article("1", pub_types=("Editorial",), abstract="")
    assert article.category == "excluded"
    services.upsert_articles([article])
    assert not Paper.objects.filter(pmid="1").exists()


def test_upsert_marks_short_abstract_skipped(circulation: Journal):
    services.upsert_articles([make_article("1", abstract="too short")])
    paper = Paper.objects.get(pmid="1")
    assert paper.summary_status == Paper.SummaryStatus.SKIPPED


def test_upsert_marks_long_abstract_pending(circulation: Journal):
    services.upsert_articles([make_article("1", abstract="x" * 250)])
    paper = Paper.objects.get(pmid="1")
    assert paper.summary_status == Paper.SummaryStatus.PENDING


def test_reingest_is_idempotent_and_never_resets_summary_state(circulation: Journal):
    services.upsert_articles([make_article("1", title="Original title")])
    paper = Paper.objects.get(pmid="1")
    paper.summary_status = Paper.SummaryStatus.OK
    paper.feed_date = date(2020, 1, 1)
    paper.is_visible = False
    paper.save()

    stats, _ = services.upsert_articles([make_article("1", title="Updated title")])

    assert Paper.objects.count() == 1
    assert stats.papers_created == 0
    assert stats.papers_updated == 1
    paper.refresh_from_db()
    assert paper.title == "Updated title"  # content does refresh
    assert paper.summary_status == Paper.SummaryStatus.OK  # but not these
    assert paper.feed_date == date(2020, 1, 1)
    assert paper.is_visible is False


# ---------------------------------------------------------------- specialty linking


def test_link_specialties_creates_journal_scope_for_specialty_journal(circulation: Journal):
    _, pmids = services.upsert_articles([make_article("1")])
    created = services.link_specialties_for_papers(pmids)
    assert created == 1
    link = PaperSpecialty.objects.get(paper__pmid="1")
    assert link.relevance == "journal_scope"


def test_link_specialties_topical_match_for_general_journal(nejm: Journal):
    _, pmids = services.upsert_articles(
        [
            make_article(
                "1", journal_title="The New England journal of medicine", mesh=("Heart Failure",)
            )
        ]
    )
    created = services.link_specialties_for_papers(pmids)
    assert created == 1
    link = PaperSpecialty.objects.get(paper__pmid="1")
    assert link.relevance == "topical_match"
    assert "Heart Failure" in link.matched_mesh


def test_link_specialties_no_match_for_unrelated_general_journal_paper(nejm: Journal):
    _, pmids = services.upsert_articles(
        [
            make_article(
                "1",
                journal_title="The New England journal of medicine",
                title="A study of unrelated things",
                abstract="nothing relevant here " * 20,
                mesh=(),
            )
        ]
    )
    created = services.link_specialties_for_papers(pmids)
    assert created == 0
    assert not PaperSpecialty.objects.filter(paper__pmid="1").exists()


# ---------------------------------------------------------------- recheck_relevance


def test_recheck_relevance_bumps_feed_date_on_first_link(nejm: Journal, cardiology: Specialty):
    paper = Paper.objects.create(
        pmid="1",
        title="Some cardiac finding",
        abstract="x" * 250,
        journal=nejm,
        journal_name_raw="NEJM",
        entrez_date=timezone.now().date() - timedelta(days=30),
        feed_date=timezone.now().date() - timedelta(days=30),
        category="standard",
        mesh_terms=["Heart Failure"],
    )
    created = services.recheck_relevance(days=90)
    assert created == 1
    paper.refresh_from_db()
    assert paper.feed_date == timezone.now().date()


def test_recheck_relevance_does_not_bump_feed_date_when_already_linked(
    nejm: Journal, cardiology: Specialty
):
    Specialty.objects.create(slug="gp", name="GP", mesh_terms=["Heart Failure"])
    paper = Paper.objects.create(
        pmid="1",
        title="Some cardiac finding",
        abstract="x" * 250,
        journal=nejm,
        journal_name_raw="NEJM",
        entrez_date=timezone.now().date() - timedelta(days=30),
        feed_date=timezone.now().date() - timedelta(days=30),
        category="standard",
        mesh_terms=["Heart Failure"],
    )
    PaperSpecialty.objects.create(paper=paper, specialty=cardiology, relevance="topical_match")
    original_feed_date = paper.feed_date

    created = services.recheck_relevance(days=90)

    assert created == 1  # the gp link
    paper.refresh_from_db()
    assert paper.feed_date == original_feed_date  # already visible; no bump


def test_recheck_relevance_ignores_papers_outside_the_window(nejm: Journal, cardiology: Specialty):
    old = timezone.now().date() - timedelta(days=200)
    Paper.objects.create(
        pmid="1",
        title="Some cardiac finding",
        abstract="x" * 250,
        journal=nejm,
        journal_name_raw="NEJM",
        entrez_date=old,
        feed_date=old,
        category="standard",
        mesh_terms=["Heart Failure"],
    )
    assert services.recheck_relevance(days=90) == 0


# ---------------------------------------------------------------- backfill_specialty


def test_backfill_specialty_links_existing_journal_scope_papers_with_no_refetch(
    circulation: Journal,
):
    paper = Paper.objects.create(
        pmid="1",
        title="Old paper",
        abstract="x" * 250,
        journal=circulation,
        journal_name_raw="Circulation",
        entrez_date=date(2020, 1, 1),
        feed_date=date(2020, 1, 1),
        category="standard",
    )
    new_specialty = Specialty.objects.create(slug="new-spec", name="New Specialty")
    SpecialtyJournal.objects.create(specialty=new_specialty, journal=circulation)

    created = services.backfill_specialty(new_specialty)

    assert created == 1
    assert PaperSpecialty.objects.filter(paper=paper, specialty=new_specialty).exists()


def test_backfill_specialty_is_idempotent(circulation: Journal, cardiology: Specialty):
    Paper.objects.create(
        pmid="1",
        title="Old paper",
        abstract="x" * 250,
        journal=circulation,
        journal_name_raw="Circulation",
        entrez_date=date(2020, 1, 1),
        feed_date=date(2020, 1, 1),
        category="standard",
    )
    services.backfill_specialty(cardiology)
    assert services.backfill_specialty(cardiology) == 0
    assert PaperSpecialty.objects.count() == 1


# ---------------------------------------------------------------- select_papers_for_summary


def _pending_paper(pmid: str, *, specialty: Specialty, journal: Journal, **kwargs) -> Paper:
    defaults = {
        "title": f"Paper {pmid}",
        "abstract": "x" * 250,
        "journal": journal,
        "journal_name_raw": journal.display_name,
        "entrez_date": date(2026, 7, 20),
        "feed_date": date(2026, 7, 20),
        "category": Paper.Category.STANDARD,
        "summary_status": Paper.SummaryStatus.PENDING,
    }
    defaults.update(kwargs)
    paper = Paper.objects.create(pmid=pmid, **defaults)
    PaperSpecialty.objects.create(paper=paper, specialty=specialty, relevance="journal_scope")
    return paper


def test_select_papers_for_summary_excludes_papers_without_a_specialty_link(
    circulation: Journal, cardiology: Specialty
):
    Paper.objects.create(
        pmid="1",
        title="Orphan paper",
        abstract="x" * 250,
        journal=circulation,
        journal_name_raw="Circulation",
        entrez_date=date(2026, 7, 20),
        feed_date=date(2026, 7, 20),
        category=Paper.Category.STANDARD,
        summary_status=Paper.SummaryStatus.PENDING,
    )
    assert services.select_papers_for_summary(10) == []


def test_select_papers_for_summary_orders_priority_studies_first(
    circulation: Journal, cardiology: Specialty
):
    standard = _pending_paper("1", specialty=cardiology, journal=circulation)
    priority_study = _pending_paper(
        "2", specialty=cardiology, journal=circulation, is_priority_study=True
    )
    ordered = services.select_papers_for_summary(10)
    assert ordered[0] == priority_study
    assert ordered[1] == standard


def test_select_papers_for_summary_excludes_papers_at_max_attempts(
    circulation: Journal, cardiology: Specialty
):
    paper = _pending_paper(
        "1",
        specialty=cardiology,
        journal=circulation,
        summary_status=Paper.SummaryStatus.FAILED,
        summary_attempts=services.MAX_SUMMARY_ATTEMPTS,
    )
    assert paper not in services.select_papers_for_summary(10)


# ---------------------------------------------------------------- summarise_paper


def test_summarise_paper_success_persists_summary(circulation: Journal, cardiology: Specialty):
    paper = _pending_paper("1", specialty=cardiology, journal=circulation)
    ok = services.summarise_paper(paper, FakeSummariser())
    assert ok is True
    paper.refresh_from_db()
    assert paper.summary_status == Paper.SummaryStatus.OK
    summary = PaperSummary.objects.get(paper=paper)
    assert summary.model_name == "fake-model"


def test_summarise_paper_failure_then_retry_then_skipped(
    circulation: Journal, cardiology: Specialty
):
    paper = _pending_paper("1", specialty=cardiology, journal=circulation)
    failing = FakeSummariser(fail_pmids=frozenset({"1"}))

    services.summarise_paper(paper, failing)
    paper.refresh_from_db()
    assert paper.summary_status == Paper.SummaryStatus.FAILED
    assert paper.summary_attempts == 1

    services.summarise_paper(paper, failing)
    paper.refresh_from_db()
    assert paper.summary_status == Paper.SummaryStatus.FAILED
    assert paper.summary_attempts == 2

    services.summarise_paper(paper, failing)
    paper.refresh_from_db()
    assert paper.summary_status == Paper.SummaryStatus.SKIPPED
    assert paper.summary_attempts == 3


def test_summarise_papers_runs_a_batch_and_totals_tokens(
    transactional_db, circulation: Journal, cardiology: Specialty
):
    # transactional_db (real commits) rather than the default rollback-wrapped
    # db: summarise_papers fans work out over a thread pool, and each worker
    # opens its own connection — one that can't see rows still sitting inside
    # an uncommitted test transaction on the main thread's connection.
    a = _pending_paper("1", specialty=cardiology, journal=circulation)
    b = _pending_paper("2", specialty=cardiology, journal=circulation)
    stats = services.summarise_papers([a, b], FakeSummariser())
    assert stats.attempted == 2
    assert stats.ok == 2
    assert stats.failed == 0


# ---------------------------------------------------------------- advisory lock


def test_ingest_lock_acquires_and_releases_sequentially():
    with services.ingest_lock() as first:
        assert first is True
    with services.ingest_lock() as second:
        assert second is True


def test_ingest_lock_skips_a_concurrent_holder(django_db_blocker):
    holding = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with django_db_blocker.unblock():
            from django.db import connection

            with connection.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s)", [services.INGEST_ADVISORY_LOCK_KEY])
            holding.set()
            release.wait(5)
            with connection.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", [services.INGEST_ADVISORY_LOCK_KEY])
            connection.close()

    thread = threading.Thread(target=hold)
    thread.start()
    try:
        assert holding.wait(5)
        with services.ingest_lock() as acquired:
            assert acquired is False
    finally:
        release.set()
        thread.join(5)


# ---------------------------------------------------------------- reclassification


def _classifiable_paper(pmid, title, *, is_rct, is_priority, abstract="x" * 250, days_ago=1):
    return Paper.objects.create(
        pmid=pmid,
        title=title,
        abstract=abstract,
        journal_name_raw="Circulation",
        entrez_date=timezone.now().date() - timedelta(days=days_ago),
        feed_date=timezone.now().date() - timedelta(days=days_ago),
        category=Paper.Category.STANDARD,
        publication_types=["Journal Article"],
        is_rct=is_rct,
        is_priority_study=is_priority,
        summary_status=Paper.SummaryStatus.OK,
    )


@pytest.mark.django_db
def test_reclassify_corrects_a_stored_rct_flag():
    """The exact bug: a title variant the old rules missed, stored as False."""
    paper = _classifiable_paper(
        "1",
        "Mechanical Thrombectomy in Ischemic Stroke: The DISCOUNT Randomized Clinical Trial.",
        is_rct=False,
        is_priority=True,
    )
    stats = services.reclassify_papers(days=90)

    paper.refresh_from_db()
    assert paper.is_rct is True
    assert stats.rct_added == 1


@pytest.mark.django_db
def test_reclassify_clears_a_false_positive():
    paper = _classifiable_paper(
        "1",
        "Drug-coated balloons versus stents: a systematic review and meta-analysis.",
        is_rct=True,
        is_priority=True,
        abstract="We pooled twelve randomised controlled trials. " + "x" * 200,
    )
    services.reclassify_papers(days=90)

    paper.refresh_from_db()
    assert paper.is_rct is False


@pytest.mark.django_db
def test_reclassify_never_moves_a_paper_up_the_feed():
    """Correcting a badge must not republish months-old papers to the top.

    This is the deliberate difference from recheck_relevance, which does bump
    feed_date — a paper gaining a specialty link has genuinely just become
    relevant, whereas this paper was always an RCT and we were simply wrong.
    """
    paper = _classifiable_paper(
        "1",
        "The DISCOUNT Randomized Clinical Trial.",
        is_rct=False,
        is_priority=True,
        days_ago=60,
    )
    original_feed_date = paper.feed_date

    services.reclassify_papers(days=90)

    paper.refresh_from_db()
    assert paper.is_rct is True
    assert paper.feed_date == original_feed_date


@pytest.mark.django_db
def test_reclassify_is_idempotent():
    _classifiable_paper(
        "1", "The DISCOUNT Randomized Clinical Trial.", is_rct=False, is_priority=True
    )

    first = services.reclassify_papers(days=90)
    second = services.reclassify_papers(days=90)

    assert first.changed == 1
    assert second.changed == 0


@pytest.mark.django_db
def test_reclassify_dry_run_writes_nothing():
    paper = _classifiable_paper(
        "1", "The DISCOUNT Randomized Clinical Trial.", is_rct=False, is_priority=True
    )
    stats = services.reclassify_papers(days=90, dry_run=True)

    paper.refresh_from_db()
    assert stats.rct_added == 1  # still reports what it would do
    assert paper.is_rct is False


@pytest.mark.django_db
def test_reclassify_respects_the_window():
    paper = _classifiable_paper(
        "1", "The DISCOUNT Randomized Clinical Trial.", is_rct=False, is_priority=True, days_ago=200
    )
    stats = services.reclassify_papers(days=90)

    paper.refresh_from_db()
    assert stats.examined == 0
    assert paper.is_rct is False


@pytest.mark.django_db
def test_reclassify_does_not_touch_category_or_summary_state():
    """Recomputing category could remove papers people have already saved."""
    paper = _classifiable_paper(
        "1", "The DISCOUNT Randomized Clinical Trial.", is_rct=False, is_priority=True
    )
    services.reclassify_papers(days=90)

    paper.refresh_from_db()
    assert paper.category == Paper.Category.STANDARD
    assert paper.summary_status == Paper.SummaryStatus.OK
