"""ORM integration for the weekly featured selection."""

from __future__ import annotations

from datetime import date, timedelta

from django.db import transaction
from django.db.models import Count

from apps.catalog.models import Specialty
from apps.papers.models import Paper, PaperSpecialty
from engine.featured import FeaturedCandidate, rank_featured

from .models import FeaturedPaper, UserPaperState


def gather_candidates(specialty: Specialty, week_start: date) -> list[FeaturedCandidate]:
    """Gather the eligible week; separate aggregates avoid a reverse-FK fanout."""
    start = week_start - timedelta(days=7)
    paper_ids = PaperSpecialty.objects.filter(specialty=specialty).values("paper_id")
    excluded = FeaturedPaper.objects.filter(
        specialty=specialty, week_start__gte=week_start - timedelta(weeks=8)
    ).values("paper_id")
    papers = list(
        Paper.objects.filter(
            id__in=paper_ids,
            feed_date__gte=start,
            feed_date__lt=week_start,
            is_visible=True,
            summary_status=Paper.SummaryStatus.OK,
        )
        .exclude(id__in=excluded)
        .select_related("journal", "summary")
    )
    ids = [paper.id for paper in papers]
    likes = {
        row["paper_id"]: row["n"]
        for row in UserPaperState.objects.filter(paper_id__in=ids, liked_at__isnull=False)
        .values("paper_id")
        .annotate(n=Count("id"))
    }
    saves = {
        row["paper_id"]: row["n"]
        for row in UserPaperState.objects.filter(paper_id__in=ids, saved_at__isnull=False)
        .values("paper_id")
        .annotate(n=Count("id"))
    }
    return [
        FeaturedCandidate(
            paper_id=paper.id,
            study_type=paper.summary.study_type,
            journal_prestige=paper.journal.prestige_weight,
            likes=likes.get(paper.id, 0),
            saves=saves.get(paper.id, 0),
            feed_date=paper.feed_date,
        )
        for paper in papers
    ]


def select_featured_for_week(specialty: Specialty, week_start: date, *, dry_run: bool = False):
    selected = rank_featured(gather_candidates(specialty, week_start))
    if dry_run:
        return selected
    with transaction.atomic():
        FeaturedPaper.objects.filter(specialty=specialty, week_start=week_start).delete()
        FeaturedPaper.objects.bulk_create(
            [
                FeaturedPaper(
                    specialty=specialty,
                    paper_id=item.candidate.paper_id,
                    week_start=week_start,
                    rank=index,
                    score=item.score,
                )
                for index, item in enumerate(selected, start=1)
            ]
        )
    return selected
