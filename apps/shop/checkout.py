"""Checkout. SRS CHK-01..CHK-10, CRT-09, rules 2, 5, 10: one transaction that validates every line
against the current price and on-hand stock, creates a Draft with a unique order number,
snapshots prices/address/fees/coupon, creates the Pending payment, redeems the coupon.
Stock is checked but never held or deducted until Confirm (SD-07)."""

import re
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.catalog.models import Variant
from apps.core.exceptions import Conflict
from apps.core.models import SiteSettings
from apps.core.phone import normalize_phone

from . import cart, coupons
from .models import (
    Area,
    Coupon,
    Order,
    OrderItem,
    OrderSequence,
    OrderStatus,
    Payment,
    PayMethod,
    PayStatus,
)
from .orders import add_log

TRX_RE = re.compile(r"^[A-Z0-9]{8,12}$")


def clean_phone(value, field="phone"):
    try:
        phone = normalize_phone(value)
    except ValueError as e:
        raise ValidationError({field: str(e)}) from None
    if not phone:
        raise ValidationError({field: "This field is required."})
    return phone


def clean_sender(value):
    return clean_phone(value, "sender_number")


def clean_trx(value, method, exclude_payment=None):
    """CHK-05: alphanumeric 8–12, upper-cased, unique per method."""
    trx = (value or "").strip().upper()
    if not TRX_RE.match(trx):
        raise ValidationError({"trx_id": "Transaction ID must be 8–12 letters or digits."})
    taken = Payment.objects.filter(method=method, trx_id=trx)
    if exclude_payment:
        taken = taken.exclude(pk=exclude_payment)
    if taken.exists():
        raise ValidationError({"trx_id": "This transaction ID has already been used."})
    return trx


def delivery_fee(site: SiteSettings, area: str) -> Decimal:
    return (site.delivery_fee_inside_dhaka if area == Area.INSIDE
            else site.delivery_fee_outside_dhaka)


def method_enabled(site: SiteSettings, method: str) -> bool:
    """§11 'Settings missing': bKash/Nagad are hidden when their number is empty."""
    return {PayMethod.COD: site.cod_enabled, PayMethod.BKASH: bool(site.bkash_number),
            PayMethod.NAGAD: bool(site.nagad_number)}[method]


def next_number() -> str:
    """CHK-08: SLL-YYMMDD-NNNN, a daily sequence that is never reused."""
    site = SiteSettings.load()
    today = timezone.localdate()
    seq, _ = OrderSequence.objects.select_for_update().get_or_create(day=today)
    seq.last += 1
    seq.save(update_fields=["last"])
    return f"{site.order_number_prefix}-{today:%y%m%d}-{seq.last:04d}"


def _required(data, *keys):
    errors = {k: "This field is required." for k in keys if not str(data.get(k, "")).strip()}
    if errors:
        raise ValidationError(errors)


def price_lines(raw_lines, lock=False):
    """Validate lines against current price and on-hand stock; returns (rows, subtotal)."""
    if not raw_lines or len(raw_lines) > cart.MAX_LINES:
        raise ValidationError({"lines": f"Your bag needs 1–{cart.MAX_LINES} lines."})
    merged: dict[int, int] = {}
    for r in raw_lines:
        merged[int(r["variant"])] = merged.get(int(r["variant"]), 0) + int(r["quantity"])
    qs = Variant.objects.select_related("product__category", "product__subcategory")
    variants = {v.id: v for v in (qs.select_for_update(of=("self",)) if lock else qs)
                .filter(pk__in=merged).order_by("id")}
    problems, rows, subtotal = [], [], Decimal(0)
    for vid, qty in merged.items():
        v = variants.get(vid)
        if v is None or not cart.variant_is_orderable(v):
            problems.append({"variant": vid, "issue": "unavailable"})
            continue
        if not 1 <= qty <= cart.MAX_QTY:
            problems.append({"variant": vid, "issue": "quantity_invalid"})
        elif qty > v.on_hand:
            problems.append({"variant": vid, "issue": "quantity_above_stock",
                             "available": min(v.on_hand, cart.MAX_QTY)})
        price = cart.effective_unit_price(v)
        subtotal += price * qty
        rows.append((v, qty, price))
    if problems:  # CHK-06: nothing is created; tell the customer which lines to fix
        raise Conflict("Some items in your bag need attention.", code="lines_unavailable",
                       fields={"lines": problems})
    return rows, subtotal


def coupon_lines(rows):
    return [coupons.Line(v.id, v.product_id, v.product.category_id, price, qty,
                         v.product.discount_pct > 0) for v, qty, price in rows]


@transaction.atomic
def place_order(data: dict, user=None, idempotency_key=None) -> tuple[Order, bool]:
    """Returns (order, created). A repeated idempotency key returns the original order (CHK-09)."""
    if idempotency_key:
        existing = Order.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing, False
    site = SiteSettings.load()
    _required(data, "name", "district", "thana", "address_line")
    phone = clean_phone(data.get("phone"))
    email = (data.get("email") or "").strip().lower()
    if email and "@" not in email:
        raise ValidationError({"email": "Enter a valid email address."})
    area = data.get("area")
    if area not in Area.values:
        raise ValidationError({"area": "Choose Inside Dhaka or Outside Dhaka."})
    method = data.get("payment_method")
    if method not in PayMethod.values or not method_enabled(site, method):
        raise ValidationError({"payment_method": "This payment method is not available."})
    sender = trx = None
    if method in (PayMethod.BKASH, PayMethod.NAGAD):  # CHK-04 both are required
        sender = clean_sender(data.get("sender_number"))
        trx = clean_trx(data.get("trx_id"), method)

    rows, subtotal = price_lines(data.get("lines"), lock=True)
    discount, coupon_code, coupon = Decimal(0), "", None
    if (data.get("coupon_code") or "").strip():
        try:
            coupon, discount = coupons.evaluate(data["coupon_code"], coupon_lines(rows),
                                                user=user, phone=phone, lock=True)
        except coupons.CouponError as e:
            raise ValidationError({"coupon_code": e.message}) from None
        coupon_code = coupon.code
    fee = delivery_fee(site, area)  # CHK-02 / rule 2, frozen on the order (rule 10)
    total = subtotal - discount + fee  # ORD-13
    expected = data.get("expected_total")
    if expected not in (None, "") and Decimal(str(expected)) != total:
        raise Conflict("Prices changed. Please review your total and confirm again.",  # §11
                       code="price_changed", fields={"total": str(total)})

    try:
        with transaction.atomic():
            order = Order.objects.create(
                number=next_number(), user=user if user and user.is_authenticated else None,
                status=OrderStatus.DRAFT, name=data["name"].strip(), phone=phone, email=email,
                area=area, district=data["district"].strip(), thana=data["thana"].strip(),
                address_line=data["address_line"].strip(), note=(data.get("note") or "").strip(),
                subtotal=subtotal, coupon_code=coupon_code, coupon_discount=discount,
                delivery_fee=fee, total=total, payment_method=method,
                idempotency_key=idempotency_key or None)
            OrderItem.objects.bulk_create([OrderItem(
                order=order, variant=v, product_name=v.product.name,
                variant_label=" / ".join(x for x in (v.color_name, v.size_label) if x),
                sku=v.sku, unit_price=price, quantity=qty, line_total=price * qty)
                for v, qty, price in rows])
            Payment.objects.create(order=order, method=method, amount=total,
                                   sender_number=sender or "", trx_id=trx,
                                   status=PayStatus.PENDING)
    except IntegrityError:  # a concurrent identical TrxID or idempotency key
        if idempotency_key and (dup := Order.objects.filter(
                idempotency_key=idempotency_key).first()):
            return dup, False
        raise ValidationError({"trx_id": "This transaction ID has already been used."}) from None
    if coupon:  # CPN-04: usage counted at placement
        Coupon.objects.filter(pk=coupon.pk).update(used_count=F("used_count") + 1)
    add_log(order, "", OrderStatus.DRAFT, user if user and user.is_authenticated else None,
            "Order placed")
    transaction.on_commit(lambda: _queue_received(order.id))
    return order, True


def _queue_received(order_id):
    from .notifications import queue_order_received

    queue_order_received(order_id)
