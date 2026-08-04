"""allauth customisation: mail that fails visibly, and Google profile prefill.

Everything else about signup (email/password fields, verification policy) is
plain allauth config in settings — this is the one place behaviour actually
differs from the default.
"""

from __future__ import annotations

import logging
from typing import Any

from allauth.account.adapter import DefaultAccountAdapter
from allauth.core import context as allauth_context
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.models import SocialLogin
from django.contrib import messages
from django.http import HttpRequest

logger = logging.getLogger(__name__)

#: Set on the request when a send failed, so a view can report it honestly.
#: Read by apps.accounts.views.resend_verification.
EMAIL_FAILED_ATTR = "medpally_email_send_failed"


class AccountAdapter(DefaultAccountAdapter):
    """Never let a broken mail relay take a page down with it.

    allauth calls `msg.send()` with Django's default fail_silently=False, so
    with no (or wrong) SMTP credentials the relay's rejection propagates all
    the way out as a 500. On signup that is the worst possible outcome: the
    account has already been created and committed, and the new reader is shown
    an error page for a *verification* mail they never asked for.

    So: swallow, but never silently. The exception is logged at ERROR (Sentry
    picks it up in production) and recorded on the request, which is what lets
    the resend view say "we couldn't send it" instead of an unconditional
    "email sent" — the exact failure that made resend look like a dead button.
    """

    def send_mail(self, template_prefix: str, email: str, context: dict) -> None:
        try:
            super().send_mail(template_prefix, email, context)
        except Exception:
            # allauth passes the request through a thread-local rather than the
            # context dict, and this is the same one its own send_mail reads.
            request = getattr(allauth_context, "request", None)
            if request is not None:
                setattr(request, EMAIL_FAILED_ATTR, True)
            logger.exception(
                "Could not send %s to %s. Check EMAIL_BACKEND, BREVO_SMTP_USER and "
                "BREVO_SMTP_KEY, and that DEFAULT_FROM_EMAIL is a verified Brevo sender.",
                template_prefix,
                email,
            )

    def add_message(
        self,
        request: HttpRequest,
        level: int,
        message_template: str | None = None,
        message_context: dict[str, Any] | None = None,
        extra_tags: str = "",
        message: str | None = None,
    ) -> None:
        """Don't claim a mail was sent when the relay just refused it.

        allauth queues "Confirmation email sent to ..." unconditionally, right
        after the send it never checked the result of. Since send_mail above
        swallows the failure, that message would be an outright lie — and it is
        the lie that made the resend button look broken rather than
        misconfigured. Swap it for the truth when we know better.
        """
        if message_template == "account/messages/email_confirmation_sent.txt" and getattr(
            request, EMAIL_FAILED_ATTR, False
        ):
            super().add_message(
                request,
                messages.ERROR,
                extra_tags=extra_tags,
                message=(
                    "We could not send the verification email just now. "
                    "Please try again in a few minutes."
                ),
            )
            return
        super().add_message(request, level, message_template, message_context, extra_tags, message)


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    def save_user(self, request: HttpRequest, sociallogin: SocialLogin, form: Any = None) -> Any:
        user = super().save_user(request, sociallogin, form)
        full_name = sociallogin.account.extra_data.get("name", "")
        if full_name and not user.profile.full_name:
            user.profile.full_name = full_name
            user.profile.save(update_fields=["full_name"])
        return user
