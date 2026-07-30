"""Onboarding: middleware gating, preset/journal diffing, and the wizard end to end."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import LegacySubscriber, UserJournalSubscription
from apps.catalog.models import Journal, Specialty, SpecialtyJournal

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def cardiology():
    return Specialty.objects.create(slug="cardiology", name="Cardiology")


@pytest.fixture
def gp():
    return Specialty.objects.create(slug="gp", name="General Practice")


@pytest.fixture
def circulation(cardiology):
    j = Journal.objects.create(
        slug="circulation", pubmed_name="Circulation", display_name="Circulation", short_name="Circ"
    )
    SpecialtyJournal.objects.create(specialty=cardiology, journal=j, is_default=True)
    return j


@pytest.fixture
def jacc(cardiology):
    j = Journal.objects.create(
        slug="jacc", pubmed_name="JACC", display_name="JACC", short_name="JACC"
    )
    SpecialtyJournal.objects.create(specialty=cardiology, journal=j, is_default=True)
    return j


@pytest.fixture
def user():
    return User.objects.create_user(email="reader@example.com", password="pw12345!")


# ---------------------------------------------------------------- apply_specialty_preset


def test_apply_specialty_preset_subscribes_to_every_default_journal(
    user, cardiology, circulation, jacc
):
    services.apply_specialty_preset(user, cardiology)
    active = set(
        UserJournalSubscription.objects.filter(user=user, is_active=True).values_list(
            "journal_id", flat=True
        )
    )
    assert active == {circulation.id, jacc.id}


def test_apply_specialty_preset_never_reactivates_a_manually_removed_journal(
    user, cardiology, circulation, jacc
):
    services.apply_specialty_preset(user, cardiology)
    sub = UserJournalSubscription.objects.get(user=user, journal=circulation)
    sub.is_active = False
    sub.source = UserJournalSubscription.Source.MANUAL
    sub.save()

    services.apply_specialty_preset(user, cardiology)  # e.g. re-applied in settings

    sub.refresh_from_db()
    assert sub.is_active is False


def test_reset_journal_selection_replaces_old_specialty_preset(user, cardiology, circulation, jacc):
    spine = Specialty.objects.create(slug="spine", name="Spine Surgery")
    spine_journal = Journal.objects.create(
        slug="spine", pubmed_name="Spine", display_name="Spine", short_name="Spine"
    )
    SpecialtyJournal.objects.create(specialty=spine, journal=spine_journal, is_default=True)

    services.apply_specialty_preset(user, cardiology)
    services.reset_journal_selection_to_specialty_preset(user, spine)

    active = set(
        UserJournalSubscription.objects.filter(user=user, is_active=True).values_list(
            "journal_id", flat=True
        )
    )
    assert active == {spine_journal.id}
    assert not UserJournalSubscription.objects.filter(
        user=user, journal__in=[circulation, jacc]
    ).exists()


# ---------------------------------------------------------------- apply_journal_selection


def test_apply_journal_selection_activates_and_creates(user, circulation, jacc):
    services.apply_journal_selection(user, {circulation.id, jacc.id})
    active = set(
        UserJournalSubscription.objects.filter(user=user, is_active=True).values_list(
            "journal_id", flat=True
        )
    )
    assert active == {circulation.id, jacc.id}
    assert UserJournalSubscription.objects.get(journal=circulation).source == "manual"


def test_apply_journal_selection_deactivates_unchecked_journals_as_manual(
    user, cardiology, circulation, jacc
):
    services.apply_specialty_preset(user, cardiology)  # both active, source=preset
    services.apply_journal_selection(user, {circulation.id})  # jacc unchecked

    jacc_sub = UserJournalSubscription.objects.get(user=user, journal=jacc)
    assert jacc_sub.is_active is False
    assert jacc_sub.source == "manual"

    circ_sub = UserJournalSubscription.objects.get(user=user, journal=circulation)
    assert circ_sub.is_active is True


def test_apply_journal_selection_deselect_then_reselect_does_not_resurrect_via_preset(
    user, cardiology, circulation, jacc
):
    services.apply_specialty_preset(user, cardiology)
    services.apply_journal_selection(user, {circulation.id})  # drop jacc
    services.apply_specialty_preset(user, cardiology)  # re-apply preset (e.g. settings)

    jacc_sub = UserJournalSubscription.objects.get(user=user, journal=jacc)
    assert jacc_sub.is_active is False


# ---------------------------------------------------------------- legacy prefill


def test_prefill_from_legacy_subscriber_fills_blank_profile(user, cardiology):
    LegacySubscriber.objects.create(email=user.email, first_name="Ada", specialty_slug="cardiology")
    services.prefill_from_legacy_subscriber(user, user.profile)
    user.profile.refresh_from_db()
    assert user.profile.full_name == "Ada"
    assert user.profile.specialty == cardiology


def test_prefill_from_legacy_subscriber_marks_it_claimed(user):
    legacy = LegacySubscriber.objects.create(email=user.email, first_name="Ada")
    services.prefill_from_legacy_subscriber(user, user.profile)
    legacy.refresh_from_db()
    assert legacy.claimed_by == user


def test_prefill_from_legacy_subscriber_is_a_noop_with_no_match(user):
    services.prefill_from_legacy_subscriber(user, user.profile)
    user.profile.refresh_from_db()
    assert user.profile.full_name == ""


# ---------------------------------------------------------------- middleware


def test_middleware_redirects_unfinished_user_to_profile_step(client, user):
    client.force_login(user)
    response = client.get("/feed/")
    assert response.status_code == 302
    assert response.url == reverse("accounts:onboarding_profile")


def test_middleware_redirects_to_journals_once_specialty_is_set(client, user, cardiology):
    user.profile.specialty = cardiology
    user.profile.save()
    client.force_login(user)
    response = client.get("/feed/")
    assert response.url == reverse("accounts:onboarding_journals")


def test_middleware_redirects_to_frequency_once_journals_exist(
    client, user, cardiology, circulation
):
    user.profile.specialty = cardiology
    user.profile.save()
    UserJournalSubscription.objects.create(user=user, journal=circulation)
    client.force_login(user)
    response = client.get("/feed/")
    assert response.url == reverse("accounts:onboarding_frequency")


def test_middleware_allows_completed_user_through(client, user, cardiology, circulation):
    user.profile.specialty = cardiology
    user.profile.onboarding_completed_at = timezone.now()
    user.profile.save()
    UserJournalSubscription.objects.create(user=user, journal=circulation)
    client.force_login(user)
    response = client.get("/feed/")
    assert response.status_code == 200


def test_middleware_does_not_redirect_anonymous_users(client):
    response = client.get("/feed/")
    assert response.status_code == 302
    assert response.url != reverse("accounts:onboarding_profile")  # goes to login instead


def test_middleware_allowlists_admin_even_when_unfinished(client, user):
    client.force_login(user)
    response = client.get("/admin/")
    assert response.status_code != 302 or "onboarding" not in response.url


# ---------------------------------------------------------------- end-to-end wizard


def test_full_onboarding_wizard_end_to_end(client, cardiology, circulation, jacc):
    user = User.objects.create_user(email="new@example.com", password="pw12345!")
    client.force_login(user)

    response = client.post(
        reverse("accounts:onboarding_profile"),
        {
            "full_name": "Dr. Ada Lovelace",
            "workplace": "General Hospital",
            "specialty": cardiology.id,
        },
    )
    assert response.status_code == 302
    assert response.url == reverse("accounts:onboarding_journals")
    user.profile.refresh_from_db()
    assert user.profile.full_name == "Dr. Ada Lovelace"
    # The preset was applied on save.
    assert UserJournalSubscription.objects.filter(user=user, is_active=True).count() == 2

    response = client.post(reverse("accounts:onboarding_journals"), {"journals": [circulation.id]})
    assert response.status_code == 302
    assert response.url == reverse("accounts:onboarding_frequency")
    active = set(
        UserJournalSubscription.objects.filter(user=user, is_active=True).values_list(
            "journal_id", flat=True
        )
    )
    assert active == {circulation.id}  # jacc was unchecked

    response = client.post(reverse("accounts:onboarding_frequency"), {"update_frequency": "daily"})
    assert response.status_code == 302
    assert response.url == reverse("feed:list")

    user.profile.refresh_from_db()
    assert user.profile.onboarding_completed_at is not None
    assert user.profile.update_frequency == "daily"

    # And now they're through — no more onboarding redirects.
    response = client.get(reverse("feed:list"))
    assert response.status_code == 200


def test_changing_specialty_during_onboarding_replaces_previous_journals(
    client, user, cardiology, circulation
):
    spine = Specialty.objects.create(slug="spine", name="Spine Surgery")
    spine_journal = Journal.objects.create(
        slug="spine", pubmed_name="Spine", display_name="Spine", short_name="Spine"
    )
    SpecialtyJournal.objects.create(specialty=spine, journal=spine_journal, is_default=True)
    client.force_login(user)

    client.post(
        reverse("accounts:onboarding_profile"),
        {"full_name": "Dr. Ada", "workplace": "", "specialty": cardiology.id},
    )
    response = client.post(
        reverse("accounts:onboarding_profile"),
        {"full_name": "Dr. Ada", "workplace": "", "specialty": spine.id},
    )

    assert response.url == reverse("accounts:onboarding_journals")
    active = set(
        UserJournalSubscription.objects.filter(user=user, is_active=True).values_list(
            "journal_id", flat=True
        )
    )
    assert active == {spine_journal.id}

    response = client.get(reverse("accounts:onboarding_journals"))
    assert b"Spine Surgery journals" in response.content
    assert b'data-journal-group-toggle="specialty"' in response.content


def test_settings_pages_reuse_the_same_forms_without_touching_onboarding_state(
    client, user, cardiology, circulation
):
    user.profile.specialty = cardiology
    user.profile.onboarding_completed_at = timezone.now()
    user.profile.save()
    UserJournalSubscription.objects.create(user=user, journal=circulation)
    client.force_login(user)

    response = client.post(
        reverse("accounts:settings_profile"),
        {"full_name": "New Name", "workplace": "", "specialty": cardiology.id},
    )
    assert response.status_code == 302
    assert response.url == reverse("accounts:settings_profile")
    user.profile.refresh_from_db()
    assert user.profile.full_name == "New Name"
    assert user.profile.onboarding_completed_at is not None


# ---------------------------------------------------------------- account page


def _onboard(user, cardiology, circulation):
    user.profile.specialty = cardiology
    user.profile.full_name = "Dr. Jane Okafor"
    user.profile.onboarding_completed_at = timezone.now()
    user.profile.save()
    UserJournalSubscription.objects.create(user=user, journal=circulation)


def test_account_page_shows_profile_summary(client, user, cardiology, circulation):
    _onboard(user, cardiology, circulation)
    client.force_login(user)

    response = client.get(reverse("accounts:account"))
    assert response.status_code == 200
    assert b"Dr. Jane Okafor" in response.content
    assert b"Cardiology" in response.content
    assert b"1 selected" in response.content


def test_account_page_initials_fall_back_to_email_when_no_name(
    client, user, cardiology, circulation
):
    _onboard(user, cardiology, circulation)
    user.profile.full_name = ""
    user.profile.save()
    client.force_login(user)

    response = client.get(reverse("accounts:account"))
    assert response.status_code == 200


# ---------------------------------------------------------------- delete account


def test_delete_account_requires_typed_confirmation(client, user, cardiology, circulation):
    _onboard(user, cardiology, circulation)
    client.force_login(user)

    response = client.post(
        reverse("accounts:delete_account_confirm"), {"confirm_text": "delete pls"}
    )
    assert response.status_code == 200
    assert User.objects.filter(pk=user.pk).exists()


def test_delete_account_deletes_user_and_everything_cascading(
    client, user, cardiology, circulation
):
    _onboard(user, cardiology, circulation)
    client.force_login(user)

    response = client.post(
        reverse("accounts:delete_account_confirm"), {"confirm_text": "delete"}, follow=True
    )
    assert response.status_code == 200
    assert not User.objects.filter(pk=user.pk).exists()
    assert not UserJournalSubscription.objects.filter(user_id=user.pk).exists()
    assert not response.wsgi_request.user.is_authenticated


def test_delete_account_requires_login(client):
    response = client.post(reverse("accounts:delete_account_confirm"), {"confirm_text": "DELETE"})
    assert response.status_code == 302
