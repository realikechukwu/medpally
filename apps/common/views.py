from django.db import connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone


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
