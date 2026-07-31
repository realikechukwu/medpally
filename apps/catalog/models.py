"""Journals and specialties.

The central modelling decision: a Journal is the primitive, and a Specialty is a
*preset* that preselects journals. Users add and remove individual journals, so
adding a new specialty is rows in a table, not a code change.
"""

from __future__ import annotations

from django.contrib.postgres.fields import ArrayField
from django.core.validators import RegexValidator
from django.db import models

from engine.pubmed.queries import normalize_journal_name
from engine.relevance import SpecialtyRules

HEX_COLOR = RegexValidator(r"^#[0-9a-fA-F]{6}$", "Must be a hex colour like #1f2937")


class Specialty(models.Model):
    """A clinical specialty: a journal preset plus a relevance vocabulary."""

    slug = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, default="")

    # Relevance vocabulary. Used to decide whether a paper from a *general*
    # journal (NEJM, Lancet) belongs in this specialty's feed. Keywords may end
    # in "*" to match a word stem: "cardi*" covers cardiac, cardiovascular,
    # cardiotoxic and cardiomyopathy in one entry.
    mesh_terms = ArrayField(models.TextField(), default=list, blank=True)
    title_keywords = ArrayField(models.TextField(), default=list, blank=True)
    abstract_keywords = ArrayField(models.TextField(), default=list, blank=True)

    journals = models.ManyToManyField(
        "catalog.Journal", through="catalog.SpecialtyJournal", related_name="specialties"
    )

    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "specialties"
        ordering = ("sort_order", "name")

    def __str__(self) -> str:
        return self.name

    def to_rules(self) -> SpecialtyRules:
        """Adapt to the engine's plain dataclass. The engine knows no Django."""
        return SpecialtyRules(
            slug=self.slug,
            mesh_terms=tuple(self.mesh_terms),
            title_keywords=tuple(self.title_keywords),
            abstract_keywords=tuple(self.abstract_keywords),
        )


class Journal(models.Model):
    """One journal.

    Identity lives in nlm_uid and the ISSNs, never in the title: PubMed returns
    "European heart journal", "JAMA cardiology", "BMJ open" and "Lancet (London,
    England)" for journals whose configured names look nothing like that.
    """

    class CoverStyle(models.TextChoices):
        SOLID = "solid", "Solid"
        SPLIT = "split", "Split"
        RULE = "rule", "Rule"

    slug = models.SlugField(max_length=100, unique=True)

    # Exactly what goes inside "…"[jour] in a PubMed query.
    pubmed_name = models.CharField(max_length=300, unique=True)
    display_name = models.CharField(max_length=200)
    # The cover wordmark: "NEJM", "JACC", "EHJ".
    short_name = models.CharField(max_length=24)

    nlm_uid = models.CharField(max_length=20, blank=True, default="")
    medline_ta = models.CharField(max_length=120, blank=True, default="")
    issn_print = models.CharField(max_length=9, blank=True, default="")
    issn_electronic = models.CharField(max_length=9, blank=True, default="")

    # General medical journals carry every specialty, so their papers are
    # filtered by the reader's specialty vocabulary. Specialty journals are not.
    is_general = models.BooleanField(
        default=False,
        help_text="General medical journal — its papers are filtered by specialty relevance.",
    )
    # Editorially maintained, deliberately not an imported impact factor.
    prestige_weight = models.SmallIntegerField(default=2)

    # Deliberately OUR palette, not the journal's brand colours. Covers use our
    # typeface and a plain abbreviation; nominative reference is defensible,
    # recreating a trademarked lockup is not.
    cover_color = models.CharField(max_length=7, default="#1f2937", validators=[HEX_COLOR])
    cover_color_accent = models.CharField(
        max_length=7, blank=True, default="", validators=[HEX_COLOR]
    )
    cover_style = models.CharField(
        max_length=10, choices=CoverStyle.choices, default=CoverStyle.SOLID
    )

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("display_name",)
        constraints = [
            # Conditional so that many journals may share the "not yet resolved"
            # empty string while a real identifier stays unique.
            models.UniqueConstraint(
                fields=["nlm_uid"], condition=~models.Q(nlm_uid=""), name="journal_nlm_uid_unique"
            ),
            models.UniqueConstraint(
                fields=["issn_electronic"],
                condition=~models.Q(issn_electronic=""),
                name="journal_issn_e_unique",
            ),
            models.UniqueConstraint(
                fields=["issn_print"],
                condition=~models.Q(issn_print=""),
                name="journal_issn_p_unique",
            ),
        ]
        indexes = [models.Index(fields=["is_general", "display_name"])]

    def __str__(self) -> str:
        return self.display_name

    @property
    def is_resolved(self) -> bool:
        """Whether this journal has a stable identifier to match articles on."""
        return bool(self.nlm_uid or self.issn_electronic or self.issn_print)


class JournalAlias(models.Model):
    """A name or identifier that resolves to a Journal.

    Not optional. cardiology-feed's configs list "Heart" and "Heart (British
    Cardiac Society)" as separate journals, and "JACC: Cardiovascular Imaging"
    alongside "JACC Cardiovascular Imaging" — the same journals twice. Without
    this table those become duplicate rows, split feeds and unknown-journal cards.
    """

    class Kind(models.TextChoices):
        PUBMED_TITLE = "pubmed_title", "PubMed title"
        MEDLINE_TA = "medline_ta", "MEDLINE abbreviation"
        ISO_ABBREV = "iso_abbrev", "ISO abbreviation"
        ISSN = "issn", "ISSN"
        NLM_UID = "nlm_uid", "NLM unique ID"
        MANUAL = "manual", "Manually mapped"

    journal = models.ForeignKey(Journal, on_delete=models.CASCADE, related_name="aliases")
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.MANUAL)
    value = models.CharField(max_length=300)
    value_normalized = models.CharField(max_length=300, editable=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "journal aliases"
        constraints = [
            models.UniqueConstraint(fields=["value_normalized"], name="journal_alias_norm_unique")
        ]

    def __str__(self) -> str:
        return f"{self.value} -> {self.journal_id}"

    def save(self, *args, **kwargs):
        self.value_normalized = self.normalize(self.value, self.kind)
        super().save(*args, **kwargs)

    @staticmethod
    def normalize(value: str, kind: str = "") -> str:
        """Identifiers are compared verbatim; names go through the title rules."""
        if kind in {JournalAlias.Kind.ISSN, JournalAlias.Kind.NLM_UID}:
            return value.strip().lower()
        return normalize_journal_name(value)


class SpecialtyJournal(models.Model):
    """Membership of a journal in a specialty's preset."""

    specialty = models.ForeignKey(Specialty, on_delete=models.CASCADE, related_name="journal_links")
    journal = models.ForeignKey(Journal, on_delete=models.CASCADE, related_name="specialty_links")
    is_default = models.BooleanField(
        default=True, help_text="Preselected when a user picks this specialty at onboarding."
    )
    sort_order = models.IntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["specialty", "journal"], name="specialty_journal_unique"
            )
        ]
        ordering = ("sort_order", "journal__display_name")

    def __str__(self) -> str:
        return f"{self.specialty_id}/{self.journal_id}"
