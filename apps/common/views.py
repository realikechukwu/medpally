from django.db import connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render


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
    """Liveness probe that also proves the database is reachable."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:  # the probe must report a failure, not raise one
        return JsonResponse({"status": "error", "database": "unreachable"}, status=503)
    return JsonResponse({"status": "ok", "database": "ok"})
