from django.contrib import admin
from django.urls import path, re_path
from django.views.generic import TemplateView
from django.views.static import serve
from Migrator import views
from pathlib import Path
from django.conf import settings


FRONTEND_DIR = (
    Path(settings.BASE_DIR).parent
    / "FrontEnd"
    / "dist"
    / "frontend"
    / "browser"
)


urlpatterns = [
    path("admin/", admin.site.urls),

    path("api/scan/", views.Db_Scanner),
    path("api/connect/", views.connect_database),

    re_path(
        r"^assets/(?P<path>.*)$",
        serve,
        {
            "document_root": FRONTEND_DIR / "assets"
        },
    ),

    re_path(
        r"^(?P<path>.*\.(?:js|css|ico|png|jpg|jpeg|svg|woff|woff2|ttf|webp|json))$",
        serve,
        {
            "document_root": FRONTEND_DIR
        },
    ),

    re_path(
        r"^.*$",
        TemplateView.as_view(template_name="index.html")
    ),
]
