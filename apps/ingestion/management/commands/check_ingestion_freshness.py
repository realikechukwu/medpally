"""Alarm for the failure that is most likely and least visible: a dead cron.

    manage.py check_ingestion_freshness --max-age-hours 36

Exits non-zero when no ingestion run has succeeded recently. Nothing else in
the system notices a scheduler that has quietly stopped firing — the site stays
up, the feed just slowly goes stale, and the first report comes from a user
weeks later. Run it on its own schedule, separate from ingestion itself, so it
still fires when ingestion is the thing that is broken.

The default 36 hours is deliberately looser than the 24-hour cadence: one
missed night is absorbed by the next run's 3-day lookback and is not worth
waking anyone for. Two in a row is a real outage.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.ingestion.models import IngestionRun


class Command(BaseCommand):
    help = "Exit non-zero if no ingestion run has succeeded within the window."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--max-age-hours", type=int, default=36)

    def handle(self, *args: Any, **options: Any) -> None:
        max_age_hours: int = options["max_age_hours"]
        cutoff = timezone.now() - timezone.timedelta(hours=max_age_hours)

        latest = (
            IngestionRun.objects.filter(
                status=IngestionRun.Status.SUCCESS, finished_at__isnull=False
            )
            .order_by("-finished_at")
            .first()
        )

        if latest is None:
            self.stderr.write(self.style.ERROR("STALE: no successful ingestion run has ever run"))
            raise SystemExit(1)

        age_hours = (timezone.now() - latest.finished_at).total_seconds() / 3600
        if latest.finished_at < cutoff:
            self.stderr.write(
                self.style.ERROR(
                    f"STALE: last successful ingestion finished {age_hours:.1f}h ago "
                    f"({latest.finished_at:%Y-%m-%d %H:%M} UTC), limit is {max_age_hours}h"
                )
            )
            raise SystemExit(1)

        # A run that "succeeded" without touching a single journal is a
        # different kind of dead: the scheduler fired, the query returned
        # nothing, and the feed still goes stale.
        if latest.journals_queried == 0:
            self.stderr.write(
                self.style.ERROR(
                    f"STALE: last run {age_hours:.1f}h ago queried zero journals — "
                    "check the catalog is seeded and journals are active"
                )
            )
            raise SystemExit(1)

        self.stdout.write(
            self.style.SUCCESS(
                f"fresh: last successful ingestion {age_hours:.1f}h ago, "
                f"{latest.journals_queried} journals, {latest.papers_created} new papers"
            )
        )
