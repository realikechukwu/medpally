"""The Google sign-in button, and what it does when nobody configured it.

The button spent its life rendered `disabled` with a "Coming soon" label, so
none of this had ever been exercised. The half-configured case matters as much
as the working one: a live button backed by an empty client_id sends the reader
to a Google error page, which is worse than no button at all.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

pytestmark = pytest.mark.django_db

User = get_user_model()

CONFIGURED = {
    "GOOGLE_OAUTH_ENABLED": True,
    "SOCIALACCOUNT_PROVIDERS": {
        "google": {
            "APP": {"client_id": "test-client-id", "secret": "test-secret", "key": ""},
            "SCOPE": ["profile", "email"],
            "AUTH_PARAMS": {"access_type": "online"},
        }
    },
}


@override_settings(GOOGLE_OAUTH_ENABLED=False)
def test_button_stays_disabled_when_no_credentials_are_configured(client):
    body = client.get(reverse("account_login")).content.decode()

    assert "Coming soon" in body
    assert "disabled" in body
    assert "/accounts/google/login/" not in body


@override_settings(**CONFIGURED)
def test_button_posts_to_google_when_configured(client):
    body = client.get(reverse("account_login")).content.decode()

    assert "Coming soon" not in body
    assert "Continue with Google" in body
    assert '<form method="post" action="/accounts/google/login/?process=login">' in body
    assert "csrfmiddlewaretoken" in body


@override_settings(**CONFIGURED)
def test_signup_page_offers_google_too(client):
    body = client.get(reverse("account_signup")).content.decode()
    assert '<form method="post" action="/accounts/google/login/?process=login">' in body


@override_settings(**CONFIGURED)
def test_google_login_redirects_to_googles_consent_screen(client):
    """A GET is rejected; the POST is what starts the flow."""
    resp = client.post(reverse("google_login"))

    assert resp.status_code == 302
    location = resp["Location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth")

    params = parse_qs(urlparse(location).query)
    assert params["client_id"] == ["test-client-id"]
    assert set(params["scope"][0].split()) == {"email", "profile"}
    # The URI Google Cloud Console must have on the allowlist, verbatim.
    assert params["redirect_uri"] == ["http://testserver/accounts/google/login/callback/"]


def test_google_signup_marks_the_email_verified_and_prefills_the_name(rf):
    """Google asserts the address, so it must not ask the reader to confirm it.

    This is the other half of "email is never verified at signup": for a Google
    reader there is nothing to verify, and sending them a confirmation mail
    would be noise.
    """
    from allauth.account.models import EmailAddress
    from allauth.socialaccount.models import SocialLogin
    from django.contrib.sessions.middleware import SessionMiddleware

    from apps.accounts.adapters import SocialAccountAdapter

    request = rf.post("/accounts/google/login/callback/")
    SessionMiddleware(lambda r: None).process_request(request)

    user = User(email="doctor@gmail.com")
    account = SocialAccount(
        provider="google",
        uid="1234567890",
        extra_data={"name": "Ada Okafor", "email": "doctor@gmail.com", "email_verified": True},
    )
    sociallogin = SocialLogin(user=user, account=account)
    sociallogin.email_addresses = [
        EmailAddress(email="doctor@gmail.com", verified=True, primary=True)
    ]

    saved = SocialAccountAdapter().save_user(request=request, sociallogin=sociallogin)

    assert saved.profile.full_name == "Ada Okafor"
    assert EmailAddress.objects.get(user=saved, email="doctor@gmail.com").verified is True
