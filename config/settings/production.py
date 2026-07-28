"""Production settings."""

import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration

from .base import *  # noqa: F403
from .base import DATABASES, env

DEBUG = False

# No default: a missing SECRET_KEY or ALLOWED_HOSTS in production must crash at
# import, not silently fall back to the insecure dev value.
SECRET_KEY = env("DJANGO_SECRET_KEY")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

# Render injects the service's own hostname. Trusting it automatically means a
# first deploy works before anyone has hand-copied the URL into an env var,
# which is exactly when a hostname mistake is hardest to diagnose (every
# request 400s with DisallowedHost and no page ever renders).
_render_host = env("RENDER_EXTERNAL_HOSTNAME", default="")
if _render_host:
    if _render_host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_render_host)
    _render_origin = f"https://{_render_host}"
    if _render_origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(_render_origin)

if not ALLOWED_HOSTS:
    raise RuntimeError(
        "Set DJANGO_ALLOWED_HOSTS (or deploy somewhere that provides "
        "RENDER_EXTERNAL_HOSTNAME) — refusing to start with an empty host allowlist."
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
