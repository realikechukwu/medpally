"""Normalize existing generated study-type labels after vocabulary changes."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.papers.models import PaperSummary
from engine.classify import normalize_study_type


class Command(BaseCommand):
    help = "Normalize PaperSummary.study_type values to the editorial vocabulary."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        changed = 0
        with transaction.atomic():
            for summary in PaperSummary.objects.iterator():
                normalized = normalize_study_type(summary.study_type)
                if normalized != summary.study_type:
                    summary.study_type = normalized
                    summary.save(update_fields=["study_type"])
                    changed += 1
            if options["dry_run"]:
                transaction.set_rollback(True)
        self.stdout.write(
            f"{changed} study types {'would be ' if options['dry_run'] else ''}normalized"
        )
