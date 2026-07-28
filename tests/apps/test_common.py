"""The landing page, error templates, and the legacy subscriber importer."""

from __future__ import annotations

import csv
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import LegacySubscriber
from apps.catalog.models import Specialty

pytestmark = pytest.mark.django_db

User = get_user_model()


# ---------------------------------------------------------------- landing page


def test_landing_page_serves_anonymously(client):
    resp = client.get(reverse("landing"))
    assert resp.status_code == 200
    assert b"Create an account" in resp.content


def test_landing_page_redirects_a_signed_in_reader_to_the_feed(client):
    user = User.objects.create_user(email="reader@example.com", password="pw12345!")
    user.profile.onboarding_completed_at = timezone.now()
    user.profile.save()
    client.force_login(user)

    resp = client.get(reverse("landing"))
    assert resp.status_code == 302
    assert resp.url == reverse("feed:list")


def test_logging_out_lands_somewhere_real(client):
    """LOGOUT_REDIRECT_URL is "/" — before the landing page existed it 404'd."""
    user = User.objects.create_user(email="reader@example.com", password="pw12345!")
    user.profile.onboarding_completed_at = timezone.now()
    user.profile.save()
    client.force_login(user)

    resp = client.post(reverse("account_logout"), follow=True)
    assert resp.status_code == 200
    assert resp.redirect_chain[-1][0] == "/"


def test_landing_page_does_not_swallow_the_onboarding_routes(client):
    """apps.accounts.urls is also mounted at "" — both must still resolve."""
    assert reverse("landing") == "/"
    assert reverse("accounts:onboarding_profile") == "/onboarding/profile/"


# ---------------------------------------------------------------- CSV importer


def _write_csv(tmp_path, rows, header=("email", "first name", "specialty")):
    path = tmp_path / "subscribers.csv"
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


@pytest.fixture
def cardiology():
    return Specialty.objects.create(slug="cardiology", name="Cardiology")


def test_import_creates_subscribers(tmp_path, cardiology):
    path = _write_csv(tmp_path, [["Ada@Example.com ", "Ada", "cardiology"]])
    call_command("import_legacy_subscribers", str(path), stdout=StringIO())

    sub = LegacySubscriber.objects.get()
    assert sub.email == "ada@example.com"  # normalised
    assert sub.first_name == "Ada"
    assert sub.specialty_slug == "cardiology"


def test_import_is_idempotent(tmp_path, cardiology):
    path = _write_csv(tmp_path, [["ada@example.com", "Ada", "cardiology"]])
    call_command("import_legacy_subscribers", str(path), stdout=StringIO())
    call_command("import_legacy_subscribers", str(path), stdout=StringIO())
    assert LegacySubscriber.objects.count() == 1


def test_import_never_overwrites_a_claimed_row(tmp_path, cardiology):
    user = User.objects.create_user(email="ada@example.com", password="pw12345!")
    LegacySubscriber.objects.create(
        email="ada@example.com", first_name="Ada", specialty_slug="cardiology", claimed_by=user
    )
    path = _write_csv(tmp_path, [["ada@example.com", "SHEET OVERWROTE ME", "cardiology"]])
    call_command("import_legacy_subscribers", str(path), stdout=StringIO())

    assert LegacySubscriber.objects.get().first_name == "Ada"


def test_import_dry_run_writes_nothing(tmp_path, cardiology):
    path = _write_csv(tmp_path, [["ada@example.com", "Ada", "cardiology"]])
    call_command("import_legacy_subscribers", str(path), "--dry-run", stdout=StringIO())
    assert LegacySubscriber.objects.count() == 0


def test_import_ignores_unknown_specialties_and_junk_rows(tmp_path, cardiology):
    path = _write_csv(
        tmp_path,
        [
            ["ada@example.com", "Ada", "dermatology"],  # not a specialty we have
            ["not-an-email", "Junk", "cardiology"],
            ["", "Blank", "cardiology"],
            ["ada@example.com", "Duplicate", "cardiology"],  # same address twice
        ],
    )
    out = StringIO()
    call_command("import_legacy_subscribers", str(path), stdout=out)

    assert LegacySubscriber.objects.count() == 1
    assert LegacySubscriber.objects.get().specialty_slug == ""
    assert "dermatology" in out.getvalue()


def test_import_accepts_alternative_header_spellings(tmp_path, cardiology):
    path = _write_csv(
        tmp_path,
        [["ada@example.com", "Ada", "cardiology"]],
        header=("Email Address", "Name", "Speciality"),
    )
    call_command("import_legacy_subscribers", str(path), stdout=StringIO())
    assert LegacySubscriber.objects.get().first_name == "Ada"


def test_imported_row_prefills_onboarding(tmp_path, cardiology):
    """The whole point of the table: a matching signup starts part-filled."""
    path = _write_csv(tmp_path, [["ada@example.com", "Ada", "cardiology"]])
    call_command("import_legacy_subscribers", str(path), stdout=StringIO())

    from apps.accounts.services import prefill_from_legacy_subscriber

    user = User.objects.create_user(email="ada@example.com", password="pw12345!")
    prefill_from_legacy_subscriber(user, user.profile)

    user.profile.refresh_from_db()
    assert user.profile.full_name == "Ada"
    assert user.profile.specialty == cardiology
    assert LegacySubscriber.objects.get().claimed_by == user
