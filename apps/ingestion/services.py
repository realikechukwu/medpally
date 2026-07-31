"""The thin adapter between the engine (no Django) and the database.

DB rows go in, engine dataclasses come out; engine dataclasses go in, DB rows
come back. Nothing here talks to PubMed or OpenAI directly — that's
engine.pubmed.client and engine.summarise.client. This module only knows how to
turn a FetchedArticle into a Paper and back, and how to decide which Journal an
article belongs to.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Case, Exists, IntegerField, OuterRef, Q, Subquery, When
from django.utils import timezone

from apps.accounts.models import UserJournalSubscription
from apps.catalog.models import Journal, JournalAlias, Specialty, SpecialtyJournal
from apps.papers.models import Paper, PaperSpecialty, PaperSummary
from engine import classify
from engine.pubmed.models import FetchedArticle, JournalIdentity
from engine.pubmed.queries import chunked
from engine.relevance import JOURNAL_SCOPE, TOPICAL_MATCH, match_specialty
from engine.summarise.client import Summariser

logger = logging.getLogger(__name__)

# Any fixed integer identifies the lock; the value has no meaning beyond being
# consistent between callers. Derived from "medfeed_ingest" so it is legible in
# `pg_locks` rather than an arbitrary number.
INGEST_ADVISORY_LOCK_KEY = 0x6D656466 ^ 0x696E6765  # "medf" ^ "inge"

MAX_SUMMARY_ATTEMPTS = 3


# ---------------------------------------------------------------- locking


@contextmanager
def ingest_lock() -> Iterator[bool]:
    """Wraps a nightly run so two overlapping crons can't ingest concurrently.

    A session-level advisory lock, held for the lifetime of the `with` block and
    released explicitly — Django's persistent connections (CONN_MAX_AGE) mean we
    cannot rely on disconnect to free it.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [INGEST_ADVISORY_LOCK_KEY])
        (acquired,) = cursor.fetchone()
    try:
        yield bool(acquired)
    finally:
        if acquired:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [INGEST_ADVISORY_LOCK_KEY])


# ---------------------------------------------------------------- journal set


def active_journal_queryset() -> Iterable[Journal]:
    """Every journal that matters right now: subscribed to, or in a live preset.

    A brand-new signup's feed must not be empty, so a journal in an active
    specialty's default preset is fetched even before anyone has subscribed.
    """
    subscribed = UserJournalSubscription.objects.filter(is_active=True).values("journal_id")
    presets = SpecialtyJournal.objects.filter(specialty__is_active=True).values("journal_id")
    return (
        Journal.objects.filter(
            Q(id__in=Subquery(subscribed)) | Q(id__in=Subquery(presets)),
            is_active=True,
        )
        .distinct()
        .order_by("slug")
    )


# ---------------------------------------------------------------- journal resolution


def resolve_journal(identity: JournalIdentity, raw_name: str = "") -> Journal | None:
    """Match an article's journal against the catalog.

    Priority order: ISSN-electronic, ISSN-print, NLM unique ID, MEDLINE
    abbreviation, then the title as PubMed printed it — each looked up in
    JournalAlias, which is where seed_catalog and resolve_journals record every
    name and identifier a journal is known by. A miss returns None; the caller
    logs it and the paper is stored with journal=None for the admin to fix.
    """
    candidates: list[tuple[str, str]] = []
    if identity.issn_electronic:
        candidates.append((identity.issn_electronic, JournalAlias.Kind.ISSN))
    if identity.issn_print:
        candidates.append((identity.issn_print, JournalAlias.Kind.ISSN))
    if identity.nlm_unique_id:
        candidates.append((identity.nlm_unique_id, JournalAlias.Kind.NLM_UID))
    if identity.medline_ta:
        candidates.append((identity.medline_ta, JournalAlias.Kind.MEDLINE_TA))
    if identity.title:
        candidates.append((identity.title, JournalAlias.Kind.PUBMED_TITLE))
    if identity.iso_abbreviation:
        candidates.append((identity.iso_abbreviation, JournalAlias.Kind.ISO_ABBREV))
    if raw_name:
        candidates.append((raw_name, JournalAlias.Kind.PUBMED_TITLE))

    normalized_in_order = [
        JournalAlias.normalize(value, kind) for value, kind in candidates if value
    ]
    if not normalized_in_order:
        return None

    by_normalized = {
        alias.value_normalized: alias.journal
        for alias in JournalAlias.objects.filter(
            value_normalized__in=normalized_in_order
        ).select_related("journal")
    }
    for normalized in normalized_in_order:
        journal = by_normalized.get(normalized)
        if journal is not None:
            return journal
    return None


# ---------------------------------------------------------------- fetch -> Paper


@dataclass(slots=True)
class IngestStats:
    journals_queried: int = 0
    articles_fetched: int = 0
    papers_created: int = 0
    papers_updated: int = 0
    specialty_links_created: int = 0
    journals_unresolved: int = 0
    journal_hits: dict[int, int] = field(default_factory=dict)


# Fields refreshed on re-ingest. Deliberately excludes feed_date, ingested_at,
# summary_status (and its attempts/error), and is_visible: a re-fetched paper
# must never lose a summary, bounce back to the top of the feed, or have an
# admin's kill switch silently reverted.
_UPSERT_UPDATE_FIELDS = [
    "doi",
    "title",
    "abstract",
    "journal",
    "journal_name_raw",
    "pub_date",
    "pub_date_raw",
    "pub_sort_date",
    "entrez_date",
    "publication_types",
    "mesh_terms",
    "authors",
    "category",
    "is_priority_study",
    "is_rct",
    "updated_at",
]


def _initial_summary_status(article: FetchedArticle) -> str:
    if len(article.abstract) < settings.SUMMARY_MIN_ABSTRACT_CHARS:
        return Paper.SummaryStatus.SKIPPED
    return Paper.SummaryStatus.PENDING


def _paper_from_article(article: FetchedArticle, journal: Journal | None) -> Paper:
    feed_date = article.entrez_date or article.pub_date or timezone.now().date()
    return Paper(
        pmid=article.pmid,
        doi=article.doi,
        title=article.title,
        abstract=article.abstract,
        journal=journal,
        journal_name_raw=article.journal.best_name,
        pub_date=article.pub_date,
        pub_date_raw=article.pub_date_raw,
        pub_sort_date=article.pub_date or feed_date,
        entrez_date=feed_date,
        feed_date=feed_date,
        publication_types=list(article.publication_types),
        mesh_terms=list(article.mesh_terms),
        authors=list(article.authors),
        category=article.category,
        is_priority_study=classify.is_priority_study(
            article.publication_types, article.title, article.abstract
        ),
        is_rct=classify.is_rct(article.publication_types, article.title, article.abstract),
        summary_status=_initial_summary_status(article),
    )


def upsert_articles(articles: Sequence[FetchedArticle]) -> tuple[IngestStats, list[str]]:
    """Persist fetched articles as Papers, resolving each one's journal.

    Returns stats plus the pmids that are new-or-updated this call, so the
    caller can run specialty matching over exactly those papers rather than the
    whole table.
    """
    stats = IngestStats()
    if not articles:
        return stats, []

    # classify_article marks protocols/editorials/letters/etc. EXCLUDED; those
    # are pure noise and are never worth a row.
    articles = [a for a in articles if a.category != classify.EXCLUDED]
    if not articles:
        return stats, []

    existing_pmids = set(
        Paper.objects.filter(pmid__in=[a.pmid for a in articles]).values_list("pmid", flat=True)
    )

    to_upsert: list[Paper] = []
    for article in articles:
        journal = resolve_journal(article.journal, article.journal.best_name)
        if journal is None:
            stats.journals_unresolved += 1
            logger.warning(
                "unresolved journal for pmid %s: %r", article.pmid, article.journal.best_name
            )
        else:
            # Recorded here rather than recomputed by the caller: resolution is
            # several queries per article and doing it twice for the sake of a
            # log line doubles the cost of the whole ingest.
            stats.journal_hits[journal.id] = stats.journal_hits.get(journal.id, 0) + 1
        to_upsert.append(_paper_from_article(article, journal))

    Paper.objects.bulk_create(
        to_upsert,
        update_conflicts=True,
        unique_fields=["pmid"],
        update_fields=_UPSERT_UPDATE_FIELDS,
    )

    pmids = [a.pmid for a in articles]
    stats.papers_created = sum(1 for p in pmids if p not in existing_pmids)
    stats.papers_updated = sum(1 for p in pmids if p in existing_pmids)
    return stats, pmids


# ---------------------------------------------------------------- specialty linking


def link_specialties_for_papers(pmids: Sequence[str]) -> int:
    """Create PaperSpecialty rows for a batch of just-ingested papers.

    Specialty-journal papers get a journal_scope link for every active
    specialty that journal belongs to, unconditionally. General-journal papers
    are matched on title/abstract/MeSH against each active specialty's
    vocabulary — this is the Python-side replacement for the old
    query-time MeSH filter.
    """
    if not pmids:
        return 0

    papers = list(
        Paper.objects.filter(pmid__in=pmids)
        .select_related("journal")
        .prefetch_related("journal__specialty_links__specialty")
    )
    active_specialties = list(Specialty.objects.filter(is_active=True))
    rules_by_specialty_id = {s.id: s.to_rules() for s in active_specialties}

    links: list[PaperSpecialty] = []
    for paper in papers:
        if paper.journal is None:
            continue

        if not paper.journal.is_general:
            for link in paper.journal.specialty_links.all():
                if not link.specialty.is_active:
                    continue
                links.append(
                    PaperSpecialty(
                        paper=paper, specialty_id=link.specialty_id, relevance=JOURNAL_SCOPE
                    )
                )
            continue

        for specialty in active_specialties:
            result = match_specialty(
                rules=rules_by_specialty_id[specialty.id],
                mesh_terms=paper.mesh_terms,
                title=paper.title,
                abstract=paper.abstract,
            )
            if result.matched:
                links.append(
                    PaperSpecialty(
                        paper=paper,
                        specialty=specialty,
                        relevance=TOPICAL_MATCH,
                        matched_mesh=list(result.matched_mesh),
                        matched_keywords=list(result.matched_keywords),
                    )
                )

    if not links:
        return 0
    created = PaperSpecialty.objects.bulk_create(links, ignore_conflicts=True)
    return len(created)


def recheck_relevance(*, days: int) -> int:
    """Re-run topical matching over recent general-journal papers.

    MeSH headings land weeks to months after publication, so a paper's first
    pass at ingest can legitimately miss. This re-checks the last `days` of
    general-journal papers against every active specialty; a paper that gains
    its *first* specialty link here has its feed_date bumped to today so it
    surfaces rather than sitting buried under everything ingested since.
    """
    cutoff = timezone.now().date() - timedelta(days=days)
    candidates = list(
        Paper.objects.filter(
            journal__is_general=True, entrez_date__gte=cutoff, journal__isnull=False
        ).select_related("journal")
    )
    if not candidates:
        return 0

    active_specialties = list(Specialty.objects.filter(is_active=True))
    rules_by_specialty_id = {s.id: s.to_rules() for s in active_specialties}

    existing_links = set(
        PaperSpecialty.objects.filter(paper__in=candidates).values_list("paper_id", "specialty_id")
    )
    had_any_link = set(
        PaperSpecialty.objects.filter(paper__in=candidates)
        .values_list("paper_id", flat=True)
        .distinct()
    )

    links: list[PaperSpecialty] = []
    newly_visible_paper_ids: set[int] = set()
    for paper in candidates:
        for specialty in active_specialties:
            if (paper.id, specialty.id) in existing_links:
                continue
            result = match_specialty(
                rules=rules_by_specialty_id[specialty.id],
                mesh_terms=paper.mesh_terms,
                title=paper.title,
                abstract=paper.abstract,
            )
            if result.matched:
                links.append(
                    PaperSpecialty(
                        paper=paper,
                        specialty=specialty,
                        relevance=TOPICAL_MATCH,
                        matched_mesh=list(result.matched_mesh),
                        matched_keywords=list(result.matched_keywords),
                    )
                )
                if paper.id not in had_any_link:
                    newly_visible_paper_ids.add(paper.id)

    if not links:
        return 0

    with transaction.atomic():
        created = PaperSpecialty.objects.bulk_create(links, ignore_conflicts=True)
        if newly_visible_paper_ids:
            Paper.objects.filter(id__in=newly_visible_paper_ids).update(
                feed_date=timezone.now().date()
            )
    return len(created)


def backfill_specialty(specialty: Specialty) -> int:
    """Link every existing paper to a specialty, as if it had always existed.

    Run once, right after adding a new specialty (or materially changing its
    vocabulary): journal_scope for papers already in its preset journals,
    topical_match for everything else via the usual title/abstract/MeSH match.
    No refetch — this is the entire point of matching at ingest rather than in
    the PubMed query.
    """
    rules = specialty.to_rules()
    preset_journal_ids = set(
        SpecialtyJournal.objects.filter(specialty=specialty).values_list("journal_id", flat=True)
    )

    already_linked = set(
        PaperSpecialty.objects.filter(specialty=specialty).values_list("paper_id", flat=True)
    )

    links: list[PaperSpecialty] = []

    if preset_journal_ids:
        for paper in (
            Paper.objects.filter(journal_id__in=preset_journal_ids)
            .exclude(id__in=already_linked)
            .iterator()
        ):
            links.append(PaperSpecialty(paper=paper, specialty=specialty, relevance=JOURNAL_SCOPE))

    for paper in (
        Paper.objects.filter(journal__is_general=True)
        .exclude(id__in=already_linked)
        .select_related("journal")
        .iterator()
    ):
        result = match_specialty(
            rules=rules, mesh_terms=paper.mesh_terms, title=paper.title, abstract=paper.abstract
        )
        if result.matched:
            links.append(
                PaperSpecialty(
                    paper=paper,
                    specialty=specialty,
                    relevance=TOPICAL_MATCH,
                    matched_mesh=list(result.matched_mesh),
                    matched_keywords=list(result.matched_keywords),
                )
            )

    if not links:
        return 0
    created = PaperSpecialty.objects.bulk_create(links, ignore_conflicts=True)
    return len(created)


# ---------------------------------------------------------------- reclassification


@dataclass(slots=True)
class ReclassifyStats:
    examined: int = 0
    rct_added: int = 0
    rct_removed: int = 0
    priority_added: int = 0
    priority_removed: int = 0

    @property
    def changed(self) -> int:
        return self.rct_added + self.rct_removed + self.priority_added + self.priority_removed


def reclassify_papers(*, days: int, dry_run: bool = False) -> ReclassifyStats:
    """Recompute is_rct / is_priority_study over recently ingested papers.

    Classification flags are computed once at ingest and stored, so a paper
    keeps whatever the rules said on the night it arrived. When the rules are
    corrected — as they were for RCT title variants — existing rows stay wrong
    until something recomputes them. This is that something.

    Two deliberate omissions:

    * `category` is not recomputed. It decides whether a paper is in the feed
      at all, and papers classified EXCLUDED are dropped before a row is ever
      created — so recomputing it here could only ever *remove* papers people
      may already have saved. That is a bigger decision than a badge fix.
    * `feed_date` is not bumped, unlike recheck_relevance. A paper gaining a
      specialty link has genuinely just become relevant; a paper gaining an RCT
      badge was always an RCT and we were wrong. Correcting a badge must not
      republish months-old papers to the top of every feed.
    """
    cutoff = timezone.now().date() - timedelta(days=days)
    papers = list(Paper.objects.filter(entrez_date__gte=cutoff))

    stats = ReclassifyStats(examined=len(papers))
    changed: list[Paper] = []

    for paper in papers:
        new_rct = classify.is_rct(paper.publication_types, paper.title, paper.abstract)
        new_priority = classify.is_priority_study(
            paper.publication_types, paper.title, paper.abstract
        )
        if new_rct == paper.is_rct and new_priority == paper.is_priority_study:
            continue

        if new_rct != paper.is_rct:
            if new_rct:
                stats.rct_added += 1
            else:
                stats.rct_removed += 1
        if new_priority != paper.is_priority_study:
            if new_priority:
                stats.priority_added += 1
            else:
                stats.priority_removed += 1

        paper.is_rct = new_rct
        paper.is_priority_study = new_priority
        changed.append(paper)

    if changed and not dry_run:
        Paper.objects.bulk_update(changed, ["is_rct", "is_priority_study"], batch_size=500)

    return stats


# ---------------------------------------------------------------- summarisation


_CATEGORY_RANK = Case(
    When(category=Paper.Category.PRIORITY, then=0),
    When(category=Paper.Category.STANDARD, then=1),
    default=2,
    output_field=IntegerField(),
)


def select_papers_for_summary(limit: int) -> list[Paper]:
    """The summarisation worklist, Django-side equivalent of rank_summary_candidates.

    Ordering matches the engine exactly: priority study designs first, then
    other priority-category papers, then standard ones. A paper with no
    specialty link would never be visible in any feed, so it is excluded here
    rather than spending budget summarising something nobody can see.
    """
    has_specialty = PaperSpecialty.objects.filter(paper=OuterRef("pk"))
    qs = (
        Paper.objects.filter(
            summary_status__in=[Paper.SummaryStatus.PENDING, Paper.SummaryStatus.FAILED],
            summary_attempts__lt=MAX_SUMMARY_ATTEMPTS,
        )
        .exclude(abstract="")
        .filter(Exists(has_specialty))
        .annotate(_cat_rank=_CATEGORY_RANK)
        .order_by("-is_priority_study", "_cat_rank", "-feed_date")
    )
    return list(qs[:limit])


def _to_fetched_article(paper: Paper) -> FetchedArticle:
    """Reconstruct just enough of a FetchedArticle to build the summary prompt."""
    journal_name = paper.journal.display_name if paper.journal else paper.journal_name_raw
    return FetchedArticle(
        pmid=paper.pmid,
        title=paper.title,
        abstract=paper.abstract,
        journal=JournalIdentity(title=journal_name),
        pub_date_raw=paper.pub_date_raw,
        pub_date=paper.pub_date,
        entrez_date=paper.entrez_date,
        doi=paper.doi,
        publication_types=tuple(paper.publication_types),
        mesh_terms=tuple(paper.mesh_terms),
        authors=tuple(paper.authors),
        category=paper.category,
    )


def _primary_specialty_name(paper: Paper) -> str:
    link = (
        PaperSpecialty.objects.filter(paper=paper)
        .select_related("specialty")
        .order_by("specialty__sort_order", "specialty__name")
        .first()
    )
    return link.specialty.name if link else "clinical"


@dataclass(slots=True)
class SummaryStats:
    attempted: int = 0
    ok: int = 0
    failed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


def summarise_paper(paper: Paper, summariser: Summariser) -> bool:
    """Summarise one paper and persist the result. Returns whether it succeeded.

    Never raises: a summariser failure is recorded on the Paper row (attempts,
    error, status) rather than propagated, so one bad paper can't abort a batch.
    """
    from engine.errors import SummariserError

    article = _to_fetched_article(paper)
    specialty_name = _primary_specialty_name(paper)

    try:
        note = summariser.summarise(article, specialty_name)
    except SummariserError as exc:
        paper.summary_attempts += 1
        paper.summary_error = str(exc)[:2000]
        paper.summary_status = (
            Paper.SummaryStatus.SKIPPED
            if paper.summary_attempts >= MAX_SUMMARY_ATTEMPTS
            else Paper.SummaryStatus.FAILED
        )
        paper.save(update_fields=["summary_attempts", "summary_error", "summary_status"])
        return False

    PaperSummary.objects.update_or_create(
        paper=paper,
        defaults={
            "study_type": note.study_type,
            "context": note.context,
            "finding": note.finding,
            "so_what": note.so_what,
            "tags": list(note.tags),
            "model_name": note.model_name,
            "prompt_version": note.prompt_version,
            "input_tokens": note.input_tokens,
            "output_tokens": note.output_tokens,
        },
    )
    paper.summary_status = Paper.SummaryStatus.OK
    paper.summary_error = ""
    paper.save(update_fields=["summary_status", "summary_error"])
    return True


def summarise_papers(
    papers: Sequence[Paper], summariser: Summariser, *, max_workers: int = 4
) -> SummaryStats:
    """Summarise a batch of papers concurrently.

    Threaded rather than async: the OpenAI SDK call is blocking I/O and the
    per-paper work is a handful of short ORM writes, so a small thread pool is
    enough to keep the OpenAI round-trip from serialising the whole run.
    """
    from concurrent.futures import ThreadPoolExecutor

    from django.db import connections

    def _run(paper: Paper) -> bool:
        # Each worker thread opens its own connection (Django connections are
        # thread-local). Under Supabase's session pooler a held-open slot is a
        # scarce resource (~15 on the free tier, per CONN_MAX_AGE=0 elsewhere in
        # this codebase) — close it the moment this task is done rather than
        # leaving it idle for the rest of the pool's lifetime.
        try:
            return summarise_paper(paper, summariser)
        finally:
            connections.close_all()

    stats = SummaryStats(attempted=len(papers))
    if not papers:
        return stats

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = list(pool.map(_run, papers))

    stats.ok = sum(results)
    stats.failed = len(results) - stats.ok

    token_totals = PaperSummary.objects.filter(paper__in=papers).values_list(
        "input_tokens", "output_tokens"
    )
    for input_tokens, output_tokens in token_totals:
        stats.input_tokens += input_tokens
        stats.output_tokens += output_tokens
    return stats


# ---------------------------------------------------------------- windowing


def default_ingest_window(*, lookback_days: int | None = None) -> tuple[date, date]:
    """[date_from, date_to] for a nightly run.

    A multi-day lookback rather than "since the last run" is deliberate: a run
    that never happened (a crashed cron, a dead worker) self-heals on the next
    successful one instead of leaving a permanent gap. Re-ingesting already-seen
    papers is cheap and idempotent (see _UPSERT_UPDATE_FIELDS above).
    """
    days = lookback_days if lookback_days is not None else settings.INGEST_LOOKBACK_DAYS
    today = timezone.now().date()
    return today - timedelta(days=days), today


def journal_chunks(journals: Sequence[Journal]) -> list[list[Journal]]:
    """Split into ~40-journal batches for fault isolation (see engine.pubmed.queries)."""
    return chunked(list(journals))  # type: ignore[arg-type]
