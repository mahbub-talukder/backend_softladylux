"""Order shapes. Customer-facing output is customer-safe (no internal notes, no staff names);
admin output carries everything (ORD-06/08)."""

from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from apps.catalog.models import Category, Product
from apps.core.models import SiteSettings

from .models import Coupon, Order, OrderItem, OrderStatus, OrderStatusLog, Payment, PayStatus

CUSTOMER_STATUS_TEXT = {  # customer-safe wording (ORD-06)
    "draft": "Order placed",
    "confirmed": "Order confirmed",
    "shipping": "On its way",
    "delivered": "Delivered",
    "cancelled": "Cancelled",
    "returned": "Returned",
}
CUSTOMER_PAY_TEXT = {
    PayStatus.PENDING: "Awaiting verification",
    PayStatus.VERIFIED: "Payment verified",
    PayStatus.REJECTED: "Payment could not be verified",  # §11
    PayStatus.COLLECTED: "Paid in cash",
}


class ItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = ["id", "variant", "product_name", "variant_label", "sku", "unit_price",
                  "quantity", "line_total"]


def customer_timeline(order):
    """Only real status changes, in plain wording. Same-status entries are staff-only."""
    return [{"status": log.to_status, "label": CUSTOMER_STATUS_TEXT[log.to_status],
             "at": log.created_at,
             "detail": (f"{order.courier} {order.tracking_ref}".strip()
                        if log.to_status == "shipping" else "")}
            for log in order.logs.all() if log.from_status != log.to_status]


class CustomerOrderSerializer(serializers.ModelSerializer):
    items = ItemSerializer(many=True, read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    payment_status = serializers.SerializerMethodField()
    timeline = serializers.SerializerMethodField()
    can_cancel = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = ["number", "status", "status_label", "placed_at", "name", "phone", "email",
                  "area", "district", "thana", "address_line", "note", "subtotal", "coupon_code",
                  "coupon_discount", "delivery_fee", "total", "payment_method", "payment_status",
                  "courier", "tracking_ref", "items", "timeline", "can_cancel"]

    def get_payment_status(self, o):
        pay = getattr(o, "payment", None)
        return CUSTOMER_PAY_TEXT.get(pay.status, "") if pay else ""

    def get_timeline(self, o):
        return customer_timeline(o)

    def get_can_cancel(self, o):  # ORD-04: only before Shipping
        return o.status in (OrderStatus.DRAFT, OrderStatus.CONFIRMED)


class PaymentSerializer(serializers.ModelSerializer):
    verified_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = ["id", "method", "amount", "sender_number", "trx_id", "status", "verified_by_name",
                  "verified_at", "reject_reason", "note"]

    def get_verified_by_name(self, p):
        return str(p.verified_by) if p.verified_by else None


class LogSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()
    internal = serializers.SerializerMethodField()

    class Meta:
        model = OrderStatusLog
        fields = ["id", "from_status", "to_status", "user", "note", "created_at", "internal"]

    def get_user(self, log):
        return str(log.user) if log.user else None

    def get_internal(self, log):
        return log.from_status == log.to_status


def is_stale(order, site=None) -> bool:
    """ORD-12: a Draft older than the stale-draft hours (Settings, default 24)."""
    site = site or SiteSettings.load()
    return (order.status == OrderStatus.DRAFT
            and order.placed_at < timezone.now() - timedelta(hours=site.stale_draft_hours))


class AdminOrderRowSerializer(serializers.ModelSerializer):
    payment_status = serializers.CharField(source="payment.status", default="", read_only=True)
    trx_id = serializers.CharField(source="payment.trx_id", default=None, read_only=True)
    item_count = serializers.SerializerMethodField()
    is_stale = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = ["id", "number", "status", "placed_at", "name", "phone", "area",
                  "payment_method", "payment_status", "trx_id", "total", "item_count", "is_stale"]

    def get_item_count(self, o):
        return sum(i.quantity for i in o.items.all())

    def get_is_stale(self, o):
        return is_stale(o, self.context.get("site"))


class AdminOrderSerializer(serializers.ModelSerializer):
    items = ItemSerializer(many=True, read_only=True)
    payment = PaymentSerializer(read_only=True)
    logs = LogSerializer(many=True, read_only=True)
    is_stale = serializers.SerializerMethodField()
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    user_name = serializers.SerializerMethodField()

    class Meta:
        model = Order
        exclude = ["idempotency_key", "updated_at", "created_at"]

    def get_is_stale(self, o):
        return is_stale(o)

    def get_user_name(self, o):
        return str(o.user) if o.user else None


class CouponSerializer(serializers.ModelSerializer):
    """CPN-01 / CPN-05. Code is upper-cased; limits are validated here and in the model."""

    categories = serializers.PrimaryKeyRelatedField(many=True, required=False,
                                                    queryset=Category.objects.all())
    products = serializers.PrimaryKeyRelatedField(many=True, required=False,
                                                  queryset=Product.objects.all())
    used_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Coupon
        exclude = ["created_at", "updated_at"]

    def validate_code(self, v):
        v = v.strip().upper()
        if not (4 <= len(v) <= 20 and v.isalnum()):
            raise serializers.ValidationError("Code must be 4–20 letters or digits.")
        qs = Coupon.objects.filter(code=v)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("This code already exists.")
        return v

    def validate(self, d):
        def g(k):
            return d.get(k, getattr(self.instance, k, None))

        if g("value") is not None and g("value") <= 0:
            raise serializers.ValidationError({"value": "Value must be greater than zero."})
        if g("type") == "percent" and g("value") is not None and g("value") > 100:
            raise serializers.ValidationError({"value": "A percentage cannot exceed 100."})
        if g("starts_at") and g("ends_at") and g("ends_at") <= g("starts_at"):
            raise serializers.ValidationError({"ends_at": "End must be after the start."})
        return d
