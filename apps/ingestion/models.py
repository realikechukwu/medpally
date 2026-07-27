"""Ingestion run bookkeeping.

A silently-dead cron is the most likely production failure here and the least
likely to be noticed, so every run leaves a row behind whether it succeeds or not.
"""

from __future__ import annotations

from django.db import models


class IngestionRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped (lock held)"

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RUNNING)
    command = models.CharField(max_length=60, default="ingest_papers")

    date_from = models.DateField(null=True, blank=True)
    date_to = models.DateField(null=True, blank=True)

    journals_queried = models.IntegerField(default=0)
    articles_fetched = models.IntegerField(default=0)
    papers_created = models.IntegerField(default=0)
    papers_updated = models.IntegerField(default=0)
    specialty_links_created = models.IntegerField(default=0)
    journals_unresolved = models.IntegerField(default=0)

    summaries_attempted = models.IntegerField(default=0)
    summaries_ok = models.IntegerField(default=0)
    summaries_failed = models.IntegerField(default=0)
    input_tokens = models.IntegerField(default=0)
    output_tokens = models.IntegerField(default=0)

    error = models.TextField(blank=True, default="")

    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-started_at",)
        indexes = [models.Index(fields=["-started_at", "status"], name="ingestion_run_recent_idx")]

    def __str__(self) -> str:
        return f"{self.command} {self.started_at:%Y-%m-%d %H:%M} ({self.status})"

    @property
    def duration_seconds(self) -> float | None:
        if not self.finished_at:
            return None
        return (self.finished_at - self.started_at).total_seconds()


class JournalFetchLog(models.Model):
    """Per-journal hit counts for one run.

    A journal returning zero for several nights running is almost always a bad
    [jour] term rather than a quiet week, and that should be visible rather than
    silent.
    """

    run = models.ForeignKey(IngestionRun, on_delete=models.CASCADE, related_name="journal_logs")
    journal = models.ForeignKey(
        "catalog.Journal", on_delete=models.CASCADE, related_name="fetch_logs"
    )
    articles_found = models.IntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["run", "journal"], name="journal_fetch_log_unique")
        ]
        indexes = [models.Index(fields=["journal", "-id"], name="journal_fetch_log_recent_idx")]

    def __str__(self) -> str:
        return f"{self.journal_id}: {self.articles_found}"
