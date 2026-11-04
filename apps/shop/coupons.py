"""Coupon rules. SRS CPN-01..04, CHK-06: validation order is active → within dates → minimum
subtotal → usage limits → applicable lines exist; discount is computed on eligible lines only,
rounded to the nearest taka and never above the subtotal; one coupon per order."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

from .models import Coupon, CouponScope, CouponType, Order, OrderStatus


class CouponError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class Line:
    """One priced cart line the coupon is evaluated against."""

    variant_id: int
    product_id: int
    category_id: int
    unit_price: Decimal
    quantity: int
    discounted: bool  # the product already carries a compare-at discount (CAT-05)

    @property
    def total(self) -> Decimal:
        return self.unit_price * self.quantity


def taka(v: Decimal) -> Decimal:
    return v.quantize(Decimal(1), rounding=ROUND_HALF_UP)


def times_used_by(coupon: Coupon, user, phone: str | None) -> int:
    """CPN-01 per-customer limit: the logged-in user, or the normalised phone for guests,
    counting non-cancelled orders that used the code."""
    qs = Order.objects.filter(coupon_code=coupon.code).exclude(status=OrderStatus.CANCELLED)
    if user is not None and user.is_authenticated:
        return qs.filter(user=user).count()
    return qs.filter(phone=phone).count() if phone else 0


def eligible(coupon: Coupon, lines: list[Line]) -> list[Line]:
    out = lines
    if coupon.scope == CouponScope.CATEGORIES:  # every sub-category of the category (CPN-02)
        cats = set(coupon.categories.values_list("id", flat=True))
        out = [ln for ln in out if ln.category_id in cats]
    elif coupon.scope == CouponScope.PRODUCTS:
        prods = set(coupon.products.values_list("id", flat=True))
        out = [ln for ln in out if ln.product_id in prods]
    if not coupon.includes_discounted:
        out = [ln for ln in out if not ln.discounted]
    return out


def evaluate(code: str, lines: list[Line], user=None, phone: str | None = None,
             lock: bool = False):
    """Return (coupon, discount). Raises CouponError with a clear message (CRT-05).
    `lock=True` locks the coupon row so concurrent checkouts cannot exceed the usage limit."""
    qs = Coupon.objects.select_for_update() if lock else Coupon.objects
    coupon = qs.filter(code=code.strip().upper()).first()
    if coupon is None or not coupon.is_active:
        raise CouponError("invalid", "This coupon code is not valid.")
    now = timezone.now()
    if (coupon.starts_at and now < coupon.starts_at) or (coupon.ends_at and now > coupon.ends_at):
        raise CouponError("expired", "This coupon is not valid right now.")
    subtotal = sum((ln.total for ln in lines), Decimal(0))
    if subtotal < coupon.min_subtotal:
        raise CouponError(
            "minimum_not_met",
            f"Add ৳{taka(coupon.min_subtotal - subtotal)} more to use this coupon "
            f"(minimum order ৳{taka(coupon.min_subtotal)}).")
    if coupon.usage_limit is not None and coupon.used_count >= coupon.usage_limit:
        raise CouponError("used_up", "This coupon has reached its usage limit.")
    if coupon.per_customer_limit is not None and times_used_by(
            coupon, user, phone) >= coupon.per_customer_limit:
        raise CouponError("already_used", "You have already used this coupon.")
    base = sum((ln.total for ln in eligible(coupon, lines)), Decimal(0))
    if base <= 0:
        raise CouponError("not_applicable", "This coupon does not apply to the items in your bag.")
    if coupon.type == CouponType.PERCENT:
        discount = base * coupon.value / Decimal(100)
        if coupon.max_discount is not None:
            discount = min(discount, coupon.max_discount)
    else:
        discount = min(coupon.value, base)
    discount = min(taka(discount), subtotal)
    return coupon, discount
