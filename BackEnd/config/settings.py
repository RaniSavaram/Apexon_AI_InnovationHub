# =========================================================
# DJANGO SETTINGS
# Apexon AI Innovation Hub
# =========================================================

from pathlib import Path
import os


# =========================================================
# BASE DIRECTORY
# =========================================================

BASE_DIR = Path(__file__).resolve().parent.parent


# =========================================================
# ANGULAR BUILD DIRECTORY
# =========================================================

ANGULAR_DIST_DIR = (
    BASE_DIR.parent
    / "FrontEnd"
    / "dist"
    / "frontend"
    / "browser"
)


# =========================================================
# SECURITY
# =========================================================

# Render will provide SECRET_KEY through Environment Variables.
#
# The local fallback allows the application to continue working
# on your laptop before you configure Render.

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-local-development-only"
)


# =========================================================
# DEBUG
# =========================================================

DEBUG = os.environ.get(
    "DEBUG",
    "True"
).lower() == "true"


# =========================================================
# ALLOWED HOSTS
# =========================================================

# LOCAL DEVELOPMENT:
#
# localhost
# 127.0.0.1
#
# RENDER:
#
# Add your Render domain through the ALLOWED_HOSTS
# environment variable.

allowed_hosts_env = os.environ.get(
    "ALLOWED_HOSTS",
    "localhost,127.0.0.1"
)

ALLOWED_HOSTS = [
    host.strip()
    for host in allowed_hosts_env.split(",")
    if host.strip()
]


# =========================================================
# APPLICATIONS
# =========================================================

INSTALLED_APPS = [

    # Django
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # Third-party
    "rest_framework",
    "corsheaders",

    # Application
    "Migrator",
]


# =========================================================
# MIDDLEWARE
# =========================================================

MIDDLEWARE = [

    "django.middleware.security.SecurityMiddleware",

    # WhiteNoise
    "whitenoise.middleware.WhiteNoiseMiddleware",

    "django.contrib.sessions.middleware.SessionMiddleware",

    # CORS should be before CommonMiddleware
    "corsheaders.middleware.CorsMiddleware",

    "django.middleware.common.CommonMiddleware",

    "django.middleware.csrf.CsrfViewMiddleware",

    "django.contrib.auth.middleware.AuthenticationMiddleware",

    "django.contrib.messages.middleware.MessageMiddleware",

    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# =========================================================
# CORS CONFIGURATION
# =========================================================

# During local development:
#
# Angular:
# http://localhost:4200
#
# Django:
# http://127.0.0.1:8000
#
# In production Angular and Django will be served from
# the SAME Render domain, so CORS is not required there.

cors_origins_env = os.environ.get(
    "CORS_ALLOWED_ORIGINS",
    "http://localhost:4200"
)

CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in cors_origins_env.split(",")
    if origin.strip()
]


# IMPORTANT:
# Do NOT use:
#
# CORS_ALLOW_ALL_ORIGINS = True
#
# because this application will be publicly accessible.


# =========================================================
# CSRF TRUSTED ORIGINS
# =========================================================

csrf_origins_env = os.environ.get(
    "CSRF_TRUSTED_ORIGINS",
    ""
)

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in csrf_origins_env.split(",")
    if origin.strip()
]


# =========================================================
# URL CONFIGURATION
# =========================================================

ROOT_URLCONF = "config.urls"


# =========================================================
# TEMPLATES
# =========================================================

TEMPLATES = [

    {
        "BACKEND":
            "django.template.backends.django.DjangoTemplates",

        "DIRS": [
            BASE_DIR.parent
            / "FrontEnd"
            / "dist"
            / "frontend"
            / "browser"
        ],

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


# =========================================================
# WSGI
# =========================================================

WSGI_APPLICATION = "config.wsgi.application"


# =========================================================
# DATABASE
# =========================================================

# For now we are keeping your existing SQLite database.
#
# IMPORTANT:
# Render's filesystem is ephemeral.
# Therefore SQLite should NOT be treated as a permanent
# production database.
#
# We will decide whether you need PostgreSQL later.

DATABASES = {

    "default": {

        "ENGINE":
            "django.db.backends.sqlite3",

        "NAME":
            BASE_DIR / "db.sqlite3",

    }

}


# =========================================================
# PASSWORD VALIDATION
# =========================================================

AUTH_PASSWORD_VALIDATORS = []


# =========================================================
# LANGUAGE
# =========================================================

LANGUAGE_CODE = "en-us"


# =========================================================
# TIME ZONE
# =========================================================

TIME_ZONE = "Asia/Kolkata"


# =========================================================
# INTERNATIONALIZATION
# =========================================================

USE_I18N = True

USE_TZ = True


# =========================================================
# STATIC FILES
# =========================================================

STATIC_URL = "/static/"


# Django static files directory

STATICFILES_DIRS = [
    BASE_DIR.parent
    / "FrontEnd"
    / "dist"
    / "frontend"
    / "browser"
]


# Directory used by collectstatic

STATIC_ROOT = BASE_DIR / "staticfiles"


# =========================================================
# WHITENOISE
# =========================================================

# Angular generates files such as:
#
# index.html
# main-xxxxx.js
# styles-xxxxx.css
# polyfills-xxxxx.js
#
# We want Django/WhiteNoise to serve these files.

if ANGULAR_DIST_DIR.exists():

    WHITENOISE_ROOT = ANGULAR_DIST_DIR

else:

    WHITENOISE_ROOT = None


# =========================================================
# WHITENOISE STATIC STORAGE
# =========================================================

STORAGES = {

    "default": {

        "BACKEND":
            "django.core.files.storage.FileSystemStorage",

    },

    "staticfiles": {

        "BACKEND":
            "whitenoise.storage.CompressedManifestStaticFilesStorage",

    },

}


# =========================================================
# DEFAULT PRIMARY KEY
# =========================================================

DEFAULT_AUTO_FIELD = (
    "django.db.models.BigAutoField"
)


# =========================================================
# PRODUCTION SECURITY SETTINGS
# =========================================================

if not DEBUG:

    # HTTPS will be used by Render.

    SECURE_PROXY_SSL_HEADER = (
        "HTTP_X_FORWARDED_PROTO",
        "https"
    )

    SECURE_SSL_REDIRECT = True

    SESSION_COOKIE_SECURE = True

    CSRF_COOKIE_SECURE = True

    SECURE_HSTS_SECONDS = 31536000

    SECURE_HSTS_INCLUDE_SUBDOMAINS = True

    SECURE_HSTS_PRELOAD = True