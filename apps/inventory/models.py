from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class Reason(models.TextChoices):
    ORDER_CONFIRMED = "order_confirmed"
    ORDER_CANCELLED = "order_cancelled"
    ORDER_RETURNED = "order_returned"
    ADJUSTMENT_IN = "adjustment_in"
    ADJUSTMENT_OUT = "adjustment_out"
    OPENING_STOCK = "opening_stock"


class StockMovement(TimeStampedModel):
    """SRS INV-08 / §6 inventory_stockmovement: immutable log of every on-hand change.
    Rows are never edited or deleted (rule 11). `order_ref` becomes an Order FK in M3."""

    variant = models.ForeignKey("catalog.Variant", on_delete=models.PROTECT,
                                related_name="movements")
    delta_on_hand = models.IntegerField()
    on_hand_after = models.PositiveIntegerField()
    reason = models.CharField(max_length=20, choices=Reason.choices)
    order_ref = models.CharField(max_length=40, blank=True)
    lot_reference = models.CharField(max_length=80, blank=True)
    remarks = models.CharField(max_length=300, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["variant", "created_at"])]  # §6 index

    def save(self, *args, **kwargs):
        if self.pk:
            raise RuntimeError("Stock movements are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("Stock movements are never deleted.")
