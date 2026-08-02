"""Select the small public-site URL map on MedPally's marketing hosts."""

from __future__ import annotations

from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse


class MarketingSiteMiddleware:
    """Keep the public site and the signed-in app on distinct hostnames.

    Both hosts reach the same Django process on Oracle, but a marketing host
    receives only the deliberately public URL configuration. This prevents an
    app URL such as /feed/ from quietly becoming available under both domains.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        host = request.get_host().split(":", 1)[0].lower().rstrip(".")
        if host in settings.MARKETING_HOSTS:
            request.urlconf = "config.marketing_urls"
            request.is_marketing_site = True
        return self.get_response(request)
