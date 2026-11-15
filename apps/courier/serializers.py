"""SRS v1.7 CRR-09/10/13/16 payloads."""

from rest_framework import serializers

from .models import CourierEvent, PickupRequest, Shipment


class ShipmentSerializer(serializers.ModelSerializer):
    """Staff view of a Steadfast parcel (order detail courier block, CRR-05/09)."""

    attention_label = serializers.CharField(source="get_attention_display", read_only=True)
    is_booked = serializers.BooleanField(read_only=True)

    class Meta:
        model = Shipment
        fields = ["consignment_id", "tracking_code", "tracking_link", "courier_status", "status_at",
                  "attention", "attention_label", "last_error", "booked_at", "is_booked"]


class CourierEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourierEvent
        fields = ["kind", "status", "message", "occurred_at", "source"]


def customer_courier_steps(order) -> list[dict]:
    """CRR-16: courier steps for customers, newest first, plain wording, no internal codes."""
    from .services import customer_status_text

    steps = []
    for e in order.courier_events.all():
        text = e.message if e.kind == CourierEvent.Kind.TRACKING else (
            customer_status_text(e.status) or e.message)
        if text:
            steps.append({"text": text, "at": e.occurred_at})
    return steps


class PickupSerializer(serializers.ModelSerializer):
    """CRR-13 pickup form. Phone: 11 digits starting 013–019 (Steadfast's rule)."""

    contact_number = serializers.RegexField(r"^01[3-9]\d{8}$", error_messages={
        "invalid": "Use an 11-digit mobile number starting 013–019."})
    requested_by_name = serializers.CharField(source="requested_by.full_name", read_only=True,
                                              default=None)

    class Meta:
        model = PickupRequest
        fields = ["id", "address_id", "police_station_id", "address", "contact_number", "note",
                  "estim_qty", "steadfast_id", "status", "requested_by_name", "created_at"]
        read_only_fields = ["steadfast_id", "status", "created_at"]
