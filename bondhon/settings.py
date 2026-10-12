"""
Bondhon Management and Auditing System - Django settings.

Configuration comes from environment variables (optionally loaded from a
`.env` file next to manage.py).  Defaults are safe for local development.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# Tiny .env loader (no third-party dependency)
# --------------------------------------------------------------------------
def _load_env_file(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file(BASE_DIR / ".env")


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def env_list(name, default=""):
    return [v.strip() for v in os.environ.get(name, default).split(",") if v.strip()]


# --------------------------------------------------------------------------
# Core
# --------------------------------------------------------------------------
DEBUG = env_bool("DJANGO_DEBUG", True)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "dev-only-insecure-key-change-me"
    else:
        raise RuntimeError("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is off.")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1" if DEBUG else "")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    # Bondhon apps
    "myapp",        # core: members, deposits, expenses, plans, reports
    "audit",        # audit trail, engagements, findings, analytics, period lock
    "messaging",    # centralised SMS: templates, outbox, campaigns, auto-messages
    "payments",     # client deposits / online payments
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "audit.middleware.AuditContextMiddleware",
    "myapp.middleware.ForcePasswordChangeMiddleware",
]

ROOT_URLCONF = "bondhon.urls"

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
                "myapp.context_processors.bondhon",
            ],
        },
    },
]

WSGI_APPLICATION = "bondhon.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / os.environ.get("DJANGO_DB_NAME", "db.sqlite3"),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --------------------------------------------------------------------------
# Locale
# --------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Dhaka"
USE_I18N = True
USE_TZ = True
DATE_FORMAT = "d-m-Y"
DATETIME_FORMAT = "d-m-Y h:i A"
USE_THOUSAND_SEPARATOR = False  # we format money explicitly in templates

# --------------------------------------------------------------------------
# Static / media
# --------------------------------------------------------------------------
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# --------------------------------------------------------------------------
# Auth / sessions / security
# --------------------------------------------------------------------------
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 7  # one week
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
# Secure cookies only when served over HTTPS (set DJANGO_HTTPS=1 in production).
HTTPS = env_bool("DJANGO_HTTPS", not DEBUG)
SESSION_COOKIE_SECURE = HTTPS
CSRF_COOKIE_SECURE = HTTPS
if HTTPS:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "0"))
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# --------------------------------------------------------------------------
# Bondhon-specific configuration (secrets come from the environment)
# --------------------------------------------------------------------------
# Public base URL used to build gateway callback URLs (no trailing slash).
SITE_BASE_URL = os.environ.get("BONDHON_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

# SMS provider (used when SiteSetting.sms_backend == "http").
# Params are a JSON object; {to}, {message}, {sender}, {api_key}, {username}, {password}
# are substituted.  See README for a BulkSMSBD-style example.
SMS_HTTP_URL = os.environ.get("SMS_HTTP_URL", "")
SMS_HTTP_METHOD = os.environ.get("SMS_HTTP_METHOD", "POST").upper()
SMS_HTTP_PARAMS = os.environ.get("SMS_HTTP_PARAMS", "")
SMS_HTTP_API_KEY = os.environ.get("SMS_HTTP_API_KEY", "")
SMS_HTTP_USERNAME = os.environ.get("SMS_HTTP_USERNAME", "")
SMS_HTTP_PASSWORD = os.environ.get("SMS_HTTP_PASSWORD", "")
SMS_HTTP_SUCCESS_MARKER = os.environ.get("SMS_HTTP_SUCCESS_MARKER", "")  # substring that must appear in a successful reply
SMS_HTTP_TIMEOUT = int(os.environ.get("SMS_HTTP_TIMEOUT", "15"))

# Online payment gateways
PAYMENT_SANDBOX_ENABLED = env_bool("PAYMENT_SANDBOX_ENABLED", DEBUG)  # fake gateway, never enable in production
SSLCOMMERZ_STORE_ID = os.environ.get("SSLCOMMERZ_STORE_ID", "")
SSLCOMMERZ_STORE_PASSWORD = os.environ.get("SSLCOMMERZ_STORE_PASSWORD", "")
SSLCOMMERZ_SANDBOX = env_bool("SSLCOMMERZ_SANDBOX", True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {
        "bondhon": {"handlers": ["console"], "level": "INFO"},
    },
}
