"""The landing page, the installable app, and the legacy subscriber importer."""

from __future__ import annotations

import csv
import json
import re
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


def test_landing_page_dresses_the_browser_chrome_in_the_hero_colour(client):
    """The hero runs edge to edge, so a white browser bar would show as a seam."""
    resp = client.get(reverse("landing"))
    assert b'<meta name="theme-color" content="#123A4D">' in resp.content


# ---------------------------------------------------------------- installable app


def test_manifest_meets_the_install_criteria(client):
    resp = client.get(reverse("manifest"))
    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/manifest+json"

    manifest = json.loads(resp.content)
    assert manifest["start_url"] == "/"
    assert manifest["display"] == "standalone"
    # Chrome will not offer to install without both of these sizes, and Android
    # crops a non-maskable icon into a circle without asking.
    assert {"192x192", "512x512"} <= {icon["sizes"] for icon in manifest["icons"]}
    assert any(icon["purpose"] == "maskable" for icon in manifest["icons"])


def test_service_worker_is_served_from_the_site_root(client):
    """A worker under /static/ could only ever control /static/."""
    resp = client.get(reverse("service_worker"))
    assert resp.status_code == 200
    assert resp["Content-Type"].startswith("text/javascript")
    assert resp.request["PATH_INFO"] == "/sw.js"


def test_service_worker_precaches_nothing_that_knows_who_is_reading(client):
    """Everything it stores is public: static assets and the offline notice."""
    body = client.get(reverse("service_worker")).content.decode()
    precache = re.search(r"var PRECACHE = \[(.*?)\];", body, re.S)
    assert precache is not None
    assert all(url.startswith("/static/") for url in re.findall(r'"([^"]+)"', precache.group(1)))


def test_offline_notice_serves_anonymously(client):
    """The worker caches it at install, before anyone has signed in."""
    resp = client.get(reverse("offline"))
    assert resp.status_code == 200
    assert b"No connection" in resp.content


def test_app_plumbing_is_reachable_midway_through_onboarding(client):
    """OnboardingMiddleware sends unknown paths to the wizard, and an install
    or a worker update must not be answered with a redirect to it."""
    user = User.objects.create_user(email="halfway@example.com", password="pw12345!")
    client.force_login(user)

    for name in ("manifest", "service_worker", "offline"):
        assert client.get(reverse(name)).status_code == 200


def test_favicon_at_the_root_redirects_to_the_static_file(client):
    """Browsers and crawlers ask for /favicon.ico whatever the <link> tags say."""
    resp = client.get("/favicon.ico")
    assert resp.status_code == 301
    assert resp.url.endswith("/img/brand/favicon.ico")


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


# ---------------------------------------------------------------- healthz / freshness


def _run(**kwargs):
    from apps.ingestion.models import IngestionRun

    defaults = {
        "status": IngestionRun.Status.SUCCESS,
        "command": "ingest_papers",
        "journals_queried": 51,
        "papers_created": 12,
        "finished_at": timezone.now(),
    }
    defaults.update(kwargs)
    return IngestionRun.objects.create(**defaults)


def test_healthz_reports_ok_and_freshness(client):
    _run()
    resp = client.get(reverse("healthz"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["ingestion"]["stale"] is False


def test_healthz_reports_stale_without_failing_the_probe(client):
    """A stale feed must not make the platform cycle a healthy web service."""
    _run(finished_at=timezone.now() - timezone.timedelta(hours=72))
    resp = client.get(reverse("healthz"))
    assert resp.status_code == 200
    assert resp.json()["ingestion"]["stale"] is True


def test_freshness_command_passes_on_a_recent_run():
    out = StringIO()
    _run()
    call_command("check_ingestion_freshness", stdout=out)
    assert "fresh" in out.getvalue()


def test_freshness_command_fails_when_nothing_has_ever_run():
    with pytest.raises(SystemExit) as exc:
        call_command("check_ingestion_freshness", stderr=StringIO())
    assert exc.value.code == 1


def test_freshness_command_fails_on_a_stale_run():
    _run(finished_at=timezone.now() - timezone.timedelta(hours=48))
    err = StringIO()
    with pytest.raises(SystemExit):
        call_command("check_ingestion_freshness", stderr=err)
    assert "STALE" in err.getvalue()


def test_freshness_command_fails_on_a_run_that_queried_no_journals():
    """The scheduler fired, found nothing to do, and the feed still goes stale."""
    _run(journals_queried=0)
    err = StringIO()
    with pytest.raises(SystemExit):
        call_command("check_ingestion_freshness", stderr=err)
    assert "zero journals" in err.getvalue()


def test_freshness_command_ignores_failed_and_running_rows():
    from apps.ingestion.models import IngestionRun

    _run(status=IngestionRun.Status.FAILED)
    _run(status=IngestionRun.Status.RUNNING, finished_at=None)
    with pytest.raises(SystemExit):
        call_command("check_ingestion_freshness", stderr=StringIO())
