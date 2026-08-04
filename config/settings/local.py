"""Local development settings."""

from .base import *  # noqa: F403
from .base import INSTALLED_APPS, env  # noqa: F401

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0"]

# Console by default — nobody wants signup during development to send real
# mail. But this used to be hardcoded, which meant a developer could set
# EMAIL_BACKEND and BREVO_SMTP_* in .env, see "Confirmation email sent", and
# still never receive anything, with nothing anywhere saying why. Honour the
# environment so `manage.py check_email you@example.com` can prove the Brevo
# credentials actually work before trusting them in production.
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")

# Fail loudly on a missing static file in dev rather than at deploy time.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
