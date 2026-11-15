from django.urls import path

from .views_public import SteadfastWebhookView

# SRS v1.7 §7: public POST webhooks/steadfast/ (CRR-08).
urlpatterns = [
    path("webhooks/steadfast/", SteadfastWebhookView.as_view(), name="steadfast-webhook"),
]
