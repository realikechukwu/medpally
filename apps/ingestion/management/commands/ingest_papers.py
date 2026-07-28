"""Nightly (or on-demand) fetch of every subscribed/preset journal.

    manage.py ingest_papers --since-days 7 --no-summaries

Wrapped in a session advisory lock so an overlapping cron run skips rather than
duplicating work, and always leaves an IngestionRun row behind — a crashed run
is visible as a stale "running" row rather than silence.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.catalog.models import Journal
from apps.ingestion import services
from apps.ingestion.models import IngestionRun, JournalFetchLog
from engine.pubmed.client import PubMedClient
from engine.summarise.client import FakeSummariser, OpenAISummariser


class Command(BaseCommand):
    help = "Fetch new articles for every subscribed/preset journal and store them as Papers."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--since-days", type=int, default=None)
        parser.add_argument("--date-from", help="YYYY-MM-DD, overrides --since-days")
        parser.add_argument("--date-to", help="YYYY-MM-DD, default today")
        parser.add_argument(
            "--no-summaries", action="store_true", help="Ingest and link only; skip summarisation."
        )
        parser.add_argument("--summary-limit", type=int, default=None)
        parser.add_argument(
            "--fake-summariser", action="store_true", help="Use the deterministic stub summariser."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        with services.ingest_lock() as acquired:
            if not acquired:
                self.stdout.write(self.style.WARNING("another ingest_papers run holds the lock"))
                IngestionRun.objects.create(status=IngestionRun.Status.SKIPPED)
                return
            self._run(options)

    def _run(self, options: dict[str, Any]) -> None:
        date_from, date_to = self._window(options)
        run = IngestionRun.objects.create(
            command="ingest_papers", date_from=date_from, date_to=date_to
        )

        try:
            journals = list(services.active_journal_queryset())
            run.journals_queried = len(journals)
            self.stdout.write(f"{len(journals)} journals in scope, window {date_from}..{date_to}")

            client = PubMedClient(
                email=settings.PUBMED_EMAIL,
                api_key=settings.PUBMED_API_KEY,
                tool=settings.PUBMED_TOOL_NAME,
            )

            all_pmids: list[str] = []
            for chunk in services.journal_chunks(journals):
                articles = list(
                    client.fetch_journal_window([j.pubmed_name for j in chunk], date_from, date_to)
                )
                run.articles_fetched += len(articles)

                stats, pmids = services.upsert_articles(articles)
                run.papers_created += stats.papers_created
                run.papers_updated += stats.papers_updated
                run.journals_unresolved += stats.journals_unresolved
                all_pmids.extend(pmids)

                self._log_journal_hits(run, chunk, articles)

            run.specialty_links_created = services.link_specialties_for_papers(all_pmids)
            self.stdout.write(
                f"  fetched {run.articles_fetched}, created {run.papers_created}, "
                f"updated {run.papers_updated}, unresolved {run.journals_unresolved}, "
                f"specialty links {run.specialty_links_created}"
            )

            if not options["no_summaries"]:
                self._summarise(run, options)

            run.status = IngestionRun.Status.SUCCESS
        except Exception as exc:
            run.status = IngestionRun.Status.FAILED
            run.error = str(exc)[:4000]
            run.finished_at = timezone.now()
            run.save()
            raise
        else:
            run.finished_at = timezone.now()
            run.save()

    def _window(self, options: dict[str, Any]) -> tuple[date, date]:
        if options.get("date_from"):
            date_from = date.fromisoformat(options["date_from"])
            date_to = (
                date.fromisoformat(options["date_to"])
                if options.get("date_to")
                else timezone.now().date()
            )
            return date_from, date_to
        return services.default_ingest_window(lookback_days=options.get("since_days"))

    def _log_journal_hits(
        self, run: IngestionRun, chunk: list[Journal], articles: list[Any]
    ) -> None:
        """One row per journal in the chunk, including zero hits.

        A journal returning zero for several nights running is almost always a
        bad [jour] term, not a quiet week — and that only becomes visible if
        the zero itself is recorded.
        """
        counts = dict.fromkeys((j.id for j in chunk), 0)
        for article in articles:
            journal = services.resolve_journal(article.journal, article.journal.best_name)
            if journal is not None and journal.id in counts:
                counts[journal.id] += 1

        JournalFetchLog.objects.bulk_create(
            [
                JournalFetchLog(run=run, journal_id=journal_id, articles_found=count)
                for journal_id, count in counts.items()
            ]
        )

    def _summarise(self, run: IngestionRun, options: dict[str, Any]) -> None:
        limit = options.get("summary_limit") or settings.SUMMARY_MAX_PER_RUN
        papers = services.select_papers_for_summary(limit)
        if not papers:
            return

        if options["fake_summariser"]:
            summariser: Any = FakeSummariser()
        else:
            summariser = OpenAISummariser(
                api_key=settings.OPENAI_API_KEY, model=settings.OPENAI_MODEL
            )

        stats = services.summarise_papers(papers, summariser)
        run.summaries_attempted = stats.attempted
        run.summaries_ok = stats.ok
        run.summaries_failed = stats.failed
        run.input_tokens = stats.input_tokens
        run.output_tokens = stats.output_tokens
        self.stdout.write(
            f"  summarised {stats.ok}/{stats.attempted} "
            f"({stats.input_tokens}+{stats.output_tokens} tokens)"
        )
