from django.urls import path

from . import views_public as p
from .views import HealthView

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("settings/", p.PublicSettingsView.as_view(), name="public-settings"),
    path("theme/", p.PublicThemeView.as_view(), name="public-theme"),
    path("home/", p.HomeView.as_view(), name="public-home"),
    path("pages/<slug:slug>/", p.PublicPageView.as_view(), name="public-page"),
]
