"""Settings shared by every environment.

Environment-specific modules (local/production/test) import * from here and
override. Nothing in this file may read a secret without a default, so that
`manage.py` works on a fresh checkout with no .env.
"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="insecure-dev-key-do-not-use-in-production")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

SITE_NAME = env("SITE_NAME", default="MedFeed")
SITE_BASE_URL = env("SITE_BASE_URL", default="http://localhost:8000")
SITE_ID = 1

# ---------------------------------------------------------------- apps

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.sites",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
]

THIRD_PARTY_APPS = [
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
]

LOCAL_APPS = [
    "apps.accounts",
    "apps.catalog",
    "apps.papers",
    "apps.feed",
    "apps.ingestion",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    # apps.accounts.middleware.OnboardingMiddleware is added in phase 4, once
    # Profile and the wizard exist.
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.common.context_processors.site",
            ],
        },
    },
]

# ---------------------------------------------------------------- database

# Supabase: use the SESSION pooler (port 5432), never the transaction pooler
# (6543) — that one breaks server-side cursors and prepared statements, and
# migrations through it hang. See the plan's "Supabase specifics".
#
# CONN_MAX_AGE stays at 0 by default: in session mode each Django connection
# holds a pooler slot for its whole lifetime and the free-tier pool is ~15.
DATABASES = {
    "default": env.db_url(
        "DATABASE_URL",
        default="postgres://medfeed:medfeed@localhost:5433/medfeed",
    )
}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=0)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------- auth

AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_REDIRECT_URL = "/feed/"
LOGOUT_REDIRECT_URL = "/"

# allauth (65.x style settings — ACCOUNT_AUTHENTICATION_METHOD and
# ACCOUNT_EMAIL_REQUIRED are deprecated).
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_EMAIL_VERIFICATION = "optional"
ACCOUNT_UNIQUE_EMAIL = True
# Custom adapters (prefilling full_name from the Google profile) land in phase 4.
SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True
SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "APP": {
            "client_id": env("GOOGLE_OAUTH_CLIENT_ID", default=""),
            "secret": env("GOOGLE_OAUTH_CLIENT_SECRET", default=""),
            "key": "",
        },
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {"access_type": "online"},
    }
}

# ---------------------------------------------------------------- email
#
# Phase 1 sends no digest email — that stays with the cardiology-feed repo.
# This is auth transactional only (password reset, optional verification).

EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", default="smtp-relay.brevo.com")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_USE_TLS = True
EMAIL_HOST_USER = env("BREVO_SMTP_USER", default="")
EMAIL_HOST_PASSWORD = env("BREVO_SMTP_KEY", default="")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="MedFeed <noreply@medfeed.app>")

# ---------------------------------------------------------------- engine

PUBMED_EMAIL = env("NCBI_EMAIL", default="")
PUBMED_API_KEY = env("NCBI_API_KEY", default="")
PUBMED_TOOL_NAME = env("PUBMED_TOOL_NAME", default="medfeed")

OPENAI_API_KEY = env("OPENAI_API_KEY", default="")
OPENAI_MODEL = env("OPENAI_MODEL", default="gpt-4o-mini")
SUMMARY_MAX_PER_RUN = env.int("SUMMARY_MAX_PER_RUN", default=400)
SUMMARY_MIN_ABSTRACT_CHARS = env.int("SUMMARY_MIN_ABSTRACT_CHARS", default=200)

INGEST_LOOKBACK_DAYS = env.int("INGEST_LOOKBACK_DAYS", default=3)
RELEVANCE_RECHECK_DAYS = env.int("RELEVANCE_RECHECK_DAYS", default=90)

FEED_PAGE_SIZE = env.int("FEED_PAGE_SIZE", default=20)

# ---------------------------------------------------------------- i18n / static

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# ---------------------------------------------------------------- logging

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "{levelname} {asctime} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.db.backends": {"level": "WARNING"},
        "engine": {"level": env("ENGINE_LOG_LEVEL", default="INFO"), "propagate": True},
    },
}
