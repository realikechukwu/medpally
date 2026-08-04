from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from django.apps import AppConfig
from django.conf import settings
from django.db.models.signals import post_migrate

logger = logging.getLogger(__name__)


def sync_site_from_settings(sender: Any, **kwargs: Any) -> None:
    """Point django.contrib.sites at this deployment.

    django.contrib.sites seeds exactly one row — `example.com` — and nothing
    ever changed it. allauth reads that row for the name and domain in every
    transactional email, so verification mail went out titled
    "[example.com] Please Confirm Your Email Address" and opening
    "Hello from example.com!". That is not merely cosmetic: a confirmation mail
    whose branding matches neither the sending domain nor the site the reader
    just signed up to is what spam filters score on, and what a careful reader
    refuses to click.

    Runs on post_migrate so the same `manage.py migrate` the deploy already
    runs applies it, and so it stays correct when SITE_BASE_URL changes.
    """
    if sender.label != "sites":
        return

    domain = urlparse(settings.SITE_BASE_URL).netloc or settings.SITE_BASE_URL
    site = sender.get_model("Site").objects.update_or_create(
        pk=settings.SITE_ID,
        defaults={"domain": domain, "name": settings.SITE_NAME},
    )[0]
    logger.debug("Site %s synced to %s (%s)", settings.SITE_ID, site.domain, site.name)


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    label = "accounts"

    def ready(self) -> None:
        from . import signals  # noqa: F401

        post_migrate.connect(sync_site_from_settings, dispatch_uid="accounts.sync_site")
