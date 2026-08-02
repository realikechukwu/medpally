"""Per-user paper state and per-specialty editorial annotations."""

from __future__ import annotations

from django.conf import settings
from django.db import models


class UserPaperState(models.Model):
    """One row per (user, paper).

    Nullable timestamps rather than booleans: the "when" comes free, Read Later
    gets a natural sort order, and the partial indexes stay tiny. One row per
    pair rather than an event log because the feed needs exactly one lookup per
    card.

    This replaces cardiology-feed's global state/sent_pmids.json. "Has this been
    sent" was a property of the digest; "has this user seen it" is a property of
    the user.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="paper_states"
    )
    paper = models.ForeignKey("papers.Paper", on_delete=models.CASCADE, related_name="user_states")

    first_seen_at = models.DateTimeField(null=True, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    saved_at = models.DateTimeField(null=True, blank=True)
    liked_at = models.DateTimeField(null=True, blank=True)
    searched_at = models.DateTimeField(null=True, blank=True)
    dismissed_at = models.DateTimeField(null=True, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "paper"], name="user_paper_state_unique")
        ]
        indexes = [
            models.Index(
                fields=["user", "-saved_at"],
                condition=models.Q(saved_at__isnull=False),
                name="user_paper_saved_idx",
            ),
            models.Index(
                fields=["user", "-liked_at"],
                condition=models.Q(liked_at__isnull=False),
                name="user_paper_liked_idx",
            ),
            models.Index(
                fields=["user", "-searched_at"],
                condition=models.Q(searched_at__isnull=False),
                name="user_paper_search_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}/{self.paper_id}"

    @property
    def is_saved(self) -> bool:
        return self.saved_at is not None

    @property
    def is_liked(self) -> bool:
        return self.liked_at is not None


class FeaturedPaper(models.Model):
    """A weekly editorial selection, scoped to a specialty rather than a user."""

    paper = models.ForeignKey(
        "papers.Paper", on_delete=models.CASCADE, related_name="featured_links"
    )
    specialty = models.ForeignKey(
        "catalog.Specialty", on_delete=models.CASCADE, related_name="featured_papers"
    )
    week_start = models.DateField()
    rank = models.SmallIntegerField()
    score = models.FloatField()
    selected_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["specialty", "week_start", "paper"], name="featured_paper_unique"
            ),
            models.UniqueConstraint(
                fields=["specialty", "week_start", "rank"], name="featured_rank_unique"
            ),
        ]
        indexes = [
            models.Index(
                fields=["specialty", "-week_start", "rank"], name="featured_specialty_week_idx"
            )
        ]

    def __str__(self) -> str:
        return f"{self.specialty_id}/{self.week_start}/#{self.rank}"
