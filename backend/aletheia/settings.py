"""Django settings for Aletheia.

Everything environment-specific comes from environment variables so the same image runs
locally (SQLite, DEBUG) and on Fly.io (Postgres, DEBUG off).
"""
import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent
CORPUS_DIR = Path(os.environ.get("ALETHEIA_CORPUS_DIR", REPO_ROOT / "corpus"))
FRONTEND_DIST = Path(os.environ.get("ALETHEIA_FRONTEND_DIST", REPO_ROOT / "frontend" / "dist"))
# Full text of readings (sanitized HTML and source PDFs), filled by `manage.py warm_content`.
# Kept out of git and out of the Docker image; see content/store.py.
CONTENT_CACHE_DIR = Path(os.environ.get("ALETHEIA_CONTENT_CACHE", BASE_DIR / "content_cache"))
# Folders of openly licensed PDFs the warm command copies from: each has a manifest.json written by
# tools/import_corpus.py or tools/mirror_pdfs.py, or files named <reading-id>.pdf. Separate with os.pathsep.
CONTENT_PDF_DIRS = [Path(p) for p in os.environ.get("ALETHEIA_PDF_DIRS", "").split(os.pathsep) if p] or [
    CORPUS_DIR / "files" / "drive", CORPUS_DIR / "files" / "web"]

DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
# The Docker image has the corpus/files manifests but not the PDFs; warm_content fetches a listed
# PDF the disk lacks from here, by its path under corpus/files (content/repo_pdfs.py). Empty: off.
CONTENT_PDF_BASE_URL = os.environ.get(
    "ALETHEIA_PDF_BASE_URL", "" if DEBUG else "https://raw.githubusercontent.com/justalewis/aletheia/main/corpus/files/")
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-not-secret" if DEBUG else "")
if not SECRET_KEY:
    raise RuntimeError("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is off")

ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
CSRF_TRUSTED_ORIGINS = [o for o in os.environ.get("DJANGO_CSRF_ORIGINS", "").split(",") if o]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "catalog",
    "builder",
    "content",
    "programs",
]

MIDDLEWARE = [
    "aletheia.middleware.HealthCheckMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "aletheia.ratelimit.ApiGuardMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "aletheia.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "aletheia.wsgi.application"

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/Los_Angeles"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# The built React app is served from the site root by WhiteNoise.
WHITENOISE_ROOT = FRONTEND_DIST if FRONTEND_DIST.exists() else None
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
    # Swap for object storage (django-storages) in production; content/store.py only uses the Storage API.
    "content": {"BACKEND": "django.core.files.storage.FileSystemStorage",
                "OPTIONS": {"location": CONTENT_CACHE_DIR, "allow_overwrite": True}},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---- public-site hardening (see aletheia/ratelimit.py)
# The database cache keeps rate-limit counts shared across gunicorn workers and machines.
# Its table comes from `manage.py createcachetable` (the Fly release command runs it).
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "aletheia_cache",
        "OPTIONS": {"MAX_ENTRIES": 50_000},
    }
}
RATE_LIMIT_ENABLED = os.environ.get("ALETHEIA_RATE_LIMIT", "1") == "1"
RATE_LIMIT_WINDOW = int(os.environ.get("ALETHEIA_RATE_WINDOW", 3600))  # seconds; fixed windows
# Requests per client IP per window, by rule (0 turns a rule off). Override one with its
# environment variable, e.g. ALETHEIA_RATE_EXPORT_PDF=40.
RATE_LIMITS = {rule: int(os.environ.get("ALETHEIA_RATE_" + rule.upper().replace("-", "_"), default)) for rule, default in {
    "build-create": 30,    # POST /api/builds
    "build-update": 300,   # PUT /api/builds/<id>
    "export-pdf": 20,      # POST /api/export/pdf
    "export": 60,          # other POST /api/export/*
    "plan": 300,           # POST /api/plan
    "programs": 20,        # writes under /api/programs
    "write": 600,          # every other POST/PUT/PATCH/DELETE under /api/
    "read": 2000,          # GET/HEAD under /api/
}.items()}
MAX_API_BODY_BYTES = int(os.environ.get("ALETHEIA_MAX_BODY_BYTES", 512 * 1024))
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_API_BODY_BYTES

# Headers that are safe with the React app. No Content-Security-Policy yet: a strict one would
# need auditing against the built bundle first.
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # Fly's proxy already redirects http to https (force_https in fly.toml). HSTS starts modest;
    # raise it once the site has run cleanly over https for a while.
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", 7 * 24 * 3600))
