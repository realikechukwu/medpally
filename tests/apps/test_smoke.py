import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

User = get_user_model()


def test_user_model_is_the_custom_one():
    assert User._meta.label == "accounts.User"
    assert User.USERNAME_FIELD == "email"


@pytest.mark.django_db
def test_create_user_and_superuser():
    user = User.objects.create_user(email="Someone@Example.COM", password="pw")
    # normalize_email lowercases the domain but preserves the local part.
    assert user.email == "Someone@example.com"
    assert not user.is_staff

    admin = User.objects.create_superuser(email="admin@example.com", password="pw")
    assert admin.is_staff and admin.is_superuser


@pytest.mark.django_db
def test_email_is_unique():
    from django.db import IntegrityError

    User.objects.create_user(email="dup@example.com", password="pw")
    with pytest.raises(IntegrityError):
        User.objects.create_user(email="dup@example.com", password="pw")


@pytest.mark.django_db
def test_admin_login_page_serves(client):
    response = client.get(reverse("admin:login"))
    assert response.status_code == 200


@pytest.mark.django_db
def test_healthz_reports_ok(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    # Freshness is reported alongside, but never fails the probe — see
    # tests/apps/test_common.py for that behaviour.
    assert "ingestion" in body
