"""Prove the mail path works, without signing anyone up to find out.

    manage.py check_email you@example.com

Verification and password-reset mail is sent by allauth deep inside a request,
where a misconfigured relay surfaces only as a logged exception and a reader
who never got their email. This command exercises exactly the same Django mail
path from the outside and reports what actually happened, so "is Brevo
connected?" has an answer that does not involve creating a test account.

Every failure mode this has in production is a configuration one, so it prints
the configuration first and names the likely culprit on the way out.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.mail import get_connection, send_mail
from django.core.management.base import BaseCommand, CommandError

CONSOLE_BACKEND = "django.core.mail.backends.console.EmailBackend"


class Command(BaseCommand):
    help = "Send a test email through the configured backend and report the outcome."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("recipient", help="Address to send the test message to.")
        parser.add_argument(
            "--config-only",
            action="store_true",
            help="Print the effective mail configuration and exit without sending.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        recipient = options["recipient"]

        user = settings.EMAIL_HOST_USER
        password = settings.EMAIL_HOST_PASSWORD
        self.stdout.write("Effective mail configuration:")
        self.stdout.write(f"  EMAIL_BACKEND      {settings.EMAIL_BACKEND}")
        self.stdout.write(f"  EMAIL_HOST         {settings.EMAIL_HOST}:{settings.EMAIL_PORT}")
        self.stdout.write(f"  EMAIL_USE_TLS      {settings.EMAIL_USE_TLS}")
        self.stdout.write(f"  BREVO_SMTP_USER    {user or '(empty)'}")
        self.stdout.write(f"  BREVO_SMTP_KEY     {'set' if password else '(empty)'}")
        self.stdout.write(f"  DEFAULT_FROM_EMAIL {settings.DEFAULT_FROM_EMAIL}")

        if settings.EMAIL_BACKEND == CONSOLE_BACKEND:
            self.stdout.write(
                self.style.WARNING(
                    "\nEMAIL_BACKEND is the console backend: mail is printed below, never sent. "
                    "Set EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend to send."
                )
            )
        elif not (user and password):
            # Django only calls SMTP AUTH when *both* are truthy, so a half-set
            # pair authenticates as nobody and Brevo rejects the message.
            self.stdout.write(
                self.style.WARNING(
                    "\nBREVO_SMTP_USER and BREVO_SMTP_KEY are not both set. Django skips SMTP "
                    "authentication entirely unless both are present, and Brevo rejects "
                    "unauthenticated mail."
                )
            )

        if options["config_only"]:
            return

        try:
            sent = send_mail(
                subject=f"{settings.SITE_NAME} mail check",
                message=(
                    f"This is a test message from {settings.SITE_NAME}.\n\n"
                    "If you are reading it, verification and password-reset email works."
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient],
                connection=get_connection(fail_silently=False),
            )
        except Exception as exc:
            raise CommandError(
                f"{type(exc).__name__}: {exc}\n\n"
                "Most likely: wrong or missing BREVO_SMTP_USER/BREVO_SMTP_KEY (the user is the "
                "SMTP login from Brevo > SMTP & API, not your Brevo account email), or "
                f"{settings.DEFAULT_FROM_EMAIL} is not a verified sender or authenticated "
                "domain in Brevo."
            ) from exc

        if not sent:
            raise CommandError("The backend accepted no messages. Nothing was sent.")
        self.stdout.write(self.style.SUCCESS(f"\nAccepted for delivery to {recipient}."))
        self.stdout.write(
            "Brevo accepting a message is not the same as inboxing it — if nothing arrives, "
            "check the Brevo dashboard's transactional log for a bounce or a blocked sender."
        )
