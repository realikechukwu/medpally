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

SITE_NAME = env("SITE_NAME", default="MedPally")
SITE_BASE_URL = env("SITE_BASE_URL", default="http://localhost:8000")
MARKETING_BASE_URL = env("MARKETING_BASE_URL", default="https://web.medpally.com")
MARKETING_HOSTS = frozenset(
    env.list(
        "MARKETING_HOSTS",
        default=["web.medpally.com", "medpally.com", "www.medpally.com"],
    )
)
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
    "apps.common.middleware.MarketingSiteMiddleware",
    "apps.accounts.middleware.OnboardingMiddleware",
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
# A clinician should not have to authenticate each time they return to the
# service. The cookie is still invalidated on logout or password change.
SESSION_COOKIE_AGE = 60 * 60 * 24 * 90
SESSION_EXPIRE_AT_BROWSER_CLOSE = False

# allauth (65.x style settings — ACCOUNT_AUTHENTICATION_METHOD and
# ACCOUNT_EMAIL_REQUIRED are deprecated).
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_SIGNUP_REDIRECT_URL = "/onboarding/"
ACCOUNT_EMAIL_VERIFICATION = "optional"
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_SESSION_REMEMBER = True

# Without this allauth derives the prefix from django.contrib.sites, which
# ships a Site row of "example.com" — every verification mail then arrives
# titled "[example.com] ...", which reads as phishing and scores as spam.
# apps.accounts.apps also rewrites that row; this makes the subject correct
# even before the first migrate has run.
ACCOUNT_EMAIL_SUBJECT_PREFIX = f"[{SITE_NAME}] "

# A resend that silently does nothing is worse than one that fails loudly, but
# a signup must not 500 because the mail relay is down — the account is already
# created by then. The adapter logs the failure and flags the request instead,
# and apps.accounts.views.resend_verification reads that flag to tell the user
# the truth rather than an unconditional "email sent".
ACCOUNT_ADAPTER = "apps.accounts.adapters.AccountAdapter"

# Our User has no username column at all (email is USERNAME_FIELD). Without
# this, allauth still resolves its default "username" field when building the
# signup form and the page 500s with FieldDoesNotExist — signup, and only
# signup, completely broken.
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
SOCIALACCOUNT_ADAPTER = "apps.accounts.adapters.SocialAccountAdapter"
SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True
GOOGLE_OAUTH_CLIENT_ID = env("GOOGLE_OAUTH_CLIENT_ID", default="")
GOOGLE_OAUTH_CLIENT_SECRET = env("GOOGLE_OAUTH_CLIENT_SECRET", default="")

# The provider stays installed unconditionally so its URLs always reverse (the
# login template reverses them, and a half-configured deploy should render
# rather than 500). Whether the button is *shown* is a separate question,
# answered by GOOGLE_OAUTH_ENABLED — allauth happily lists a provider whose
# client_id is the empty string, and that button can only fail.
GOOGLE_OAUTH_ENABLED = bool(GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET)

SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "APP": {
            "client_id": GOOGLE_OAUTH_CLIENT_ID,
            "secret": GOOGLE_OAUTH_CLIENT_SECRET,
            "key": "",
        },
        "SCOPE": ["profile", "email"],
        # offline would hand us a refresh token we have no use for: we read the
        # profile once at signup and never call Google again on the user's
        # behalf. Fewer stored credentials, smaller consent screen.
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
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="MedPally <noreply@medpally.com>")

# Django defaults to no socket timeout at all, so an unresponsive relay blocks
# the worker thread that is sending until gunicorn's own timeout kills it. Two
# workers of two threads do not have many to spare, and nothing about signup
# should wait on Brevo for longer than a reader will.
EMAIL_TIMEOUT = env.int("EMAIL_TIMEOUT", default=10)

# ---------------------------------------------------------------- engine

PUBMED_EMAIL = env("NCBI_EMAIL", default="")
PUBMED_API_KEY = env("NCBI_API_KEY", default="")
PUBMED_TOOL_NAME = env("PUBMED_TOOL_NAME", default="medpally")

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
