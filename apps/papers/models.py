"""Papers, their summaries, and their specialty relevance links."""

from __future__ import annotations

from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.db import models

from engine import classify
from engine.relevance import JOURNAL_SCOPE, TOPICAL_MATCH


class Paper(models.Model):
    """One PubMed record.

    There is no per-run "digest" any more: ingestion fills this shared pool and
    each user's feed is a query over it. What a user has already seen lives in
    feed.UserPaperState, replacing cardiology-feed's global sent_pmids.json.
    """

    class Category(models.TextChoices):
        PRIORITY = classify.PRIORITY, "Priority"
        PRIORITY_NO_ABSTRACT = classify.PRIORITY_NO_ABSTRACT, "Priority (no abstract)"
        STANDARD = classify.STANDARD, "Standard"
        LOW_PRIORITY = classify.LOW_PRIORITY, "Low priority"
        EXCLUDED = classify.EXCLUDED, "Excluded"

    class SummaryStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        OK = "ok", "OK"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    pmid = models.CharField(max_length=16, unique=True)
    doi = models.CharField(max_length=255, blank=True, default="")

    title = models.TextField()
    # Stored to feed the summariser. Deliberately NOT rendered in the feed: some
    # publishers assert copyright on abstracts, and the generated note is the
    # product. "Generated note plus a link out, never the abstract" is policy.
    abstract = models.TextField(blank=True, default="")

    journal = models.ForeignKey(
        "catalog.Journal",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="papers",
        help_text="Null when the journal could not be resolved; see the admin.",
    )
    journal_name_raw = models.CharField(max_length=300, blank=True, default="")

    # Display only. Can sit months away from entrez_date for online-ahead-of-print.
    pub_date = models.DateField(null=True, blank=True)
    pub_date_raw = models.CharField(max_length=40, blank=True, default="")
    # Always sortable: strict PubMed date parsing can leave pub_date blank.
    pub_sort_date = models.DateField()

    # When PubMed indexed the record. The honest "when did this appear" signal.
    entrez_date = models.DateField()

    # The canonical feed sort key. Starts equal to entrez_date, and is bumped
    # when a specialty link first appears — so a paper whose MeSH arrives late
    # surfaces at the top rather than buried sixty pages down.
    feed_date = models.DateField()

    publication_types = ArrayField(models.TextField(), default=list, blank=True)
    mesh_terms = ArrayField(models.TextField(), default=list, blank=True)
    authors = ArrayField(models.TextField(), default=list, blank=True)

    category = models.CharField(max_length=24, choices=Category.choices)
    # Denormalised from publication_types for feed ordering and the RCT badge.
    is_priority_study = models.BooleanField(default=False)
    is_rct = models.BooleanField(default=False)

    is_visible = models.BooleanField(default=True, help_text="Admin kill switch.")

    summary_status = models.CharField(
        max_length=10, choices=SummaryStatus.choices, default=SummaryStatus.PENDING
    )
    summary_attempts = models.SmallIntegerField(default=0)
    summary_error = models.TextField(blank=True, default="")

    specialties = models.ManyToManyField(
        "catalog.Specialty", through="papers.PaperSpecialty", related_name="papers"
    )

    ingested_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-feed_date", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=["doi"], condition=~models.Q(doi=""), name="paper_doi_unique"
            )
        ]
        indexes = [
            # Matches the feed's ORDER BY exactly, and is partial so it only
            # covers rows the feed can actually show.
            models.Index(
                fields=["journal", "-feed_date", "-is_priority_study", "-id"],
                condition=models.Q(is_visible=True, summary_status="ok"),
                name="paper_feed_idx",
            ),
            models.Index(fields=["-feed_date", "-id"], name="paper_feed_date_idx"),
            models.Index(
                fields=["-pub_sort_date", "-id"],
                condition=models.Q(is_visible=True, summary_status="ok"),
                name="paper_pub_sort_idx",
            ),
            models.Index(
                fields=["summary_status"],
                condition=models.Q(summary_status="pending"),
                name="paper_summary_pending_idx",
            ),
            GinIndex(fields=["mesh_terms"], name="paper_mesh_gin"),
            GinIndex(fields=["publication_types"], name="paper_pubtypes_gin"),
        ]

    def __str__(self) -> str:
        return f"{self.pmid}: {self.title[:60]}"

    def save(self, *args, **kwargs):
        if self.pub_sort_date is None:
            self.pub_sort_date = self.pub_date or self.entrez_date
        super().save(*args, **kwargs)

    @property
    def url(self) -> str:
        return f"https://pubmed.ncbi.nlm.nih.gov/{self.pmid}/"

    @property
    def doi_url(self) -> str:
        return f"https://doi.org/{self.doi}" if self.doi else ""


class PaperSummary(models.Model):
    """The generated editorial note. One per paper, computed once.

    A separate table rather than fields on Paper so that when the prompt changes
    we know which rows were written by which version and can regenerate
    selectively. summary_status stays on Paper so the worklist query never joins.
    """

    paper = models.OneToOneField(
        Paper, on_delete=models.CASCADE, primary_key=True, related_name="summary"
    )

    study_type = models.CharField(max_length=60)
    context = models.TextField()
    finding = models.TextField()
    so_what = models.TextField()
    tags = ArrayField(models.TextField(), default=list, blank=True)

    model_name = models.CharField(max_length=60)
    prompt_version = models.CharField(max_length=40)
    input_tokens = models.IntegerField(default=0)
    output_tokens = models.IntegerField(default=0)
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "paper summaries"
        indexes = [
            GinIndex(fields=["tags"], name="summary_tags_gin"),
            models.Index(fields=["prompt_version"], name="summary_prompt_version_idx"),
        ]

    def __str__(self) -> str:
        return f"summary of {self.paper_id}"


class PaperSpecialty(models.Model):
    """Why a paper belongs to a specialty.

    Written at ingest rather than filtered in the PubMed query, so that adding a
    specialty later backfills over papers already stored, matching rules can be
    tuned and re-run, and papers whose MeSH arrives late can be picked up by
    recheck_relevance.
    """

    class Relevance(models.TextChoices):
        JOURNAL_SCOPE = JOURNAL_SCOPE, "In a specialty journal"
        TOPICAL_MATCH = TOPICAL_MATCH, "Topically matched"

    paper = models.ForeignKey(Paper, on_delete=models.CASCADE, related_name="specialty_links")
    specialty = models.ForeignKey(
        "catalog.Specialty", on_delete=models.CASCADE, related_name="paper_links"
    )
    relevance = models.CharField(max_length=20, choices=Relevance.choices)
    matched_mesh = ArrayField(models.TextField(), default=list, blank=True)
    matched_keywords = ArrayField(models.TextField(), default=list, blank=True)
    linked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "paper specialties"
        constraints = [
            models.UniqueConstraint(fields=["paper", "specialty"], name="paper_specialty_unique")
        ]
        indexes = [models.Index(fields=["specialty", "paper"], name="paper_specialty_lookup_idx")]

    def __str__(self) -> str:
        return f"{self.paper_id}/{self.specialty_id} ({self.relevance})"
