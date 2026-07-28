"""Redirects any signed-in, not-yet-onboarded user to the next wizard step.

Makes the wizard both resumable (close the tab after step 1, come back next
week, land on step 2) and un-skippable (there is no URL that gets you to the
feed without a specialty and at least one journal).
"""

from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

from apps.accounts import services

ALLOWED_PREFIXES = ("/onboarding/", "/accounts/", "/static/", "/admin/", "/healthz")


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
