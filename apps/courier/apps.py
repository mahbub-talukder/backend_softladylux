from django.apps import AppConfig


class CourierConfig(AppConfig):
    """SRS v1.7 §4.18 (CRR): Steadfast courier booking, webhook, reconciliation and admin pages."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.courier"
