"""Test settings.

The test database is local Postgres — ArrayField and GinIndex rule out SQLite,
and tests must never point at Supabase.
"""

from .base import *  # noqa: F403
from .base import DATABASES, MIDDLEWARE, env

DEBUG = False

# WhiteNoise warns about a missing staticfiles/ dir and serves nothing useful
# in tests. Drop it rather than collectstatic on every run.
MIDDLEWARE = [m for m in MIDDLEWARE if "whitenoise" not in m]

DATABASES["default"] = env.db_url(
    "TEST_DATABASE_URL",
    default="postgres://medfeed:medfeed@localhost:5433/medfeed_test",
)
DATABASES["default"]["CONN_MAX_AGE"] = 0

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Fast, and keeps password-hashing out of test timings.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Anything that reaches for a real key in a test is a bug we want to see.
OPENAI_API_KEY = ""
PUBMED_API_KEY = ""
PUBMED_EMAIL = "test@example.com"
