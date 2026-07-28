"""Summarise pending (or previously-failed) papers on demand.

    manage.py summarise_papers --limit 5

ingest_papers already runs this as its last step; this command exists so
summarisation can be re-run, budgeted, or dry-run independently — e.g. after
raising SUMMARY_MAX_PER_RUN, or to clear a backlog left by a run that used
--no-summaries.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.ingestion import services
from engine.summarise.client import FakeSummariser, OpenAISummariser


class Command(BaseCommand):
    help = "Summarise pending/failed papers, best-ranked first."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--fake", action="store_true", help="Use the deterministic stub.")

    def handle(self, *args: Any, **options: Any) -> None:
        limit = options["limit"] or settings.SUMMARY_MAX_PER_RUN
        papers = services.select_papers_for_summary(limit)
        if not papers:
            self.stdout.write("nothing to summarise")
            return

        summariser: Any
        if options["fake"]:
            summariser = FakeSummariser()
        else:
            if not settings.OPENAI_API_KEY:
                self.stderr.write("OPENAI_API_KEY is not set (or pass --fake)")
                return
            summariser = OpenAISummariser(
                api_key=settings.OPENAI_API_KEY, model=settings.OPENAI_MODEL
            )

        stats = services.summarise_papers(papers, summariser)
        for paper in papers:
            paper.refresh_from_db(fields=["summary_status"])
            marker = (
                self.style.SUCCESS("ok")
                if paper.summary_status == "ok"
                else self.style.ERROR(paper.summary_status)
            )
            self.stdout.write(f"  {paper.pmid}  {marker}  {paper.title[:70]}")

        self.stdout.write(
            f"\n{stats.ok}/{stats.attempted} summarised "
            f"({stats.input_tokens}+{stats.output_tokens} tokens)"
        )
