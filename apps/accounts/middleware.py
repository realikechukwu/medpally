"""Guide unfinished readers to onboarding without blocking their feed.

The feed is the first useful destination after signing in.  Onboarding stays
resumable through its own URLs, while readers can browse the app and complete
their profile when they are ready.
"""

from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

from apps.accounts import services

# /p/ is on this list because it is the public share page: someone who signed
# up from a shared link and hasn't finished onboarding should still be able to
# read the paper that brought them here, not be bounced into the wizard.
ALLOWED_PREFIXES = (
    "/onboarding/",
    "/accounts/",
    "/feed/",
    "/static/",
    "/admin/",
    "/healthz",
    "/p/",
    # Installed-app plumbing: redirecting these to the wizard would break
    # installation and leave the service worker unable to update itself.
    "/sw.js",
    "/manifest.webmanifest",
    "/offline/",
    "/favicon.ico",
)


class OnboardingMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if self._should_redirect(request):
            return redirect(self._next_step(request))
        return self.get_response(request)

    def _should_redirect(self, request: HttpRequest) -> bool:
        if not request.user.is_authenticated:
            return False
        if request.path.startswith(ALLOWED_PREFIXES):
            return False
        return not request.user.profile.has_completed_onboarding

    def _next_step(self, request: HttpRequest) -> str:
        return reverse(services.next_onboarding_url_name(request.user))
