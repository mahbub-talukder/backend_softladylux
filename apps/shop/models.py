"""Orders, payments and coupons. SRS §6 shop_*; ORD, PAY, CPN, CHK; business rules 3–11."""

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.models import TimeStampedModel


class OrderStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    CONFIRMED = "confirmed", "Confirmed"
    SHIPPING = "shipping", "Shipping"
    DELIVERED = "delivered", "Delivered"
    CANCELLED = "cancelled", "Cancelled"
    RETURNED = "returned", "Returned"


class Area(models.TextChoices):
    INSIDE = "inside_dhaka", "Inside Dhaka"
    OUTSIDE = "outside_dhaka", "Outside Dhaka"


class PayMethod(models.TextChoices):
    BKASH = "bkash", "bKash"
    NAGAD = "nagad", "Nagad"
    COD = "cod", "Cash on Delivery"


class PayStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    VERIFIED = "verified", "Verified"
    REJECTED = "rejected", "Rejected"
    COLLECTED = "collected", "Collected"


class CouponType(models.TextChoices):
    PERCENT = "percent", "Percent"
    FIXED = "fixed", "Fixed BDT"


class CouponScope(models.TextChoices):
    ALL = "all", "All products"
    CATEGORIES = "categories", "Selected categories"
    PRODUCTS = "products", "Selected products"


class Coupon(TimeStampedModel):
    """CPN-01: code stored upper-case (unique, case-insensitive). `used_count` is counted when
    an order is placed and released on Cancel (CPN-04)."""

    code = models.CharField(max_length=20, unique=True)
    description = models.CharField(max_length=200, blank=True)
    type = models.CharField(max_length=8, choices=CouponType.choices)
    value = models.DecimalField(max_digits=10, decimal_places=2)
    max_discount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    min_subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    usage_limit = models.PositiveIntegerField(null=True, blank=True)
    per_customer_limit = models.PositiveIntegerField(null=True, blank=True)
    scope = models.CharField(max_length=10, choices=CouponScope.choices, default=CouponScope.ALL)
    categories = models.ManyToManyField("catalog.Category", blank=True, related_name="+")
    products = models.ManyToManyField("catalog.Product", blank=True, related_name="+")
    includes_discounted = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    used_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at", "-id"]

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.code


class WishlistItem(TimeStampedModel):
    """SRS §6 shop_wishlistitem, ACC-07: stored in the database for logged-in customers."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="wishlist")
    product = models.ForeignKey("catalog.Product", on_delete=models.CASCADE, related_name="+")

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["user", "product"],
                                               name="uniq_wishlist_user_product")]


class OrderSequence(models.Model):
    """CHK-08: one row per day (Asia/Dhaka) holding the last used sequence number."""

    day = models.DateField(unique=True)
    last = models.PositiveIntegerField(default=0)


class Order(TimeStampedModel):
    """§6 shop_order. Totals, prices and the address are snapshots frozen at placement (rule 10).
    Orders are never hard-deleted (rule 11)."""

    number = models.CharField(max_length=20, unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="orders")
    status = models.CharField(max_length=10, choices=OrderStatus.choices,
                              default=OrderStatus.DRAFT)
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=11)
    email = models.EmailField(blank=True)
    area = models.CharField(max_length=14, choices=Area.choices)
    district = models.CharField(max_length=80)
    thana = models.CharField(max_length=80)
    address_line = models.CharField(max_length=300)
    note = models.CharField(max_length=500, blank=True)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    coupon_code = models.CharField(max_length=20, blank=True)
    coupon_discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2)
    total = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=5, choices=PayMethod.choices)
    courier = models.CharField(max_length=80, blank=True)
    tracking_ref = models.CharField(max_length=80, blank=True)
    placed_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=300, blank=True)
    returned_at = models.DateTimeField(null=True, blank=True)
    return_reason = models.CharField(max_length=300, blank=True)
    idempotency_key = models.CharField(max_length=64, unique=True, null=True, blank=True)

    class Meta:
        ordering = ["-placed_at", "-id"]
        indexes = [models.Index(fields=["status", "placed_at"])]  # §6 indexes

    def __str__(self):
        return self.number


class OrderItem(TimeStampedModel):
    """Line snapshot: name, label, SKU and price are copied so later edits never change history."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey("catalog.Variant", on_delete=models.PROTECT, related_name="+")
    product_name = models.CharField(max_length=160)
    variant_label = models.CharField(max_length=80, blank=True)
    sku = models.CharField(max_length=40)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveSmallIntegerField()
    line_total = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ["id"]


class OrderStatusLog(TimeStampedModel):
    """ORD-06: who, when, old and new status, note. Entries with from == to are staff-only
    events (internal notes, edits, payment verify/reject) and never shown to customers."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="logs")
    from_status = models.CharField(max_length=10, blank=True)
    to_status = models.CharField(max_length=10)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="+")
    note = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["created_at", "id"]


class Payment(TimeStampedModel):
    """PAY-01. One payment per order. A TrxID is unique per method (rule 5, PAY-05)."""

    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="payment")
    method = models.CharField(max_length=5, choices=PayMethod.choices)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    sender_number = models.CharField(max_length=11, blank=True)
    trx_id = models.CharField(max_length=12, null=True, blank=True)  # noqa: DJ001 (NULL for COD: §6)
    status = models.CharField(max_length=10, choices=PayStatus.choices, default=PayStatus.PENDING)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    verified_at = models.DateTimeField(null=True, blank=True)
    reject_reason = models.CharField(max_length=300, blank=True)
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=["method", "trx_id"], condition=Q(trx_id__isnull=False),
            name="uniq_trx_per_method")]  # §6, rule 5
