"""Courier tables. SRS v1.7 §6 courier_*; CRR-03..CRR-13."""

from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class Attention(models.TextChoices):
    """CRR-05 / CRR-09 / CRR-11: why staff should look at a Steadfast parcel. Blank = all fine."""

    NONE = "", "—"
    BOOKING_FAILED = "booking_failed", "Booking failed"
    HOLD = "hold", "On hold at courier"
    PARTIAL = "partial", "Partly delivered"
    RETURNING = "returning", "Courier returning parcel"
    RETURNED_TO_MERCHANT = "returned_to_merchant", "Parcel back with you"
    EXCEPTIONAL = "exceptional", "Lost or damaged at courier"


class Shipment(TimeStampedModel):
    """CRR-03: one Steadfast parcel per order. A row with no consignment_id is a failed booking
    (CRR-05) and holds the error so staff can see it and retry."""

    order = models.OneToOneField("shop.Order", on_delete=models.CASCADE, related_name="shipment")
    consignment_id = models.BigIntegerField(null=True, blank=True, unique=True)
    tracking_code = models.CharField(max_length=40, blank=True)
    tracking_link = models.URLField(blank=True)
    courier_status = models.CharField(max_length=60, blank=True)  # raw Steadfast value
    status_at = models.DateTimeField(null=True, blank=True)
    attention = models.CharField(max_length=24, choices=Attention.choices, blank=True, default="")
    last_error = models.CharField(max_length=1000, blank=True)
    booked_at = models.DateTimeField(null=True, blank=True)
    booked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="+")
    last_event_at = models.DateTimeField(null=True, blank=True)  # CRR-11: last webhook/poll news

    class Meta:
        indexes = [models.Index(fields=["attention"])]

    @property
    def is_booked(self) -> bool:
        return self.consignment_id is not None


class CourierEvent(TimeStampedModel):
    """CRR-10: a courier status change or tracking step, stored once whatever its source."""

    class Kind(models.TextChoices):
        STATUS = "status"
        TRACKING = "tracking"

    class Source(models.TextChoices):
        BOOKING = "booking"
        WEBHOOK = "webhook"
        POLL = "poll"

    order = models.ForeignKey("shop.Order", on_delete=models.CASCADE, related_name="courier_events")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    status = models.CharField(max_length=60, blank=True)
    message = models.CharField(max_length=500, blank=True)
    occurred_at = models.DateTimeField()
    source = models.CharField(max_length=10, choices=Source.choices)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["order", "kind", "message", "occurred_at"],
                                               name="courier_event_once")]


class WebhookReceipt(TimeStampedModel):
    """CRR-08: every accepted webhook call, keyed by Steadfast's Idempotency-Key."""

    idempotency_key = models.CharField(max_length=200, unique=True)
    notification_type = models.CharField(max_length=40, blank=True)
    payload = models.JSONField(default=dict)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-received_at"]


class PickupRequest(TimeStampedModel):
    """CRR-13: pickups sent from this system (Steadfast has no list endpoint for them)."""

    address_id = models.PositiveIntegerField()
    police_station_id = models.PositiveIntegerField()
    address = models.CharField(max_length=255)
    contact_number = models.CharField(max_length=11)
    note = models.CharField(max_length=500, blank=True)
    estim_qty = models.PositiveIntegerField(null=True, blank=True)
    steadfast_id = models.BigIntegerField(null=True, blank=True)
    status = models.CharField(max_length=40, blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at", "-id"]
