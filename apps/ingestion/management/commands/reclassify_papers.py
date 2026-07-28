"""Recompute stored classification flags after a rules change.

    manage.py reclassify_papers --days 90 --dry-run

Classification is computed once at ingest and stored, so existing rows keep
whatever the rules said on the night they arrived. When the rules are corrected
this is what brings the back-catalogue into line.

Unlike recheck_relevance this is NOT a nightly job — re-running it every night
would recompute the same answer indefinitely. Run it once after changing
engine/classify.py, with --dry-run first.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from apps.ingestion import services


class Command(BaseCommand):
    help = "Recompute is_rct / is_priority_study over recently ingested papers."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--days",
            type=int,
            default=90,
            help="How far back to recompute, by entrez_date (default: 90).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        days: int = options["days"]
        dry_run: bool = options["dry_run"]

        stats = services.reclassify_papers(days=days, dry_run=dry_run)

        verb = "would change" if dry_run else "changed"
        self.stdout.write(
            f"examined {stats.examined} papers from the last {days}d; {verb} {stats.changed}"
        )
        self.stdout.write(
            f"  is_rct            +{stats.rct_added} / -{stats.rct_removed}\n"
            f"  is_priority_study +{stats.priority_added} / -{stats.priority_removed}"
        )
        if dry_run:
            self.stdout.write(self.style.WARNING("dry run — nothing was written"))
        elif stats.changed:
            self.stdout.write(self.style.SUCCESS("done"))
