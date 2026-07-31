"""The feed query, keyset pagination, and per-user state lookup.

Everything here is written to the query-count budget in the plan: the feed
page itself is one query, seen-state is one lookup plus (at most) one insert,
and that's the whole page regardless of how many cards are on it.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from django.conf import settings
from django.db.models import Exists, OuterRef, Q, QuerySet, Subquery

from apps.accounts.models import User, UserJournalSubscription
from apps.papers.models import Paper, PaperSpecialty

from .filters import FeedFilters
from .models import FeaturedPaper, UserPaperState


def feed_queryset(user: User) -> QuerySet[Paper]:
    """Every paper this user's subscriptions and specialty entitle them to see.

    Exists() rather than a join across the specialty M2M: an OR'd join fans out
    one row per matching specialty link, which would need .distinct() to
    collapse — and .distinct() defeats the index-ordered scan this query is
    built to use (paper_feed_idx) and breaks keyset pagination outright.
    """
    profile = user.profile
    subscribed_journal_ids = UserJournalSubscription.objects.filter(
        user=user, is_active=True
    ).values("journal_id")

    qs = Paper.objects.filter(
        is_visible=True,
        summary_status=Paper.SummaryStatus.OK,
        journal_id__in=Subquery(subscribed_journal_ids),
    ).select_related("journal", "summary")

    if profile.specialty_id:
        topical = PaperSpecialty.objects.filter(
            paper=OuterRef("pk"), specialty_id=profile.specialty_id
        )
        qs = qs.filter(Q(journal__is_general=False) | Exists(topical))

    dismissed = UserPaperState.objects.filter(
        user=user, paper=OuterRef("pk"), dismissed_at__isnull=False
    )
    qs = qs.exclude(Exists(dismissed))

    if profile.specialty_id:
        featured = FeaturedPaper.objects.filter(paper=OuterRef("pk"), specialty_id=profile.specialty_id)
        qs = qs.annotate(is_featured=Exists(featured))
    return qs


def exclude_seen(qs: QuerySet[Paper], user: User) -> QuerySet[Paper]:
    """Hide papers the reader has opened."""
    seen = UserPaperState.objects.filter(
        user=user, paper=OuterRef("pk"), opened_at__isnull=False
    )
    return qs.exclude(Exists(seen))


# ---------------------------------------------------------------- keyset cursor


@dataclass(frozen=True, slots=True)
class SortSpec:
    key: str
    order_by: tuple[str, ...]
    fields: tuple[str, ...]
    parsers: tuple[Callable[[Any], Any], ...]


FEED_SORT = SortSpec("feed", ("-feed_date", "-is_priority_study", "-id"), ("feed_date", "is_priority_study", "id"), (date.fromisoformat, bool, int))
PUB_SORT = SortSpec("pub", ("-pub_sort_date", "-id"), ("pub_sort_date", "id"), (date.fromisoformat, int))


def get_sort_spec(key: str) -> SortSpec:
    return PUB_SORT if key == "pub" else FEED_SORT


def encode_cursor(paper: Paper, sort: SortSpec = FEED_SORT) -> str:
    values = []
    for field in sort.fields:
        value = getattr(paper, field)
        values.append(value.isoformat() if isinstance(value, date) else value)
    payload = [sort.key, *values]
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def decode_cursor(raw: str, sort: SortSpec = FEED_SORT) -> tuple[Any, ...] | None:
    """Decode a cursor, or None if it isn't one.

    Cursors arrive in a query string, so they arrive tampered with, truncated
    by a mail client, or pasted half-way. None of that is a server error — the
    caller falls back to the first page.
    """
    try:
        payload = json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
        if not isinstance(payload, list) or len(payload) != len(sort.fields) + 1 or payload[0] != sort.key:
            return None
        return tuple(parser(value) for parser, value in zip(sort.parsers, payload[1:], strict=True))
    except (ValueError, TypeError, binascii.Error):
        return None


def apply_cursor(qs: QuerySet[Paper], cursor: str | None, sort: SortSpec = FEED_SORT) -> QuerySet[Paper]:
    """Expand the cursor into the three-clause form so paper_feed_idx still applies.

    Plain OFFSET pagination duplicates and skips cards at page boundaries once
    nightly ingestion is inserting rows between requests; keyset pagination
    doesn't have that problem because "the next page" is defined relative to
    the last row seen, not a row count.
    """
    if not cursor:
        return qs
    values = decode_cursor(cursor, sort)
    if values is None:
        return qs
    clause = Q()
    for i, field in enumerate(sort.fields):
        equal = {sort.fields[j]: values[j] for j in range(i)}
        clause |= Q(**equal, **{f"{field}__lt": values[i]})
    return qs.filter(clause)


@dataclass(slots=True)
class FeedPage:
    papers: list[Paper]
    next_cursor: str | None


def get_feed_page(
    user: User,
    *,
    filters: FeedFilters | None = None,
    cursor: str | None = None,
    unseen_only: bool = False,
    page_size: int | None = None,
) -> FeedPage:
    page_size = page_size or settings.FEED_PAGE_SIZE
    filters = filters or FeedFilters(tab="unseen" if unseen_only else "all", cursor=cursor or "")
    sort = get_sort_spec(filters.sort)
    qs = feed_queryset(user)
    if filters.tab == "unseen":
        qs = exclude_seen(qs, user)
    if filters.design:
        qs = qs.filter(summary__study_type__in=filters.study_types())
    if filters.tab == "featured":
        if not user.profile.specialty_id:
            return FeedPage(papers=[], next_cursor=None)
        qs = qs.filter(is_featured=True)
    qs = apply_cursor(qs.order_by(*sort.order_by), filters.cursor or cursor, sort)

    papers = list(qs[: page_size + 1])
    has_next = len(papers) > page_size
    papers = papers[:page_size]
    next_cursor = encode_cursor(papers[-1], sort) if has_next and papers else None
    return FeedPage(papers=papers, next_cursor=next_cursor)


# ---------------------------------------------------------------- read later
#
# Read Later sorts by when you saved it, not by feed_date, so it needs its own
# cursor. Same keyset reasoning as the feed: saves arrive while you're reading.


@dataclass(slots=True)
class SavedPage:
    states: list[UserPaperState]
    next_cursor: str | None


def _encode_saved_cursor(state: UserPaperState) -> str:
    payload = [state.saved_at.isoformat(), state.paper_id]
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def _decode_saved_cursor(raw: str) -> tuple[datetime, int] | None:
    try:
        saved_at_str, paper_id = json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
        return datetime.fromisoformat(saved_at_str), int(paper_id)
    except (ValueError, TypeError, binascii.Error):
        return None


def get_saved_page(
    user: User, *, cursor: str | None = None, page_size: int | None = None
) -> SavedPage:
    page_size = page_size or settings.FEED_PAGE_SIZE
    qs = (
        UserPaperState.objects.filter(user=user, saved_at__isnull=False)
        .select_related("paper", "paper__journal", "paper__summary")
        .order_by("-saved_at", "-paper_id")
    )

    decoded = _decode_saved_cursor(cursor) if cursor else None
    if decoded is not None:
        saved_at, paper_id = decoded
        qs = qs.filter(Q(saved_at__lt=saved_at) | Q(saved_at=saved_at, paper_id__lt=paper_id))

    states = list(qs[: page_size + 1])
    has_next = len(states) > page_size
    states = states[:page_size]
    next_cursor = _encode_saved_cursor(states[-1]) if has_next and states else None
    return SavedPage(states=states, next_cursor=next_cursor)


# ---------------------------------------------------------------- seen state


def attach_state(user: User, papers: list[Paper]) -> dict[int, UserPaperState]:
    """Return existing state for the cards, without treating rendering as reading."""
    if not papers:
        return {}

    paper_ids = [p.id for p in papers]
    existing = {
        s.paper_id: s for s in UserPaperState.objects.filter(user=user, paper_id__in=paper_ids)
    }

    return existing
