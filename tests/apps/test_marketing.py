"""The public site is isolated by hostname from the MedPally application."""

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings

pytestmark = pytest.mark.django_db

MARKETING_HOSTS = frozenset({"web.medpally.com", "medpally.com", "www.medpally.com"})
PUBLIC_SETTINGS = override_settings(
    ALLOWED_HOSTS=["testserver", *MARKETING_HOSTS],
    MARKETING_HOSTS=MARKETING_HOSTS,
    SITE_BASE_URL="https://app.medpally.com",
    MARKETING_BASE_URL="https://web.medpally.com",
)


@PUBLIC_SETTINGS
@pytest.mark.parametrize("host", ["web.medpally.com", "medpally.com", "www.medpally.com"])
def test_every_marketing_hostname_serves_the_public_homepage(client, host):
    response = client.get("/", HTTP_HOST=host)

    assert response.status_code == 200
    assert b"Keep up with the research that matters" in response.content
    assert b'href="https://app.medpally.com/accounts/signup/"' in response.content


@PUBLIC_SETTINGS
def test_footer_has_small_plain_information_links_on_every_page(client):
    for path in ("/", "/privacy/", "/terms/", "/ai-summaries/"):
        response = client.get(path, HTTP_HOST="web.medpally.com")
        assert response.status_code == 200
        assert b'<nav class="marketing-footer-links"' in response.content
        assert b'href="/privacy/">Privacy</a>' in response.content
        assert b'href="/terms/">Terms</a>' in response.content
        assert b'href="/ai-summaries/">AI summarisation</a>' in response.content


@PUBLIC_SETTINGS
@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/privacy/", b"Information we collect"),
        ("/terms/", b"Not medical advice"),
        ("/ai-summaries/", b"How AI summarisation works"),
    ],
)
def test_information_pages_are_public(client, path, expected):
    response = client.get(path, HTTP_HOST="web.medpally.com")
    assert response.status_code == 200
    assert expected in response.content


@PUBLIC_SETTINGS
def test_app_routes_are_not_exposed_on_the_marketing_hostname(client):
    assert client.get("/feed/", HTTP_HOST="web.medpally.com").status_code == 404
    assert client.get("/accounts/login/", HTTP_HOST="web.medpally.com").status_code == 404
    assert client.get("/sw.js", HTTP_HOST="web.medpally.com").status_code == 404


@PUBLIC_SETTINGS
def test_app_hostname_keeps_the_existing_landing_page(client):
    response = client.get("/", HTTP_HOST="testserver")
    assert response.status_code == 200
    assert b"The week's research from the journals you actually read" in response.content


@PUBLIC_SETTINGS
def test_unfinished_signed_in_reader_can_view_the_public_site(client):
    user = get_user_model().objects.create_user(email="new@example.com", password="password")
    client.force_login(user)

    response = client.get("/privacy/", HTTP_HOST="web.medpally.com")

    assert response.status_code == 200
    assert not response.has_header("Location")


@PUBLIC_SETTINGS
def test_marketing_discovery_files_use_the_current_host(client):
    robots = client.get("/robots.txt", HTTP_HOST="web.medpally.com", secure=True)
    sitemap = client.get("/sitemap.xml", HTTP_HOST="web.medpally.com", secure=True)

    assert robots.status_code == 200
    assert b"Sitemap: https://web.medpally.com/sitemap.xml" in robots.content
    assert sitemap.status_code == 200
    assert b"<loc>https://web.medpally.com/privacy/</loc>" in sitemap.content
