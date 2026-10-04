"""Papers a reader brings in from outside their feed: a shared or pasted link.

find_shared_paper turns a share into a preview and writes nothing — a share
sheet opens a plain GET, and nothing should land in Saved until the reader
taps Save. save_shared_paper then stores the paper (fetching it from PubMed if
MedPally has never seen it), saves it for the reader, marks it External when
it is not part of their own feed, and starts its summary.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import date

from django.conf import settings
from django.db import connections, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.ingestion import services as ingestion
from apps.papers.models import Paper
from engine.errors import EngineError
from engine.http import RATE_WITH_KEY, RATE_WITHOUT_KEY, HttpClient
from engine.pubmed.client import PubMedClient
from engine.pubmed.lookup import Reference, find_article, parse_reference
from engine.summarise.client import OpenAISummariser

from . import services
from .models import UserPaperState

logger = logging.getLogger(__name__)

# A reader is waiting on the page, so give up quickly rather than retrying
# the way the nightly ingest does.
LOOKUP_TIMEOUT_SECONDS = 8.0
LOOKUP_ATTEMPTS = 2


class PubMedUnavailable(Exception):
    """PubMed could not be reached, as opposed to having no matching record."""


class PaperNotFound(Exception):
    """No PubMed record, or one an admin has hidden."""


@dataclass(frozen=True, slots=True)
class SharedPaper:
    """What the add screen shows before the reader confirms."""

    pmid: str
    title: str
    journal_name: str
    published: date | None
    in_medpally: bool
    in_feed: bool
    already_saved: bool


@dataclass(frozen=True, slots=True)
class SavedShare:
    paper: Paper
    is_external: bool
    already_saved: bool


def pubmed_client() -> PubMedClient:
    api_key = settings.PUBMED_API_KEY
    http = HttpClient(
        rate_per_second=RATE_WITH_KEY if api_key else RATE_WITHOUT_KEY,
        user_agent=f"{settings.PUBMED_TOOL_NAME}/1.0 (+mailto:{settings.PUBMED_EMAIL})",
        timeout=LOOKUP_TIMEOUT_SECONDS,
        max_attempts=LOOKUP_ATTEMPTS,
    )
    return PubMedClient(
        email=settings.PUBMED_EMAIL, api_key=api_key, tool=settings.PUBMED_TOOL_NAME, http=http
    )


def find_shared_paper(user: User, *, text: str, title: str = "") -> SharedPaper | None:
    """The paper a share points at, or None when nothing on PubMed matches."""
    reference = parse_reference(text, title)
    if reference.is_empty:
        return None

    paper = _known_paper(reference)
    if paper is None:
        try:
            article = find_article(reference, pubmed_client())
        except EngineError as exc:
            raise PubMedUnavailable(str(exc)) from exc
        if article is None:
            return None
        paper = Paper.objects.filter(pmid=article.pmid).first()
        if paper is None:
            return SharedPaper(
                pmid=article.pmid,
                title=article.title,
                journal_name=article.journal.best_name,
                published=article.pub_date or article.entrez_date,
                in_medpally=False,
                in_feed=False,
                already_saved=False,
            )

    if not paper.is_visible:
        return None
    state = UserPaperState.objects.filter(user=user, paper=paper).first()
    return SharedPaper(
        pmid=paper.pmid,
        title=paper.title,
        journal_name=paper.journal.display_name if paper.journal else paper.journal_name_raw,
        published=paper.pub_date or paper.pub_sort_date,
        in_medpally=True,
        in_feed=_in_feed(user, paper),
        already_saved=bool(state and state.saved_at),
    )


def save_shared_paper(user: User, pmid: str) -> SavedShare:
    """Save a shared paper for this reader, storing it first if it is new."""
    paper = Paper.objects.filter(pmid=pmid).first()
    if paper is None:
        try:
            article = next(iter(pubmed_client().efetch_by_pmid([pmid])), None)
        except EngineError as exc:
            raise PubMedUnavailable(str(exc)) from exc
        if article is None:
            raise PaperNotFound(pmid)
        paper = ingestion.store_shared_article(article)
    if not paper.is_visible:
        raise PaperNotFound(pmid)

    now = timezone.now()
    with transaction.atomic():
        state, _ = UserPaperState.objects.select_for_update().get_or_create(user=user, paper=paper)
        already_saved = state.saved_at is not None
        # Bringing a paper in on purpose overrides an old "not interested",
        # and that has to happen before asking whether it is in their feed.
        if state.dismissed_at is not None:
            state.dismissed_at = None
            state.save(update_fields=["dismissed_at", "updated_at"])
        is_external = not _in_feed(user, paper)
        state.saved_at = state.saved_at or now
        if is_external:
            state.external_at = state.external_at or now
        state.save(update_fields=["saved_at", "external_at", "updated_at"])

    # Not only papers this share stored: one ingestion kept earlier with no
    # specialty match has no feed to summarise it for, so it waits on this too.
    if state.external_at is not None and paper.summary_status in {
        Paper.SummaryStatus.PENDING,
        Paper.SummaryStatus.FAILED,
    }:
        transaction.on_commit(lambda: summarise_soon(paper.pk))
    return SavedShare(paper=paper, is_external=is_external, already_saved=already_saved)


def reader_added(user: User, paper: Paper) -> bool:
    """Whether this reader shared the paper in, which lets them open it unsummarised."""
    if not user.is_authenticated:
        return False
    return UserPaperState.objects.filter(user=user, paper=paper, external_at__isnull=False).exists()


def summarise_soon(paper_id: int) -> None:
    """Write the note now rather than at the next nightly run.

    On a thread so the Save tap returns at once. If the worker is recycled
    mid-call the paper simply stays pending, and the nightly summarise run
    picks it up (select_papers_for_summary includes external papers).
    """
    if not settings.OPENAI_API_KEY:
        return
    threading.Thread(
        target=_summarise_now, args=(paper_id,), name=f"summarise-{paper_id}", daemon=True
    ).start()


def _summarise_now(paper_id: int) -> None:
    try:
        paper = Paper.objects.filter(
            pk=paper_id,
            summary_status__in=[Paper.SummaryStatus.PENDING, Paper.SummaryStatus.FAILED],
        ).first()
        if paper is not None:
            summariser = OpenAISummariser(
                api_key=settings.OPENAI_API_KEY, model=settings.OPENAI_MODEL
            )
            ingestion.summarise_paper(paper, summariser)
    except Exception:
        logger.exception("summary for shared paper %s failed", paper_id)
    finally:
        # Connections are per thread; this one's must not outlive it.
        connections.close_all()


def _known_paper(reference: Reference) -> Paper | None:
    """Skip PubMed entirely when the share names a paper MedPally already has."""
    if reference.pmid:
        return Paper.objects.filter(pmid=reference.pmid).first()
    if reference.doi:
        return Paper.objects.filter(doi__iexact=reference.doi).first()
    return None


def _in_feed(user: User, paper: Paper) -> bool:
    return services.feed_queryset(user).filter(pk=paper.pk).exists()
