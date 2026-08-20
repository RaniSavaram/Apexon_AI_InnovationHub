from django.contrib import admin
from django.urls import path, re_path
from django.views.generic import TemplateView
from django.views.static import serve
from Migrator import views
from pathlib import Path
from django.conf import settings


# ============================================================
# ANGULAR FRONTEND DIRECTORY
# ============================================================

FRONTEND_DIR = (
    Path(settings.BASE_DIR).parent
    / "FrontEnd"
    / "dist"
    / "frontend"
    / "browser"
)


# ============================================================
# URL PATTERNS
# ============================================================

urlpatterns = [

    # Django Admin
    path(
        "admin/",
        admin.site.urls
    ),

    # ========================================================
    # API ENDPOINTS
    # ========================================================

    path(
        "api/scan/",
        views.Db_Scanner
    ),

    path(
        "api/connect/",
        views.connect_database
    ),

    # ========================================================
    # ANGULAR ASSETS
    # ========================================================

    re_path(
        r"^assets/(?P<path>.*)$",
        serve,
        {
            "document_root": FRONTEND_DIR / "assets"
        },
    ),

    # ========================================================
    # ANGULAR JS / CSS / IMAGES / FONTS
    # ========================================================

    re_path(
        r"^(?P<path>.*\.(?:js|css|ico|png|jpg|jpeg|svg|woff|woff2|ttf|webp|json))$",
        serve,
        {
            "document_root": FRONTEND_DIR
        },
    ),

    # ========================================================
    # ANGULAR APPLICATION
    # ========================================================
    #
    # Angular handles client-side routing.
    # Any URL that is not an API/admin/static URL
    # should return index.html.
    #

    re_path(
        r"^.*$",
        TemplateView.as_view(
            template_name="index.html"
        ),
    ),
]