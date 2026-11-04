# Covers SRS CPN-01..CPN-04, CRT-05, CHK-06; scenario 14.
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.catalog.models import Category
from apps.shop import coupons
from apps.shop.checkout import coupon_lines, place_order, price_lines
from apps.shop.models import Coupon, CouponScope, CouponType, OrderStatus
from apps.shop.orders import transition

from .helpers import configure_site, one_variant, payload

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def site(db):
    return configure_site()


def make(code="WELCOME10", **kw):
    base = dict(type=CouponType.PERCENT, value=Decimal(10))
    base.update(kw)
    return Coupon.objects.create(code=code, **base)


def lines_for(*variants_qty):
    rows, _ = price_lines([{"variant": v.id, "quantity": q} for v, q in variants_qty])
    return coupon_lines(rows)


def test_scenario_14_welcome10_applies_ten_percent_and_is_released_on_cancel():
    make()
    v = one_variant(price="2000")
    order, _ = place_order(payload(v, 2, coupon_code="welcome10"))  # case-insensitive
    assert order.coupon_discount == 400 and order.total == 3660  # 4000 - 400 + 60
    assert Coupon.objects.get().used_count == 1
    transition(order.id, "cancelled", reason="changed my mind")
    assert Coupon.objects.get().used_count == 0  # CPN-04 released


def test_usage_kept_on_return():  # CPN-04
    make()
    v = one_variant()
    order, _ = place_order(payload(v, coupon_code="WELCOME10"))
    transition(order.id, "confirmed")
    transition(order.id, "shipping")
    transition(order.id, "returned", reason="wrong size")
    assert Coupon.objects.get().used_count == 1


def test_fixed_coupon_capped_at_eligible_total():
    make("FLAT", type=CouponType.FIXED, value=Decimal(5000))
    _, d = coupons.evaluate("FLAT", lines_for((one_variant(price="900"), 1)))
    assert d == 900


def test_percent_cap_and_rounding_to_taka():
    make("CAPPED", value=Decimal(15), max_discount=Decimal(100))
    assert coupons.evaluate("CAPPED", lines_for((one_variant(price="1999"), 1)))[1] == 100
    make("ROUND", value=Decimal("12.5"))
    assert coupons.evaluate("ROUND", lines_for((one_variant("B", price="999"), 1)))[1] == 125  # 124.875


@pytest.mark.parametrize("mutate,code", [
    (dict(is_active=False), "invalid"),
    (dict(starts_at=timezone.now() + timedelta(days=1)), "expired"),
    (dict(ends_at=timezone.now() - timedelta(days=1)), "expired"),
    (dict(min_subtotal=Decimal(5000)), "minimum_not_met"),
    (dict(usage_limit=1, used_count=1), "used_up"),
])
def test_validation_messages(mutate, code):  # CPN-02 order, CRT-05
    make(**mutate)
    with pytest.raises(coupons.CouponError) as e:
        coupons.evaluate("WELCOME10", lines_for((one_variant(), 1)))
    assert e.value.code == code and e.value.message


def test_unknown_code():
    with pytest.raises(coupons.CouponError) as e:
        coupons.evaluate("NOPE", lines_for((one_variant(), 1)))
    assert e.value.code == "invalid"


def test_category_scope_covers_only_that_category_and_its_subs():  # CPN-02
    bras = Category.objects.get(slug="bras")
    c = make("BRAS", scope=CouponScope.CATEGORIES)
    c.categories.add(bras)
    bra = one_variant("A Bra", price="1000")
    from apps.catalog.tests.factories import product

    panty = product("A Panty", cat="panties", price="500", sizes=("M",), colours=("Rose",)).variants.get()
    _, d = coupons.evaluate("BRAS", lines_for((bra, 1), (panty, 1)))
    assert d == 100  # 10% of the bra only
    with pytest.raises(coupons.CouponError) as e:
        coupons.evaluate("BRAS", lines_for((panty, 1)))
    assert e.value.code == "not_applicable"


def test_product_scope():
    a, b = one_variant("A", price="1000"), one_variant("B", price="1000")
    c = make("ONLYA", scope=CouponScope.PRODUCTS)
    c.products.add(a.product)
    assert coupons.evaluate("ONLYA", lines_for((a, 1), (b, 1)))[1] == 100


def test_discounted_items_excluded_unless_allowed():
    sale = one_variant("Sale", price="1000", compare="1250")
    full = one_variant("Full", price="1000")
    make("NOSALE")
    assert coupons.evaluate("NOSALE", lines_for((sale, 1), (full, 1)))[1] == 100
    make("WITHSALE", includes_discounted=True)
    assert coupons.evaluate("WITHSALE", lines_for((sale, 1), (full, 1)))[1] == 200
    with pytest.raises(coupons.CouponError):
        coupons.evaluate("NOSALE", lines_for((sale, 1)))


def test_per_customer_limit_by_phone_for_guests():  # decision 6
    make("ONCE", per_customer_limit=1)
    v = one_variant(on_hand=10)
    place_order(payload(v, coupon_code="ONCE"))
    with pytest.raises(ValidationError) as e:
        place_order(payload(v, coupon_code="ONCE"))
    assert "already used" in str(e.value.detail)
    other = payload(v, coupon_code="ONCE", phone="01999999999")
    assert place_order(other)[1] is True  # a different mobile is a different customer


def test_per_customer_limit_by_user_and_cancelled_orders_dont_count(owner):
    make("ONCE", per_customer_limit=1)
    v = one_variant(on_hand=10)
    first, _ = place_order(payload(v, coupon_code="ONCE"), user=owner)
    with pytest.raises(ValidationError):
        place_order(payload(v, coupon_code="ONCE"), user=owner)
    transition(first.id, OrderStatus.CANCELLED, reason="oops")
    assert place_order(payload(v, coupon_code="ONCE"), user=owner)[1] is True


def test_total_usage_limit_enforced_at_checkout():
    make("TWO", usage_limit=2)
    v = one_variant(on_hand=10)
    for phone in ("01711111111", "01722222222"):
        place_order(payload(v, coupon_code="TWO", phone=phone))
    with pytest.raises(ValidationError):
        place_order(payload(v, coupon_code="TWO", phone="01733333333"))


def test_discount_never_exceeds_subtotal_and_delivery_never_discounted():  # ORD-13
    make("HUGE", type=CouponType.FIXED, value=Decimal(99999))
    order, _ = place_order(payload(one_variant(price="500"), coupon_code="HUGE"))
    assert order.coupon_discount == 500 and order.total == 60


def test_bad_coupon_at_checkout_is_a_field_error_and_creates_nothing():
    from apps.shop.models import Order

    with pytest.raises(ValidationError) as e:
        place_order(payload(one_variant(), coupon_code="NOPE"))
    assert "coupon_code" in e.value.detail and Order.objects.count() == 0
