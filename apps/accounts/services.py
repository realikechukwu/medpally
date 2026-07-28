"""Onboarding side-effects: applying a specialty preset, diffing journal picks.

Kept out of views.py so the "never resurrect a manually-removed journal" rule
lives in one tested place rather than being re-derived at every call site.
"""

from __future__ import annotations

from apps.catalog.models import Specialty, SpecialtyJournal

from .models import User, UserJournalSubscription


def apply_specialty_preset(user: User, specialty: Specialty) -> None:
    """Subscribe the user to every default journal in this specialty's preset.

    bulk_create(ignore_conflicts=True) is what makes this safe to call more than
    once (e.g. a user changes specialty in settings later): the unique
    constraint on (user, journal) means a journal the user already has a row
    for — active or manually deactivated — is silently skipped, never
    reactivated.
    """
    journal_ids = SpecialtyJournal.objects.filter(specialty=specialty, is_default=True).values_list(
        "journal_id", flat=True
    )
    UserJournalSubscription.objects.bulk_create(
        [
            UserJournalSubscription(
                user=user, journal_id=journal_id, source=UserJournalSubscription.Source.PRESET
            )
            for journal_id in journal_ids
        ],
        ignore_conflicts=True,
    )


def apply_journal_selection(user: User, selected_journal_ids: set[int]) -> None:
    """Reconcile a user's checked journals against their subscription rows.

    A diff rather than delete-and-recreate: unchecking a journal deactivates
    its row and marks it "manual" so a later preset re-apply (see above) can
    never bring it back. Every row this function touches is stamped manual —
    once a user has directly edited their list, "preset" no longer describes
    any part of it truthfully.
    """
    existing = {sub.journal_id: sub for sub in UserJournalSubscription.objects.filter(user=user)}

    to_create = []
    for journal_id in selected_journal_ids:
        sub = existing.get(journal_id)
        if sub is None:
            to_create.append(
                UserJournalSubscription(
                    user=user,
                    journal_id=journal_id,
                    source=UserJournalSubscription.Source.MANUAL,
                    is_active=True,
                )
            )
        elif not sub.is_active or sub.source != UserJournalSubscription.Source.MANUAL:
            sub.is_active = True
            sub.source = UserJournalSubscription.Source.MANUAL
            sub.save(update_fields=["is_active", "source", "updated_at"])

    if to_create:
        UserJournalSubscription.objects.bulk_create(to_create)

    to_deactivate = [
        sub
        for journal_id, sub in existing.items()
        if journal_id not in selected_journal_ids and sub.is_active
    ]
    for sub in to_deactivate:
        sub.is_active = False
        sub.source = UserJournalSubscription.Source.MANUAL
    if to_deactivate:
        UserJournalSubscription.objects.bulk_update(to_deactivate, ["is_active", "source"])


def next_onboarding_url_name(user: User) -> str:
    """Which wizard step an unfinished user should land on next.

    Shared by OnboardingMiddleware (redirecting away from anywhere else) and
    the bare /onboarding/ view (redirecting *to* somewhere), so the notion of
    "what's next" is defined exactly once.
    """
    profile = user.profile
    if not profile.specialty_id:
        return "accounts:onboarding_profile"
    if not UserJournalSubscription.objects.filter(user=user, is_active=True).exists():
        return "accounts:onboarding_journals"
    return "accounts:onboarding_frequency"


def prefill_from_legacy_subscriber(user: User, profile) -> None:
    """Prefill onboarding from a matching cardiology-feed newsletter row.

    Never creates an account — there is no password and no consent for that —
    it only pre-populates the profile form for a user who is signing up fresh.
    """
    from .models import LegacySubscriber

    legacy = LegacySubscriber.objects.filter(
        email__iexact=user.email, claimed_by__isnull=True
    ).first()
    if legacy is None:
        return

    if not profile.full_name and legacy.first_name:
        profile.full_name = legacy.first_name
    if not profile.specialty_id and legacy.specialty_slug:
        specialty = Specialty.objects.filter(slug=legacy.specialty_slug, is_active=True).first()
        if specialty is not None:
            profile.specialty = specialty
    profile.save()

    legacy.claimed_by = user
    legacy.save(update_fields=["claimed_by"])
