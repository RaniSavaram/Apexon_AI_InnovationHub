"""
Django settings for the ApexonAiInnovationHub backend.

This backend's job (for now) is to serve the built Angular app
(from ../frontend/dist/frontend/browser) and, later, host API endpoints
under /api/.
"""
import os

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Path to the Angular production build output (adjust if you change
# angular.json's outputPath)
ANGULAR_DIST_DIR = BASE_DIR.parent / "FrontEnd" / "dist" / "frontend" / "browser"

# --- Security ---------------------------------------------------------
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "django-insecure-local-only")
DEBUG = os.environ.get("DJANGO_DEBUG", "False").lower() == "true"
ALLOWED_HOSTS = [
    "apexon-ai-innovationhub-new.onrender.com",
]

# --- Applications -------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    'Migrator',
    'rest_framework',
    "corsheaders",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "corsheaders.middleware.CorsMiddleware",
]

CORS_ALLOWED_ORIGINS = [
    "https://apexon-ai-innovationhub-new.onrender.com",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # Django needs to find Angular's index.html here
        "DIRS": [ANGULAR_DIST_DIR],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
CSRF_TRUSTED_ORIGINS = [
    "https://apexon-ai-innovationhub-new.onrender.com",
]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# --- Static files (Angular's JS/CSS/assets) -----------------------------
# Angular's index.html has <base href="/"> and references its JS/CSS bundles
# as root-relative files (e.g. "/main-XXXX.js"), not under a "/static/"
# prefix. WhiteNoise's WHITENOISE_ROOT serves a folder's contents straight
# from "/", which matches that without needing to touch Angular's config.
STATIC_URL = "static/"
STATICFILES_DIRS = []
WHITENOISE_ROOT = ANGULAR_DIST_DIR if ANGULAR_DIST_DIR.exists() else None

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"