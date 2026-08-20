
from django.contrib import admin
from django.urls import path, re_path
from django.views.generic import TemplateView
from Migrator import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/scan/", views.Db_Scanner),
    path("api/connect/", views.connect_database),
    path("api/connection/", views.saved_connection),
    path("api/connections/", views.saved_connections),
    path("output/<str:filename>", views.serve_output_file),
    re_path(r"^.*$", TemplateView.as_view(template_name="index.html")),
]