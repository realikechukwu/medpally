"""Production settings."""

import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration

from .base import *  # noqa: F403
from .base import DATABASES, env

DEBUG = False

# No default: a missing SECRET_KEY or ALLOWED_HOSTS in production must crash at
# import, not silently fall back to the insecure dev value.
SECRET_KEY = env("DJANGO_SECRET_KEY")
_app_allowed_hosts = env.list("DJANGO_ALLOWED_HOSTS", default=[])
if not _app_allowed_hosts:
    raise RuntimeError("Set DJANGO_ALLOWED_HOSTS — refusing to start with an empty host allowlist.")

ALLOWED_HOSTS = sorted(
    set(_app_allowed_hosts) | {"web.medpally.com", "medpally.com", "www.medpally.com"}
)
CSRF_TRUSTED_ORIGINS = sorted(
    set(env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[]))
    | {
        "https://web.medpally.com",
        "https://medpally.com",
        "https://www.medpally.com",
    }
)

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

DATABASES["default"].setdefault("OPTIONS", {})
DATABASES["default"]["OPTIONS"].update({"sslmode": "require", "connect_timeout": 10})

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"

_sentry_dsn = env("SENTRY_DSN", default="")
if _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        integrations=[DjangoIntegration()],
        traces_sample_rate=0.1,
        send_default_pii=False,
        environment=env("SENTRY_ENVIRONMENT", default="production"),
    )
