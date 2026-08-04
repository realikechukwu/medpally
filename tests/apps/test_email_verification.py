"""Verification email: that it is sent, and that we never lie about sending it.

None of this was covered before, which is how a resend button that silently
did nothing survived: every layer reported success, because no layer ever
checked. The failure-path tests here are the point of the file.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

pytestmark = pytest.mark.django_db

User = get_user_model()

PW = "a-perfectly-fine-password-42"
SMTP_DOWN = "django.core.mail.backends.locmem.EmailBackend.send_messages"


@pytest.fixture(autouse=True)
def _reset_confirmation_cooldown():
    """allauth keys its send cooldown on the address, in the process-wide cache.

    Without this every test after the first to use a given address silently
    takes the rate-limited branch and asserts against the wrong behaviour.
    """
    cache.clear()
    yield
    cache.clear()


def signed_in(client, email="reader@example.com", verified=False):
    user = User.objects.create_user(email=email, password=PW)
    EmailAddress.objects.create(user=user, email=email, verified=verified, primary=True)
    # OnboardingMiddleware bounces an unfinished reader out of /account/ into
    # the wizard, so these would never reach the view under test.
    user.profile.onboarding_completed_at = timezone.now()
    user.profile.save(update_fields=["onboarding_completed_at"])
    client.force_login(user)
    return user


# ---------------------------------------------------------------- signup


def test_signup_sends_a_verification_email(client):
    """ACCOUNT_EMAIL_VERIFICATION='optional' still sends — on signup only."""
    client.post(
        reverse("account_signup"),
        {"email": "new@example.com", "password1": PW, "password2": PW},
    )

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["new@example.com"]
    assert EmailAddress.objects.filter(email="new@example.com", verified=False).exists()


def test_signup_survives_a_dead_mail_relay(client):
    """The account is already committed; a broken relay must not 500 the page."""
    with patch(SMTP_DOWN, side_effect=OSError("connection refused")):
        resp = client.post(
            reverse("account_signup"),
            {"email": "new@example.com", "password1": PW, "password2": PW},
            follow=True,
        )

    assert resp.status_code == 200
    assert User.objects.filter(email="new@example.com").exists()


# ---------------------------------------------------------------- branding


def test_verification_email_is_branded_not_example_com(client):
    """django.contrib.sites ships an 'example.com' row that allauth reads.

    Left alone it puts example.com in the subject and the body of every
    transactional email, which reads as phishing and scores as spam.
    """
    # Deliberately not an example.com recipient: the address would otherwise
    # match the very string this test is looking for.
    client.post(
        reverse("account_signup"),
        {"email": "new@medpally-test.invalid", "password1": PW, "password2": PW},
    )

    message = mail.outbox[0]
    assert "example.com" not in message.subject
    assert "example.com" not in message.body
    assert message.subject.startswith("[MedPally]")
    assert "Hello from MedPally!" in message.body


def test_post_migrate_syncs_the_site_row_to_this_deployment():
    site = Site.objects.get(pk=1)
    assert site.domain != "example.com"
    assert site.name == "MedPally"


# ---------------------------------------------------------------- resend


def test_resend_sends_a_fresh_verification_email(client):
    user = signed_in(client)
    mail.outbox.clear()

    resp = client.post(reverse("accounts:resend_verification"), follow=True)

    assert resp.status_code == 200
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [user.email]
    assert "sent" in " ".join(str(m) for m in resp.context["messages"]).lower()


def test_resend_creates_the_address_row_when_a_user_has_none(client):
    """Users made in the admin or by a data import never got an EmailAddress."""
    user = User.objects.create_user(email="imported@example.com", password=PW)
    user.profile.onboarding_completed_at = timezone.now()
    user.profile.save(update_fields=["onboarding_completed_at"])
    client.force_login(user)
    mail.outbox.clear()

    client.post(reverse("accounts:resend_verification"), follow=True)

    assert EmailAddress.objects.filter(user=user, email=user.email).exists()
    assert len(mail.outbox) == 1


def test_resend_reports_failure_instead_of_claiming_success(client):
    """The bug this file exists for: 'email sent' when nothing was sent."""
    signed_in(client)

    with patch(SMTP_DOWN, side_effect=OSError("connection refused")):
        resp = client.post(reverse("accounts:resend_verification"), follow=True)

    assert resp.status_code == 200
    assert not mail.outbox
    text = " ".join(str(m) for m in resp.context["messages"]).lower()
    assert "could not send" in text
    assert "sent to" not in text


def test_resend_is_rate_limited_and_says_so(client):
    signed_in(client)
    mail.outbox.clear()

    client.post(reverse("accounts:resend_verification"))
    resp = client.post(reverse("accounts:resend_verification"), follow=True)

    assert len(mail.outbox) == 1, "allauth's cooldown should swallow the second"
    assert "moments ago" in " ".join(str(m) for m in resp.context["messages"])


def test_resend_does_nothing_for_an_already_verified_address(client):
    signed_in(client, verified=True)
    mail.outbox.clear()

    resp = client.post(reverse("accounts:resend_verification"), follow=True)

    assert not mail.outbox
    assert "already verified" in " ".join(str(m) for m in resp.context["messages"])


def test_resend_rejects_get_and_anonymous_callers(client):
    assert client.get(reverse("accounts:resend_verification")).status_code in (302, 405)

    signed_in(client)
    assert client.get(reverse("accounts:resend_verification")).status_code == 405


# ---------------------------------------------------------------- account screen


def test_account_screen_offers_resend_only_while_unverified(client):
    signed_in(client)
    resp = client.get(reverse("accounts:account"))
    assert reverse("accounts:resend_verification") in resp.content.decode()

    EmailAddress.objects.update(verified=True)
    resp = client.get(reverse("accounts:account"))
    body = resp.content.decode()
    assert reverse("accounts:resend_verification") not in body
    assert "Verified" in body


# ---------------------------------------------------------------- diagnostics


@override_settings(EMAIL_HOST_USER="", EMAIL_HOST_PASSWORD="")
def test_check_email_command_flags_missing_credentials():
    """Django skips SMTP AUTH unless *both* are set — silently, which is the trap."""
    from io import StringIO

    from django.core.management import call_command

    out = StringIO()
    call_command("check_email", "someone@example.com", "--config-only", stdout=out)

    assert "not both set" in out.getvalue()
