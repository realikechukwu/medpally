"""The allauth pages themselves.

Every other test in this suite builds users with User.objects.create_user(),
which is exactly why none of them noticed that the signup page 500s: nothing
had ever exercised allauth's own views against our username-less User model.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.mark.parametrize(
    "url_name",
    ["account_signup", "account_login", "account_reset_password"],
)
def test_auth_pages_render(client, url_name):
    resp = client.get(reverse(url_name))
    assert resp.status_code == 200


def test_signup_creates_an_account_and_starts_onboarding(client):
    """The most important path in the app: a stranger becomes a reader."""
    resp = client.post(
        reverse("account_signup"),
        {
            "email": "newdoctor@example.com",
            "password1": "a-perfectly-fine-password-42",
            "password2": "a-perfectly-fine-password-42",
        },
        follow=True,
    )
    assert resp.status_code == 200

    user = User.objects.get(email="newdoctor@example.com")
    assert user.profile is not None
    assert user.profile.onboarding_completed_at is None

    # Signup starts setup, while a returning reader's login goes to the feed.
    assert resp.redirect_chain
    assert resp.redirect_chain[-1][0] == reverse("accounts:onboarding_profile")


def test_signup_rejects_a_duplicate_email(client):
    User.objects.create_user(email="taken@example.com", password="pw12345678!")
    client.post(
        reverse("account_signup"),
        {
            "email": "taken@example.com",
            "password1": "a-perfectly-fine-password-42",
            "password2": "a-perfectly-fine-password-42",
        },
    )
    assert User.objects.filter(email="taken@example.com").count() == 1


def test_login_works_with_email_and_lands_on_the_feed(client):
    User.objects.create_user(email="reader@example.com", password="a-perfectly-fine-password-42")
    resp = client.post(
        reverse("account_login"),
        {"login": "reader@example.com", "password": "a-perfectly-fine-password-42"},
        follow=True,
    )
    assert resp.status_code == 200
    assert resp.wsgi_request.user.is_authenticated
    assert resp.redirect_chain[-1][0] == reverse("feed:list")


def test_login_session_is_remembered_for_ninety_days(client):
    User.objects.create_user(email="reader@example.com", password="a-perfectly-fine-password-42")

    response = client.post(
        reverse("account_login"),
        {"login": "reader@example.com", "password": "a-perfectly-fine-password-42"},
    )

    session_cookie = response.cookies[settings.SESSION_COOKIE_NAME]
    assert settings.SESSION_COOKIE_AGE == 60 * 60 * 24 * 90
    assert int(session_cookie["max-age"]) == settings.SESSION_COOKIE_AGE


@pytest.mark.parametrize(
    "url_name",
    ["account_signup", "account_login", "account_reset_password"],
)
def test_auth_pages_inherit_our_base_template(client, url_name):
    """allauth's stock layout ships no CSS at all.

    Rendered against our dark background that is near-black text on
    near-black — technically a 200, practically unusable. templates/allauth/
    layouts/base.html redirects the whole allauth template tree at base.html;
    if that override stops resolving, these pages silently become unreadable
    rather than failing, so it is worth an explicit assertion.
    """
    resp = client.get(reverse(url_name))
    assert b"js/htmx" in resp.content, "not rendering through our base.html"
