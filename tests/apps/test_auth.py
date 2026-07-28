"""The allauth pages themselves.

Every other test in this suite builds users with User.objects.create_user(),
which is exactly why none of them noticed that the signup page 500s: nothing
had ever exercised allauth's own views against our username-less User model.
"""

from __future__ import annotations

import pytest
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

    # A brand-new account has nothing to read yet, so the middleware should
    # have routed them into the wizard rather than an empty feed.
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


def test_login_works_with_email(client):
    User.objects.create_user(email="reader@example.com", password="a-perfectly-fine-password-42")
    resp = client.post(
        reverse("account_login"),
        {"login": "reader@example.com", "password": "a-perfectly-fine-password-42"},
        follow=True,
    )
    assert resp.status_code == 200
    assert resp.wsgi_request.user.is_authenticated
