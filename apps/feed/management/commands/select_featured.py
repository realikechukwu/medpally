from __future__ import annotations

from datetime import date
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.catalog.models import Specialty
from apps.feed.featured import select_featured_for_week
from engine.featured import week_start_for


class Command(BaseCommand):
    help = "Select each specialty's transparent weekly featured papers."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--week", help="Any ISO date in the target week")
        parser.add_argument("--specialty")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--explain", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            day = date.fromisoformat(options["week"]) if options["week"] else timezone.localdate()
        except ValueError as error:
            raise CommandError("--week must be an ISO date") from error
        week_start = week_start_for(day)
        specialties = Specialty.objects.filter(is_active=True)
        if options["specialty"]:
            specialties = specialties.filter(slug=options["specialty"])
            if not specialties.exists():
                raise CommandError("unknown specialty")
        for specialty in specialties:
            selected = select_featured_for_week(specialty, week_start, dry_run=options["dry_run"])
            self.stdout.write(f"{specialty.slug}: {len(selected)} selected")
            if options["explain"]:
                for item in selected:
                    self.stdout.write(
                        f"  #{item.candidate.paper_id} {item.score:g} {dict(item.components)}"
                    )
