"""Fill in each Journal's stable identifiers by asking PubMed.

Rather than querying the NLM Catalog, this searches PubMed for the journal's own
`[jour]` term and reads NlmUniqueID / MedlineTA / ISSNs off a real article. That
does two jobs at once: it resolves identity, and it *proves the search term
works*. A journal that returns zero articles has a bad `pubmed_name`, which is
exactly the failure that would otherwise be silent every night.

It also merges duplicates. cardiology-feed lists "Heart" and "Heart (British
Cardiac Society)" as two journals; they share one NlmUniqueID, so after this runs
the second collapses into the first as an alias.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from apps.catalog.models import Journal, JournalAlias, SpecialtyJournal
from engine.errors import EngineError
from engine.pubmed.client import PubMedClient
from engine.pubmed.models import JournalIdentity


class Command(BaseCommand):
    help = "Resolve journal identifiers from PubMed and merge duplicates."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--slug", action="append", help="Only these journals (repeatable).")
        parser.add_argument(
            "--force", action="store_true", help="Re-resolve journals that already have an NLM ID."
        )
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        journals = Journal.objects.all()
        if options["slug"]:
            journals = journals.filter(slug__in=options["slug"])
        elif not options["force"]:
            journals = journals.filter(nlm_uid="")

        journals = list(journals.order_by("slug"))
        if not journals:
            self.stdout.write("nothing to resolve")
            return

        client = PubMedClient(
            email=settings.PUBMED_EMAIL,
            api_key=settings.PUBMED_API_KEY,
            tool=settings.PUBMED_TOOL_NAME,
        )

        resolved = unresolved = merged = 0

        for journal in journals:
            try:
                identity = self._identify(client, journal.pubmed_name)
            except EngineError as exc:
                self.stderr.write(self.style.ERROR(f"  {journal.slug}: {exc}"))
                unresolved += 1
                continue

            if identity is None:
                self.stderr.write(
                    self.style.WARNING(
                        f"  {journal.slug}: no articles for {journal.pubmed_name!r} "
                        "— the [jour] term is probably wrong"
                    )
                )
                unresolved += 1
                continue

            if options["dry_run"]:
                self.stdout.write(
                    f"  {journal.slug}: nlm={identity.nlm_unique_id} "
                    f"ta={identity.medline_ta!r} title={identity.title!r}"
                )
                resolved += 1
                continue

            with transaction.atomic():
                duplicate = self._find_duplicate(journal, identity)
                if duplicate is not None:
                    self._merge(journal, duplicate)
                    self.stdout.write(
                        self.style.WARNING(
                            f"  {journal.slug} is the same journal as {duplicate.slug} "
                            f"(NLM {identity.nlm_unique_id}) — merged"
                        )
                    )
                    merged += 1
                    continue

                self._apply(journal, identity)
                resolved += 1
                self.stdout.write(
                    f"  {journal.slug} -> {identity.medline_ta} ({identity.nlm_unique_id})"
                )

        self.stdout.write("")
        self.stdout.write(f"  resolved   {resolved}")
        self.stdout.write(f"  merged     {merged}")
        self.stdout.write(f"  unresolved {unresolved}")
        if unresolved:
            self.stdout.write(
                self.style.WARNING("Unresolved journals will never match an article. Fix the name.")
            )

    def _identify(self, client: PubMedClient, pubmed_name: str) -> JournalIdentity | None:
        """Read a journal's identity off its most recent article."""
        result = client.esearch(f'"{pubmed_name}"[jour]')
        if result.count == 0:
            return None
        for article in client.efetch_from_history(result, batch_size=1):
            return article.journal
        return None

    def _find_duplicate(self, journal: Journal, identity: JournalIdentity) -> Journal | None:
        """Another Journal row that is the same real-world journal."""
        criteria = Q(pk__isnull=True)  # never matches; a seed for the OR chain
        if identity.nlm_unique_id:
            criteria |= Q(nlm_uid=identity.nlm_unique_id)
        if identity.issn_electronic:
            criteria |= Q(issn_electronic=identity.issn_electronic)
        if identity.issn_print:
            criteria |= Q(issn_print=identity.issn_print)
        return Journal.objects.filter(criteria).exclude(pk=journal.pk).first()

    def _apply(self, journal: Journal, identity: JournalIdentity) -> None:
        journal.nlm_uid = identity.nlm_unique_id
        journal.medline_ta = identity.medline_ta
        journal.issn_print = identity.issn_print
        journal.issn_electronic = identity.issn_electronic
        journal.save(
            update_fields=["nlm_uid", "medline_ta", "issn_print", "issn_electronic", "updated_at"]
        )
        self._add_aliases(journal, identity)

    def _add_aliases(self, journal: Journal, identity: JournalIdentity) -> None:
        """Record every name and identifier PubMed uses for this journal."""
        candidates = [
            (identity.title, JournalAlias.Kind.PUBMED_TITLE),
            (identity.medline_ta, JournalAlias.Kind.MEDLINE_TA),
            (identity.iso_abbreviation, JournalAlias.Kind.ISO_ABBREV),
            (identity.nlm_unique_id, JournalAlias.Kind.NLM_UID),
            (identity.issn_print, JournalAlias.Kind.ISSN),
            (identity.issn_electronic, JournalAlias.Kind.ISSN),
            (identity.issn_linking, JournalAlias.Kind.ISSN),
        ]
        for value, kind in candidates:
            if not value:
                continue
            normalized = JournalAlias.normalize(value, kind)
            if JournalAlias.objects.filter(value_normalized=normalized).exists():
                continue
            JournalAlias.objects.create(journal=journal, kind=kind, value=value)

    def _merge(self, duplicate: Journal, keeper: Journal) -> None:
        """Fold `duplicate` into `keeper`, preserving preset membership."""
        JournalAlias.objects.filter(journal=duplicate).update(journal=keeper)
        JournalAlias.objects.get_or_create(
            value_normalized=JournalAlias.normalize(duplicate.pubmed_name),
            defaults={
                "journal": keeper,
                "kind": JournalAlias.Kind.MANUAL,
                "value": duplicate.pubmed_name,
            },
        )
        for link in SpecialtyJournal.objects.filter(journal=duplicate):
            SpecialtyJournal.objects.get_or_create(
                specialty_id=link.specialty_id,
                journal=keeper,
                defaults={"is_default": link.is_default, "sort_order": link.sort_order},
            )
        SpecialtyJournal.objects.filter(journal=duplicate).delete()
        duplicate.delete()
