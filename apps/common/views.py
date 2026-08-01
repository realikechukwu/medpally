import hashlib

from django.db import connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.utils import timezone
from django.views.decorators.cache import cache_control


def landing(request: HttpRequest) -> HttpResponse:
    """What sits at /.

    Signed-in readers have no use for a pitch, so they go straight to the feed.
    Everyone else gets one — the public /p/<pmid>/ share page is the only way
    new readers arrive, and its nav has to lead somewhere that explains what
    this is.
    """
    if request.user.is_authenticated:
        return redirect("feed:list")
    return render(request, "landing.html")


# ---------------------------------------------------------------- installable app

# Kept in step with the PRECACHE list in templates/pwa/sw.js.
PRECACHED_ASSETS = ("css/app.css", "js/app.js", "js/htmx.min.js")


def manifest(request: HttpRequest) -> HttpResponse:
    """The web app manifest, rendered so icon URLs survive static hashing."""
    return render(
        request,
        "pwa/manifest.webmanifest",
        content_type="application/manifest+json",
    )


@cache_control(no_cache=True, max_age=0)
def service_worker(request: HttpRequest) -> HttpResponse:
    """The service worker, which has to be served from the root to scope to it.

    Its cache name is derived from the hashed URLs of everything it precaches,
    so a deploy that changes any of them retires the old cache by itself.
    """
    fingerprint = hashlib.sha256(
        "".join(static(path) for path in PRECACHED_ASSETS).encode()
    ).hexdigest()[:12]
    return render(
        request,
        "pwa/sw.js",
        {"cache_version": fingerprint},
        content_type="text/javascript",
    )


def offline(request: HttpRequest) -> HttpResponse:
    """The page the service worker serves when a navigation cannot reach us."""
    return render(request, "pwa/offline.html")


def favicon(request: HttpRequest) -> HttpResponse:
    """Browsers and crawlers ask for /favicon.ico regardless of our <link> tags."""
    return redirect(static("img/brand/favicon.ico"), permanent=True)


def healthz(request: HttpRequest) -> JsonResponse:
    """Liveness probe that also proves the database is reachable.

    Ingestion freshness is reported but never fails the probe: a stale feed is
    an alarm for us, not a reason for the platform to cycle a web service that
    is serving requests perfectly well. The hard alarm is
    `manage.py check_ingestion_freshness`, which runs on its own schedule.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:  # the probe must report a failure, not raise one
        return JsonResponse({"status": "error", "database": "unreachable"}, status=503)

    return JsonResponse({"status": "ok", "database": "ok", "ingestion": _ingestion_freshness()})


def _ingestion_freshness() -> dict[str, object]:
    from apps.ingestion.models import IngestionRun

    latest = (
        IngestionRun.objects.filter(status=IngestionRun.Status.SUCCESS, finished_at__isnull=False)
        .order_by("-finished_at")
        .values("finished_at", "papers_created")
        .first()
    )
    if latest is None:
        return {"last_success": None, "age_hours": None, "stale": True}

    age_hours = (timezone.now() - latest["finished_at"]).total_seconds() / 3600
    return {
        "last_success": latest["finished_at"].isoformat(),
        "age_hours": round(age_hours, 1),
        "stale": age_hours > 36,
    }
