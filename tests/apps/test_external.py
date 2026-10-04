"""Papers a reader shares in from outside their feed, and the Android share target."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Journal, Specialty, SpecialtyJournal
from apps.feed import external, featured, services
from apps.feed.models import UserPaperState
from apps.ingestion import services as ingestion
from apps.papers.models import Paper
from engine.errors import TransportError
from engine.pubmed.models import FetchedArticle, JournalIdentity
from tests.apps.test_feed import make_paper, make_user, subscribe

pytestmark = pytest.mark.django_db


@pytest.fixture
def cardiology():
    return Specialty.objects.create(slug="cardiology", name="Cardiology")


@pytest.fixture
def circulation(cardiology):
    journal = Journal.objects.create(
        slug="circulation", pubmed_name="Circulation", display_name="Circulation", short_name="Circ"
    )
    SpecialtyJournal.objects.create(specialty=cardiology, journal=journal)
    return journal


@pytest.fixture
def gut():
    return Journal.objects.create(
        slug="gut", pubmed_name="Gut", display_name="Gut", short_name="Gut"
    )


@pytest.fixture
def user(cardiology, circulation):
    reader = make_user("reader@example.com", cardiology)
    subscribe(reader, circulation)
    return reader


def shared_article(pmid: str = "40000001", title: str = "A trial nobody's feed picked up"):
    return FetchedArticle(
        pmid=pmid,
        title=title,
        abstract="x" * 300,
        journal=JournalIdentity(title="Journal of Elsewhere"),
        pub_date_raw="2026 Sep 30",
        pub_date=date(2026, 9, 30),
        entrez_date=date(2026, 9, 28),
        doi="10.5555/elsewhere.1",
        category=Paper.Category.STANDARD,
    )


class FakePubMed:
    def __init__(self, articles=(), error: Exception | None = None):
        self.articles = {a.pmid: a for a in articles}
        self.error = error
        self.calls = 0

    def search_pmids(self, term, *, retmax=5, sort=""):
        self.calls += 1
        if self.error:
            raise self.error
        return [pmid for pmid, a in self.articles.items() if a.doi and a.doi in term]

    def efetch_by_pmid(self, pmids):
        self.calls += 1
        if self.error:
            raise self.error
        return [self.articles[p] for p in pmids if p in self.articles]


@pytest.fixture
def pubmed(monkeypatch):
    fake = FakePubMed([shared_article()])
    monkeypatch.setattr(external, "pubmed_client", lambda: fake)
    return fake


@pytest.fixture
def summarised(monkeypatch):
    started: list[int] = []
    monkeypatch.setattr(external, "summarise_soon", started.append)
    return started


# ---------------------------------------------------------------- finding


def test_the_add_screen_lives_under_saved(client, user):
    client.force_login(user)
    resp = client.get(reverse("feed:add_paper"))
    assert resp.status_code == 200
    assert resp.context["active_tab"] == "saved"
    assert b'data-nav-back href="/feed/read-later/"' in resp.content


def test_a_paper_already_in_the_feed_needs_no_pubmed_call(client, user, circulation, pubmed):
    make_paper("123", journal=circulation, feed_date=date(2026, 9, 1), title="Feed paper")
    client.force_login(user)

    resp = client.get(reverse("feed:add_paper"), {"q": "https://pubmed.ncbi.nlm.nih.gov/123/"})

    assert b"Feed paper" in resp.content
    assert b"This paper is in your feed." in resp.content
    assert pubmed.calls == 0


def test_an_android_share_previews_without_saving(client, user, pubmed):
    client.force_login(user)

    resp = client.get(
        reverse("feed:add_paper"),
        {
            "title": "Elsewhere | Journal",
            "text": "Look at this https://doi.org/10.5555/elsewhere.1",
            "url": "",
        },
    )

    assert resp.status_code == 200
    assert b"Found on PubMed" in resp.content
    assert b"A trial nobody&#x27;s feed picked up" in resp.content
    assert b"saved as External" in resp.content
    assert not Paper.objects.filter(pmid="40000001").exists()
    assert not UserPaperState.objects.exists()


def test_an_unmatched_share_says_so(client, user, pubmed):
    client.force_login(user)
    resp = client.get(reverse("feed:add_paper"), {"url": "https://example.com/blog/podcasts"})
    assert b"couldn&#x27;t match that to a PubMed record" in resp.content


def test_pubmed_being_down_is_reported_not_raised(client, user, monkeypatch):
    monkeypatch.setattr(
        external, "pubmed_client", lambda: FakePubMed(error=TransportError("timeout"))
    )
    client.force_login(user)

    resp = client.get(reverse("feed:add_paper"), {"q": "10.5555/elsewhere.1"})

    assert resp.status_code == 200
    assert b"couldn&#x27;t reach PubMed" in resp.content


def test_the_share_target_survives_signing_in(client):
    resp = client.get(reverse("feed:add_paper"), {"url": "https://pubmed.ncbi.nlm.nih.gov/1/"})
    assert resp.status_code == 302
    assert "next=/feed/add/%3Furl%3D" in resp["Location"]


# ---------------------------------------------------------------- saving


def test_saving_a_new_paper_stores_it_privately_and_marks_it_external(
    client, user, pubmed, summarised, django_capture_on_commit_callbacks
):
    client.force_login(user)

    with django_capture_on_commit_callbacks(execute=True):
        resp = client.post(reverse("feed:add_paper"), {"pmid": "40000001"})

    assert resp.status_code == 302 and resp["Location"] == reverse("feed:read_later")
    paper = Paper.objects.get(pmid="40000001")
    assert paper.is_external
    assert paper.summary_status == Paper.SummaryStatus.PENDING
    state = UserPaperState.objects.get(user=user, paper=paper)
    assert state.saved_at is not None and state.external_at is not None
    assert summarised == [paper.pk]
    assert not services.feed_queryset(user).filter(pk=paper.pk).exists()


def test_saving_a_paper_from_your_own_feed_is_an_ordinary_save(client, user, circulation, pubmed):
    paper = make_paper("123", journal=circulation, feed_date=date(2026, 9, 1))
    client.force_login(user)

    client.post(reverse("feed:add_paper"), {"pmid": "123"})

    state = UserPaperState.objects.get(user=user, paper=paper)
    assert state.saved_at is not None
    assert state.external_at is None


def test_a_known_paper_outside_your_feed_is_external_for_you_only(
    client, user, gut, cardiology, pubmed
):
    paper = make_paper("456", journal=gut, feed_date=date(2026, 9, 1))
    client.force_login(user)

    client.post(reverse("feed:add_paper"), {"pmid": "456"})

    assert UserPaperState.objects.get(user=user, paper=paper).external_at is not None
    paper.refresh_from_db()
    assert not paper.is_external


def unmatched_paper(journal: Journal, pmid: str = "456") -> Paper:
    """A general-journal paper ingestion stored without matching any specialty."""
    paper = make_paper(
        pmid,
        journal=journal,
        feed_date=date(2026, 9, 18),
        summary_status=Paper.SummaryStatus.PENDING,
    )
    paper.summary.delete()
    return paper


def test_a_known_paper_no_feed_carries_is_summarised_when_added(
    client, user, gut, pubmed, summarised, django_capture_on_commit_callbacks
):
    paper = unmatched_paper(gut)
    client.force_login(user)

    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("feed:add_paper"), {"pmid": paper.pmid})

    assert summarised == [paper.pk]


def test_saving_a_summarised_paper_starts_no_summary(
    client, user, gut, pubmed, summarised, django_capture_on_commit_callbacks
):
    paper = make_paper("456", journal=gut, feed_date=date(2026, 9, 1))
    client.force_login(user)

    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("feed:add_paper"), {"pmid": paper.pmid})

    assert summarised == []


def test_sharing_a_dismissed_paper_brings_it_back(client, user, circulation, pubmed):
    paper = make_paper("123", journal=circulation, feed_date=date(2026, 9, 1))
    UserPaperState.objects.create(user=user, paper=paper, dismissed_at=timezone.now())
    client.force_login(user)

    client.post(reverse("feed:add_paper"), {"pmid": "123"})

    state = UserPaperState.objects.get(user=user, paper=paper)
    assert state.dismissed_at is None
    assert state.saved_at is not None
    assert state.external_at is None


def test_saving_needs_a_chosen_paper(client, user, pubmed):
    client.force_login(user)
    resp = client.post(reverse("feed:add_paper"), {"pmid": "not-a-pmid"})
    assert resp.status_code == 200
    assert b"Choose a paper to save first." in resp.content


# ---------------------------------------------------------------- reading it back


def external_paper(user, *, status=Paper.SummaryStatus.PENDING) -> Paper:
    paper = ingestion.store_shared_article(shared_article())
    Paper.objects.filter(pk=paper.pk).update(summary_status=status)
    paper.refresh_from_db()
    now = timezone.now()
    UserPaperState.objects.create(user=user, paper=paper, saved_at=now, external_at=now)
    return paper


def test_the_external_tab_lists_only_papers_brought_in(client, user, circulation):
    feed_paper = make_paper("123", journal=circulation, feed_date=date(2026, 9, 1), title="Mine")
    UserPaperState.objects.create(user=user, paper=feed_paper, saved_at=timezone.now())
    external_paper(user)
    client.force_login(user)

    saved = client.get(reverse("feed:read_later"))
    tab = client.get(reverse("feed:external"))

    assert b"Mine" in saved.content and b"A trial nobody" in saved.content
    assert saved.content.count(b"badge-external") == 1
    assert b"A trial nobody" in tab.content
    assert b"Mine" not in tab.content
    assert b'href="/p/40000001/?from=external"' in tab.content
    assert b"Summary on its way." in tab.content


def test_an_unsummarised_paper_opens_only_for_whoever_added_it(client, user, cardiology):
    paper = external_paper(user)
    other = make_user("other@example.com", cardiology)
    url = reverse("paper_detail", args=[paper.pmid])

    assert client.get(url).status_code == 404
    client.force_login(other)
    assert client.get(url).status_code == 404

    client.force_login(user)
    resp = client.get(url, {"from": "external"})
    assert resp.status_code == 200
    assert b"Writing the summary" in resp.content
    assert b'hx-trigger="every 6s"' in resp.content
    assert b"Back to External" in resp.content


def test_a_finished_summary_stops_the_polling(client, user):
    paper = external_paper(user)
    paper.summary_status = Paper.SummaryStatus.OK
    paper.save()
    paper.refresh_from_db()
    from apps.papers.models import PaperSummary

    PaperSummary.objects.create(
        paper=paper,
        study_type="RCT",
        context="c",
        finding="The finding",
        so_what="s",
        tags=[],
        model_name="fake",
        prompt_version="v1",
    )
    client.force_login(user)

    resp = client.get(reverse("paper_detail", args=[paper.pmid]))

    assert b"The finding" in resp.content
    assert b"hx-trigger" not in resp.content.split(b'id="summary-slot"')[1].split(b">")[0]


def test_whoever_added_it_can_unsave_it_before_it_is_summarised(client, user, cardiology):
    paper = external_paper(user)
    other = make_user("other@example.com", cardiology)

    client.force_login(other)
    assert client.post(reverse("feed:toggle_save", args=[paper.pmid])).status_code == 404

    client.force_login(user)
    assert client.post(reverse("feed:toggle_save", args=[paper.pmid])).status_code == 200
    assert UserPaperState.objects.get(user=user, paper=paper).saved_at is None


# ---------------------------------------------------------------- the pipeline


def test_external_papers_are_summarised_by_the_nightly_run_too(user):
    paper = external_paper(user)
    assert paper in ingestion.select_papers_for_summary(10)


def test_ingestion_finding_a_shared_paper_makes_it_a_feed_paper(user):
    paper = external_paper(user)

    ingestion.upsert_articles([shared_article()])

    paper.refresh_from_db()
    assert not paper.is_external


def test_a_known_paper_a_reader_added_is_summarised_by_the_nightly_run(user, gut):
    paper = unmatched_paper(gut)
    assert paper not in ingestion.select_papers_for_summary(10)

    now = timezone.now()
    UserPaperState.objects.create(user=user, paper=paper, saved_at=now, external_at=now)

    assert paper in ingestion.select_papers_for_summary(10)


def test_a_shared_paper_ingestion_later_finds_is_still_summarised(user):
    paper = external_paper(user)

    ingestion.upsert_articles([shared_article()])

    assert paper in ingestion.select_papers_for_summary(10)


def test_papers_readers_added_are_summarised_first(user, circulation, gut):
    feed_paper = make_paper(
        "123",
        journal=circulation,
        feed_date=date(2026, 10, 3),
        summary_status=Paper.SummaryStatus.PENDING,
        is_priority_study=True,
    )
    feed_paper.summary.delete()
    ingestion.link_specialties_for_papers([feed_paper.pmid])
    added = unmatched_paper(gut)
    now = timezone.now()
    UserPaperState.objects.create(user=user, paper=added, saved_at=now, external_at=now)

    assert ingestion.select_papers_for_summary(1) == [added]
    assert ingestion.select_papers_for_summary(10) == [added, feed_paper]


def test_external_papers_are_never_featured(user, cardiology, circulation):
    from apps.papers.models import PaperSpecialty, PaperSummary

    week = date(2026, 9, 28)
    paper = external_paper(user, status=Paper.SummaryStatus.OK)
    Paper.objects.filter(pk=paper.pk).update(
        journal=circulation, feed_date=week - timedelta(days=2)
    )
    PaperSummary.objects.create(
        paper=paper,
        study_type="RCT",
        context="c",
        finding="f",
        so_what="s",
        tags=[],
        model_name="fake",
        prompt_version="v1",
    )
    PaperSpecialty.objects.create(paper=paper, specialty=cardiology, relevance="journal_scope")

    candidates = featured.gather_candidates(cardiology, week)

    assert paper.pk not in [c.paper_id for c in candidates]


def test_the_installed_app_is_a_share_target(client):
    manifest = json.loads(client.get(reverse("manifest")).content)
    assert manifest["share_target"] == {
        "action": "/feed/add/",
        "method": "GET",
        "params": {"title": "title", "text": "text", "url": "url"},
    }
