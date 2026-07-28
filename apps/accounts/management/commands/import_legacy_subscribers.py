"""Load the cardiology-feed newsletter list from a CSV export.

    manage.py import_legacy_subscribers subscribers.csv --dry-run

These rows exist only to *prefill* onboarding for someone who signs up with a
matching address. No account is ever created from them: there is no password
and no consent for that, and creating one would silently desync the old repo's
sending list.

CSV rather than the Sheets API on purpose — gspread and google-auth stay out of
the dependency tree, and a one-off export is less machinery than a service
account for a list that is read a handful of times before cutover.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import LegacySubscriber
from apps.catalog.models import Specialty

# Accepted column spellings, lowercased. The exported Sheet has been through a
# few hands, so the header row is not something to rely on being stable.
EMAIL_COLUMNS = ("email", "email address", "e-mail", "address")
NAME_COLUMNS = ("first_name", "first name", "name", "firstname")
SPECIALTY_COLUMNS = ("specialty", "speciality", "specialty_slug", "topic")


class Command(BaseCommand):
    help = "Import newsletter subscribers from a CSV export into LegacySubscriber."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("path", help="Path to the CSV export.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        path = Path(options["path"])
        if not path.exists():
            raise CommandError(f"no such file: {path}")

        rows = self._read(path)
        if not rows:
            raise CommandError(f"{path} has no usable rows — is there an email column?")

        known_slugs = set(Specialty.objects.values_list("slug", flat=True))
        created = updated = skipped = 0
        unknown_specialties: set[str] = set()

        with transaction.atomic():
            for email, first_name, specialty_slug in rows:
                if specialty_slug and specialty_slug not in known_slugs:
                    unknown_specialties.add(specialty_slug)
                    specialty_slug = ""

                existing = LegacySubscriber.objects.filter(email__iexact=email).first()
                if existing is None:
                    if not options["dry_run"]:
                        LegacySubscriber.objects.create(
                            email=email, first_name=first_name, specialty_slug=specialty_slug
                        )
                    created += 1
                    continue

                # Never overwrite a claimed row: that subscriber has already
                # signed up and edited their own profile.
                if existing.claimed_by_id is not None:
                    skipped += 1
                    continue

                changed = []
                if first_name and existing.first_name != first_name:
                    existing.first_name = first_name
                    changed.append("first_name")
                if specialty_slug and existing.specialty_slug != specialty_slug:
                    existing.specialty_slug = specialty_slug
                    changed.append("specialty_slug")
                if changed:
                    if not options["dry_run"]:
                        existing.save(update_fields=changed)
                    updated += 1
                else:
                    skipped += 1

            if options["dry_run"]:
                transaction.set_rollback(True)

        prefix = "would create" if options["dry_run"] else "created"
        self.stdout.write(
            f"{len(rows)} rows: {prefix} {created}, updated {updated}, unchanged/claimed {skipped}"
        )
        if unknown_specialties:
            self.stdout.write(
                self.style.WARNING(
                    "unrecognised specialty values ignored: "
                    + ", ".join(sorted(unknown_specialties))
                )
            )
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("dry run — nothing was written"))

    def _read(self, path: Path) -> list[tuple[str, str, str]]:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                return []

            lookup = {(name or "").strip().lower(): name for name in reader.fieldnames}
            email_col = self._pick(lookup, EMAIL_COLUMNS)
            if email_col is None:
                return []
            name_col = self._pick(lookup, NAME_COLUMNS)
            specialty_col = self._pick(lookup, SPECIALTY_COLUMNS)

            seen: set[str] = set()
            rows: list[tuple[str, str, str]] = []
            for raw in reader:
                email = (raw.get(email_col) or "").strip().lower()
                if not email or "@" not in email or email in seen:
                    continue
                seen.add(email)
                rows.append(
                    (
                        email,
                        (raw.get(name_col) or "").strip() if name_col else "",
                        (raw.get(specialty_col) or "").strip().lower() if specialty_col else "",
                    )
                )
            return rows

    @staticmethod
    def _pick(lookup: dict[str, str], candidates: tuple[str, ...]) -> str | None:
        for candidate in candidates:
            if candidate in lookup:
                return lookup[candidate]
        return None
