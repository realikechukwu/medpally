from __future__ import annotations

from typing import Any

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils.translation import gettext_lazy as _


class UserManager(BaseUserManager["User"]):
    """Manager for a User keyed on email rather than username."""

    use_in_migrations = True

    def _create_user(self, email: str, password: str | None, **extra: Any) -> User:
        if not email:
            raise ValueError("Users must have an email address")
        user = self.model(email=self.normalize_email(email), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra: Any) -> User:
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email: str, password: str | None = None, **extra: Any) -> User:
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        if extra.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra)


class User(AbstractUser):
    """Email-keyed user.

    This model exists from the very first migration on purpose — swapping in a
    custom user model after the fact is a multi-day data migration.
    """

    username = None  # type: ignore[assignment]
    first_name = None  # type: ignore[assignment]
    last_name = None  # type: ignore[assignment]

    email = models.EmailField(_("email address"), unique=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    objects = UserManager()  # type: ignore[misc,assignment]

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")

    def __str__(self) -> str:
        return self.email


class Profile(models.Model):
    """Everything about a user that is not authentication.

    Created by a post_save signal so it always exists — views never have to
    guard against a missing profile.
    """

    class UpdateFrequency(models.TextChoices):
        DAILY = "daily", "Daily"
        EVERY_3_DAYS = "every_3_days", "Every few days"
        WEEKLY = "weekly", "Weekly"

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, primary_key=True, related_name="profile"
    )

    full_name = models.CharField(max_length=120, blank=True, default="")
    workplace = models.CharField(max_length=200, blank=True, default="")
    specialty = models.ForeignKey(
        "catalog.Specialty",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="profiles",
    )

    # Stored from onboarding now; acted on when digest email ships. Until then
    # the feed simply updates nightly for everyone.
    update_frequency = models.CharField(
        max_length=15, choices=UpdateFrequency.choices, default=UpdateFrequency.WEEKLY
    )
    timezone = models.CharField(max_length=50, default="Europe/London")

    onboarding_completed_at = models.DateTimeField(null=True, blank=True)
    feed_last_viewed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.full_name or self.user.email

    @property
    def has_completed_onboarding(self) -> bool:
        return self.onboarding_completed_at is not None


class UserJournalSubscription(models.Model):
    """A journal a user wants in their feed.

    Soft-deactivated rather than deleted, so that re-applying a specialty preset
    never resurrects a journal the user deliberately removed.
    """

    class Source(models.TextChoices):
        PRESET = "preset", "From specialty preset"
        MANUAL = "manual", "Chosen by the user"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="journal_subscriptions")
    journal = models.ForeignKey(
        "catalog.Journal", on_delete=models.CASCADE, related_name="subscriptions"
    )
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.PRESET)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "journal"], name="user_journal_unique")
        ]
        indexes = [
            models.Index(
                fields=["user"], condition=models.Q(is_active=True), name="user_journal_active_idx"
            ),
            # Ingestion asks "which journals does anyone actually subscribe to".
            models.Index(
                fields=["journal"],
                condition=models.Q(is_active=True),
                name="journal_subscribed_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}/{self.journal_id}"


class LegacySubscriber(models.Model):
    """A cardiology-feed newsletter subscriber, imported from the Google Sheet.

    Used only to prefill onboarding when someone signs up with a matching email.
    Deliberately never auto-creates an account: there is no password and no
    consent, and it would desync the newsletter's own list.
    """

    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=120, blank=True, default="")
    specialty_slug = models.CharField(max_length=50, blank=True, default="")
    imported_at = models.DateTimeField(auto_now_add=True)
    claimed_by = models.OneToOneField(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="legacy_subscription"
    )

    def __str__(self) -> str:
        return self.email
