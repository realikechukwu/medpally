"""Load specialties and journals from apps/catalog/fixtures/catalog.yaml.

Idempotent: re-running updates in place and never duplicates. Journals removed
from the YAML are left alone rather than deleted — a Paper may point at one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.catalog.models import Journal, JournalAlias, Specialty, SpecialtyJournal

DEFAULT_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "catalog.yaml"

SPECIALTY_FIELDS = (
    "name",
    "description",
    "sort_order",
    "mesh_terms",
    "title_keywords",
    "abstract_keywords",
)
JOURNAL_FIELDS = (
    "pubmed_name",
    "display_name",
    "short_name",
    "is_general",
    "is_active",
    "cover_color",
    "cover_color_accent",
    "cover_style",
)


class Command(BaseCommand):
    help = "Seed or update specialties and journals from a YAML fixture."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--file", default=str(DEFAULT_FIXTURE))
        parser.add_argument(
            "--dry-run", action="store_true", help="Report what would change, then roll back."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        path = Path(options["file"])
        if not path.exists():
            raise CommandError(f"fixture not found: {path}")

        data = yaml.safe_load(path.read_text())
        specialties = data.get("specialties") or []
        journals = data.get("journals") or []

        try:
            with transaction.atomic():
                stats = self._seed(specialties, journals)
                if options["dry_run"]:
                    self.stdout.write(self.style.WARNING("dry run — rolling back"))
                    raise _Rollback
        except _Rollback:
            pass

        for label, value in stats.items():
            self.stdout.write(f"  {label:26} {value}")
        self.stdout.write(self.style.SUCCESS("seed_catalog complete"))

    def _seed(
        self, specialties: list[dict[str, Any]], journals: list[dict[str, Any]]
    ) -> dict[str, int]:
        stats = dict.fromkeys(
            (
                "specialties created",
                "specialties updated",
                "journals created",
                "journals updated",
                "aliases created",
                "preset links created",
            ),
            0,
        )

        by_slug: dict[str, Specialty] = {}
        for entry in specialties:
            defaults = {f: entry.get(f) for f in SPECIALTY_FIELDS if entry.get(f) is not None}
            obj, created = Specialty.objects.update_or_create(slug=entry["slug"], defaults=defaults)
            by_slug[entry["slug"]] = obj
            stats["specialties created" if created else "specialties updated"] += 1

        for entry in journals:
            defaults = {f: entry.get(f) for f in JOURNAL_FIELDS if entry.get(f) is not None}
            journal, created = Journal.objects.update_or_create(
                slug=entry["slug"], defaults=defaults
            )
            stats["journals created" if created else "journals updated"] += 1

            # The journal's own name is an alias too, so article resolution can
            # match on it without a special case.
            alias_values = {
                (entry["pubmed_name"], JournalAlias.Kind.PUBMED_TITLE),
                (entry["display_name"], JournalAlias.Kind.PUBMED_TITLE),
            }
            alias_values |= {(a, JournalAlias.Kind.MANUAL) for a in entry.get("aliases") or []}

            for value, kind in alias_values:
                normalized = JournalAlias.normalize(value, kind)
                if JournalAlias.objects.filter(value_normalized=normalized).exists():
                    continue
                JournalAlias.objects.create(journal=journal, kind=kind, value=value)
                stats["aliases created"] += 1

            for order, slug in enumerate(entry.get("specialties") or []):
                specialty = by_slug.get(slug)
                if specialty is None:
                    self.stderr.write(
                        self.style.WARNING(f"  unknown specialty {slug!r} on {entry['slug']}")
                    )
                    continue
                _, link_created = SpecialtyJournal.objects.update_or_create(
                    specialty=specialty,
                    journal=journal,
                    defaults={"is_default": True, "sort_order": order},
                )
                if link_created:
                    stats["preset links created"] += 1

        return stats


class _Rollback(Exception):
    """Internal: unwinds the transaction for --dry-run."""
