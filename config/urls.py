from django.conf import settings
from django.contrib import admin
from django.urls import include, path

# SRS ARC-05 (/api/v1/), §2.1 (Django admin at an obscure developer-only path).
urlpatterns = [
    path(f"api/{settings.ADMIN_URL}/", admin.site.urls),
    path("api/v1/admin/", include("apps.catalog.admin_urls")),
    path("api/v1/admin/", include("apps.shop.admin_urls")),
    path("api/v1/admin/", include("apps.core.admin_urls")),
    path("api/v1/", include("apps.core.urls")),
    path("api/v1/", include("apps.catalog.urls")),
    path("api/v1/", include("apps.shop.urls")),
]
