"""allauth customisation: prefill full_name from a Google profile.

Everything else about signup (email/password fields, verification policy) is
plain allauth config in settings — this is the one place behaviour actually
differs from the default.
"""

from __future__ import annotations

from typing import Any

from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.models import SocialLogin
from django.http import HttpRequest


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    def save_user(self, request: HttpRequest, sociallogin: SocialLogin, form: Any = None) -> Any:
        user = super().save_user(request, sociallogin, form)
        full_name = sociallogin.account.extra_data.get("name", "")
        if full_name and not user.profile.full_name:
            user.profile.full_name = full_name
            user.profile.save(update_fields=["full_name"])
        return user
