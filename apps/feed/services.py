"""The feed query, keyset pagination, and seen-state bookkeeping.

Everything here is written to the query-count budget in the plan: the feed
page itself is one query, seen-state is one lookup plus (at most) one insert,
and that's the whole page regardless of how many cards are on it.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import date

from django.conf import settings
from django.db.models import Exists, OuterRef, Q, QuerySet, Subquery
from django.utils import timezone

from apps.accounts.models import User, UserJournalSubscription
from apps.papers.models import Paper, PaperSpecialty

from .models import UserPaperState


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

    return qs.order_by("-feed_date", "-is_priority_study", "-id")


def exclude_seen(qs: QuerySet[Paper], user: User) -> QuerySet[Paper]:
    """The ?unseen=1 toggle: hide anything with a recorded impression."""
    seen = UserPaperState.objects.filter(
        user=user, paper=OuterRef("pk"), first_seen_at__isnull=False
    )
    return qs.exclude(Exists(seen))


# ---------------------------------------------------------------- keyset cursor


@dataclass(frozen=True, slots=True)
class Cursor:
    feed_date: date
    is_priority_study: bool
    id: int


def encode_cursor(paper: Paper) -> str:
    payload = [paper.feed_date.isoformat(), paper.is_priority_study, paper.id]
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def decode_cursor(raw: str) -> Cursor:
    feed_date_str, is_priority_study, paper_id = json.loads(
        base64.urlsafe_b64decode(raw.encode()).decode()
    )
    return Cursor(date.fromisoformat(feed_date_str), bool(is_priority_study), int(paper_id))


def apply_cursor(qs: QuerySet[Paper], cursor: str | None) -> QuerySet[Paper]:
    """Expand the cursor into the three-clause form so paper_feed_idx still applies.

    Plain OFFSET pagination duplicates and skips cards at page boundaries once
    nightly ingestion is inserting rows between requests; keyset pagination
    doesn't have that problem because "the next page" is defined relative to
    the last row seen, not a row count.
    """
    if not cursor:
        return qs
    c = decode_cursor(cursor)
    return qs.filter(
        Q(feed_date__lt=c.feed_date)
        | Q(feed_date=c.feed_date, is_priority_study__lt=c.is_priority_study)
        | Q(feed_date=c.feed_date, is_priority_study=c.is_priority_study, id__lt=c.id)
    )


@dataclass(slots=True)
class FeedPage:
    papers: list[Paper]
    next_cursor: str | None


def get_feed_page(
    user: User,
    *,
    cursor: str | None = None,
    unseen_only: bool = False,
    page_size: int | None = None,
) -> FeedPage:
    page_size = page_size or settings.FEED_PAGE_SIZE
    qs = feed_queryset(user)
    if unseen_only:
        qs = exclude_seen(qs, user)
    qs = apply_cursor(qs, cursor)

    papers = list(qs[: page_size + 1])
    has_next = len(papers) > page_size
    papers = papers[:page_size]
    next_cursor = encode_cursor(papers[-1]) if has_next and papers else None
    return FeedPage(papers=papers, next_cursor=next_cursor)


# ---------------------------------------------------------------- seen state


def attach_state_and_record_impressions(
    user: User, papers: list[Paper]
) -> dict[int, UserPaperState]:
    """One lookup, one insert, regardless of page size.

    Returns the state that existed *before* this call — the caller uses that to
    tell a genuinely new impression from a page the user has already scrolled
    past, without a second read after the insert.
    """
    if not papers:
        return {}

    paper_ids = [p.id for p in papers]
    existing = {
        s.paper_id: s for s in UserPaperState.objects.filter(user=user, paper_id__in=paper_ids)
    }

    now = timezone.now()
    to_create = [
        UserPaperState(user=user, paper_id=pid, first_seen_at=now)
        for pid in paper_ids
        if pid not in existing
    ]
    if to_create:
        UserPaperState.objects.bulk_create(to_create, ignore_conflicts=True)

    return existing
