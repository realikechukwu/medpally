"""The feed query, keyset pagination, and per-user state lookup.

Everything here is written to the query-count budget in the plan: the feed
page itself is one query, the week-count aggregate behind the group headings
is a second, seen-state is one lookup plus (at most) one insert, and that's
the whole page regardless of how many cards — or how many weeks — are on it.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from django.conf import settings
from django.db.models import Count, DateField, Exists, OuterRef, Q, QuerySet, Subquery
from django.db.models.functions import TruncWeek

from apps.accounts.models import User, UserJournalSubscription
from apps.papers.models import Paper, PaperSpecialty
from engine.featured import week_start_for

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
        featured = FeaturedPaper.objects.filter(
            paper=OuterRef("pk"), specialty_id=profile.specialty_id
        )
        qs = qs.annotate(is_featured=Exists(featured))
    return qs


def exclude_seen(qs: QuerySet[Paper], user: User) -> QuerySet[Paper]:
    """Hide papers the reader has opened."""
    seen = UserPaperState.objects.filter(user=user, paper=OuterRef("pk"), opened_at__isnull=False)
    return qs.exclude(Exists(seen))


def filtered_queryset(user: User, filters: FeedFilters) -> QuerySet[Paper]:
    """The feed narrowed to one tab, design and week — before any ordering.

    Cards and week counts have to agree about what is in the feed, or a header
    promises twenty papers and the week opens on twelve. Sharing this one
    definition is what keeps them honest; it is not merely deduplication.

    A featured tab with no specialty yields none() rather than an early return,
    so callers get a queryset either way — and none() costs no query.
    """
    qs = feed_queryset(user)
    if filters.tab == "unseen":
        qs = exclude_seen(qs, user)
    if filters.design:
        qs = qs.filter(summary__study_type__in=filters.study_types())
    if filters.tab == "featured":
        if not user.profile.specialty_id:
            return qs.none()
        qs = qs.filter(is_featured=True)
    return qs


# ---------------------------------------------------------------- keyset cursor


@dataclass(frozen=True, slots=True)
class SortSpec:
    key: str
    order_by: tuple[str, ...]
    fields: tuple[str, ...]
    parsers: tuple[Callable[[Any], Any], ...]


FEED_SORT = SortSpec(
    "feed",
    ("-feed_date", "-is_priority_study", "-id"),
    ("feed_date", "is_priority_study", "id"),
    (date.fromisoformat, bool, int),
)
PUB_SORT = SortSpec(
    "pub", ("-pub_sort_date", "-id"), ("pub_sort_date", "id"), (date.fromisoformat, int)
)


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
        if (
            not isinstance(payload, list)
            or len(payload) != len(sort.fields) + 1
            or payload[0] != sort.key
        ):
            return None
        return tuple(parser(value) for parser, value in zip(sort.parsers, payload[1:], strict=True))
    except (ValueError, TypeError, binascii.Error):
        return None


def apply_cursor(
    qs: QuerySet[Paper], cursor: str | None, sort: SortSpec = FEED_SORT
) -> QuerySet[Paper]:
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
    # The same Paper objects regrouped, not a copy. attach_state() still wants
    # the flat list, and the template wants them in weeks.
    weeks: list[WeekGroup]


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
    date_field = sort.fields[0]
    qs = filtered_queryset(user, filters)

    # The cursor already carries the sort date of the last paper on the previous
    # page, so the week that page ended in comes free — no second cursor, and a
    # tampered cursor falls back here and in apply_cursor together, which is what
    # keeps a junk cursor showing page one *with* its heading.
    raw_cursor = filters.cursor or cursor
    decoded = decode_cursor(raw_cursor, sort) if raw_cursor else None
    previous_week = week_start_for(decoded[0]) if decoded else None

    papers = list(apply_cursor(qs.order_by(*sort.order_by), raw_cursor, sort)[: page_size + 1])
    has_next = len(papers) > page_size
    papers = papers[:page_size]
    next_cursor = encode_cursor(papers[-1], sort) if has_next and papers else None

    totals = (
        week_counts(
            qs,
            date_field=date_field,
            oldest=getattr(papers[-1], date_field),
            newest=getattr(papers[0], date_field),
        )
        if papers
        else {}
    )
    weeks = group_by_week(papers, date_field=date_field, totals=totals, previous_week=previous_week)
    return FeedPage(papers=papers, next_cursor=next_cursor, weeks=weeks)


# ---------------------------------------------------------------- week grouping
#
# Months of daily papers make one long river with no landmarks. Weeks are the
# unit this product already runs on — featured selection is weekly and
# week_start_for() is the same Monday — so the feed borrows it and hands the
# reader a heading per week with the week's whole total beside it.
#
# The grouping is derived from the papers already on the page rather than from
# the calendar. A calendar rule ("this week and last week") looks equivalent and
# is not: ingestion is nightly, so early Monday the current week is empty; a
# narrow design filter empties most weeks; and every Featured paper is stamped a
# week behind its selection run (apps/feed/featured.py gathers the *preceding*
# Mon-Sun). Anchoring on the data is right in all of those cases and, unlike the
# clock, can be tested with fixed dates.

EXPANDED_WEEKS = 2


@dataclass(slots=True)
class WeekGroup:
    week_start: date
    total: int
    papers: list[Paper]
    show_header: bool
    is_open: bool


def week_counts(
    qs: QuerySet[Paper], *, date_field: str, oldest: date, newest: date
) -> dict[date, int]:
    """Whole-week totals for the weeks this page touches. One query, always.

    Bounded to the page's own span, so it reads one to three weeks rather than
    aggregating a reader's entire history.

    Pass the queryset *before* the feed's ordering is applied. The compiler
    folds every non-alias ORDER BY expression into the GROUP BY, so an aggregate
    carrying ("-feed_date", "-is_priority_study", "-id") groups by the paper
    itself — GROUP BY 1, id — and reports a total of 1 for every week, silently
    and with no error to notice. Ordering on the `week` alias here displaces
    that if a caller ever does hand over an ordered queryset; both together are
    what make this safe, and the test names the hazard.
    """
    rows = (
        qs.filter(
            **{
                f"{date_field}__gte": week_start_for(oldest),
                f"{date_field}__lt": week_start_for(newest) + timedelta(days=7),
            }
        )
        .annotate(week=TruncWeek(date_field, output_field=DateField()))
        .values("week")
        .annotate(n=Count("id"))
        .order_by("-week")
    )
    return {row["week"]: row["n"] for row in rows}


def group_by_week(
    papers: list[Paper],
    *,
    date_field: str,
    totals: dict[date, int],
    previous_week: date | None,
) -> list[WeekGroup]:
    """Bucket one page of papers into weeks, in a single pass.

    The papers arrive ordered by `field` descending and the bucket key is that
    same field, so the buckets come out in order for either sort and a week can
    never reappear once it has been closed.

    `previous_week` is the week the last page ended in. When this page opens in
    that same week the header is suppressed — otherwise a week split across a
    page boundary would announce itself twice.
    """
    groups: list[WeekGroup] = []
    for paper in papers:
        start = week_start_for(getattr(paper, date_field))
        if not groups or groups[-1].week_start != start:
            groups.append(
                WeekGroup(
                    week_start=start,
                    total=0,
                    papers=[],
                    # Only the page's *first* group can be a continuation; every
                    # later one begins here and so announces itself.
                    show_header=bool(groups) or start != previous_week,
                    is_open=previous_week is None and len(groups) < EXPANDED_WEEKS,
                )
            )
        groups[-1].papers.append(paper)

    for group in groups:
        # A header must never claim fewer papers than it is showing, so the
        # count on the page is the floor if the aggregate was skipped.
        group.total = max(totals.get(group.week_start, 0), len(group.papers))
    return groups


# ---------------------------------------------------------------- saved / liked collections
#
# These collections sort by the moment the reader acted, not by feed_date, so
# they need their own cursors. Same keyset reasoning as the feed: saves and
# likes arrive while you're reading.


@dataclass(slots=True)
class CollectionPage:
    states: list[UserPaperState]
    next_cursor: str | None


def _encode_collection_cursor(state: UserPaperState, timestamp_field: str) -> str:
    payload = [getattr(state, timestamp_field).isoformat(), state.paper_id]
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def _decode_collection_cursor(raw: str) -> tuple[datetime, int] | None:
    try:
        saved_at_str, paper_id = json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
        return datetime.fromisoformat(saved_at_str), int(paper_id)
    except (ValueError, TypeError, binascii.Error):
        return None


def _get_collection_page(
    user: User,
    *,
    timestamp_field: str,
    cursor: str | None = None,
    page_size: int | None = None,
) -> CollectionPage:
    if timestamp_field not in {"saved_at", "liked_at"}:
        raise ValueError(f"Unsupported paper collection: {timestamp_field}")

    page_size = page_size or settings.FEED_PAGE_SIZE
    qs = (
        UserPaperState.objects.filter(user=user, **{f"{timestamp_field}__isnull": False})
        .select_related("paper", "paper__journal", "paper__summary")
        .order_by(f"-{timestamp_field}", "-paper_id")
    )

    decoded = _decode_collection_cursor(cursor) if cursor else None
    if decoded is not None:
        acted_at, paper_id = decoded
        qs = qs.filter(
            Q(**{f"{timestamp_field}__lt": acted_at})
            | Q(**{timestamp_field: acted_at, "paper_id__lt": paper_id})
        )

    states = list(qs[: page_size + 1])
    has_next = len(states) > page_size
    states = states[:page_size]
    next_cursor = (
        _encode_collection_cursor(states[-1], timestamp_field) if has_next and states else None
    )
    return CollectionPage(states=states, next_cursor=next_cursor)


def get_saved_page(
    user: User, *, cursor: str | None = None, page_size: int | None = None
) -> CollectionPage:
    return _get_collection_page(
        user, timestamp_field="saved_at", cursor=cursor, page_size=page_size
    )


def get_liked_page(
    user: User, *, cursor: str | None = None, page_size: int | None = None
) -> CollectionPage:
    return _get_collection_page(
        user, timestamp_field="liked_at", cursor=cursor, page_size=page_size
    )


def get_recent_searches(user: User, *, limit: int = 6) -> list[UserPaperState]:
    """Papers the reader most recently selected from search results."""
    return list(
        UserPaperState.objects.filter(
            user=user,
            searched_at__isnull=False,
            paper__is_visible=True,
            paper__summary_status=Paper.SummaryStatus.OK,
        )
        .select_related("paper", "paper__journal")
        .order_by("-searched_at", "-paper_id")[:limit]
    )


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
