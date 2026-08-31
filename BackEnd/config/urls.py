
from django.contrib import admin
from django.urls import path, re_path
from django.views.generic import TemplateView
from Migrator import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/scan/", views.Db_Scanner),
    path("api/scan-status/<uuid:scan_id>/", views.scan_status),
    path("api/connect/", views.connect_database),
    path("api/connection/", views.saved_connection),
    path("api/connections/", views.saved_connections),
    path("api/debug/", views.debug_view),
    path("api/generate-fabric-artifacts/", views.generate_fabric_artifacts),
    path("output/<str:filename>", views.serve_generated_document, name="generated-document"),
    re_path(r"^.*$", TemplateView.as_view(template_name="index.html")),
]