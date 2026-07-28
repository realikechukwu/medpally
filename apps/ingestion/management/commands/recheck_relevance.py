"""Re-run topical matching over recent general-journal papers.

    manage.py recheck_relevance --days 90

MeSH headings arrive weeks to months after publication, so a paper's first
pass at ingest can legitimately miss a specialty it belongs to. Intended to run
nightly alongside ingest_papers: cheap (bounded by RELEVANCE_RECHECK_DAYS), and
it's how a late-indexed paper "arrives" instead of being permanently invisible.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.ingestion import services


class Command(BaseCommand):
    help = "Re-run specialty matching over recent general-journal papers."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--days", type=int, default=None)

    def handle(self, *args: Any, **options: Any) -> None:
        days = options["days"] or settings.RELEVANCE_RECHECK_DAYS
        created = services.recheck_relevance(days=days)
        self.stdout.write(self.style.SUCCESS(f"{created} new PaperSpecialty links (last {days}d)"))
