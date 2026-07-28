"""Link a specialty to every paper already in the database.

    manage.py backfill_specialty cardiology

Run once after adding a new specialty (or materially editing its vocabulary in
seed_catalog): journal_scope links for its preset journals, topical_match for
everything else via the normal matching rules. No refetch — this is the entire
point of matching at ingest rather than in the PubMed query itself.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.catalog.models import Specialty
from apps.ingestion import services


class Command(BaseCommand):
    help = "Backfill PaperSpecialty links for one specialty over all existing papers."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("slug")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            specialty = Specialty.objects.get(slug=options["slug"])
        except Specialty.DoesNotExist as exc:
            raise CommandError(f"no specialty with slug {options['slug']!r}") from exc

        created = services.backfill_specialty(specialty)
        self.stdout.write(self.style.SUCCESS(f"{created} new PaperSpecialty links for {specialty}"))
