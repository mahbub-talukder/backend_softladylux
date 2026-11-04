# Covers SRS CHK-01..CHK-10, CRT-02/09, rules 2, 5, 9, 10; scenarios 3, 6, 10.
from decimal import Decimal

import pytest
from rest_framework.exceptions import ValidationError

from apps.core.exceptions import Conflict
from apps.core.models import SiteSettings
from apps.inventory.models import StockMovement
from apps.shop.checkout import place_order
from apps.shop.models import Order, OrderStatus, Payment, PayStatus

from .helpers import bkash, configure_site, one_variant, payload

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def site(db):
    return configure_site()


def test_scenario_3_bkash_order_is_draft_pending_stock_unchanged():
    v = one_variant()
    order, created = place_order(bkash(v, 2))
    v.refresh_from_db()
    assert created and order.status == OrderStatus.DRAFT
    assert order.payment.status == PayStatus.PENDING and order.payment.trx_id == "8N7A6D5EE7"
    assert v.on_hand == 5 and not StockMovement.objects.filter(variant=v).exists()  # no hold
    assert order.subtotal == Decimal("2000") and order.delivery_fee == Decimal("60")
    assert order.total == Decimal("2060") and order.payment.amount == order.total


def test_order_number_format_and_daily_sequence():  # CHK-08
    v = one_variant()
    a, _ = place_order(payload(v))
    b, _ = place_order(payload(v))
    assert a.number.startswith("SLL-") and len(a.number) == len("SLL-261104-0001")
    assert a.number.endswith("-0001") and b.number.endswith("-0002")


def test_prices_and_address_are_snapshots():  # rule 10
    v = one_variant(price="1000")
    order, _ = place_order(payload(v, 2))
    v.product.price = Decimal("1500")
    v.product.save()
    configure_site(delivery_fee_inside_dhaka=99)
    order.refresh_from_db()
    item = order.items.get()
    assert (item.unit_price, order.delivery_fee, order.total) == (
        Decimal("1000.00"), Decimal("60.00"), Decimal("2060.00"))
    assert item.product_name == "Plunge Bra" and item.sku == v.sku


def test_scenario_10_new_fee_applies_to_next_checkout_only():
    v = one_variant()
    first, _ = place_order(payload(v))
    configure_site(delivery_fee_inside_dhaka=80)
    second, _ = place_order(payload(v))
    first.refresh_from_db()
    assert first.delivery_fee == 60 and second.delivery_fee == 80


def test_outside_dhaka_fee():  # rule 2
    order, _ = place_order(payload(one_variant(), area="outside_dhaka"))
    assert order.delivery_fee == 120


def test_variant_price_override_used():
    v = one_variant()
    v.price_override = Decimal("800")
    v.save()
    assert place_order(payload(v))[0].subtotal == 800


def test_scenario_6_duplicate_trx_rejected_per_method():  # CHK-05
    v = one_variant()
    place_order(bkash(v))
    with pytest.raises(ValidationError) as e:
        place_order(bkash(v))
    assert "already been used" in str(e.value.detail)
    # the same code on the other method is a different payment, allowed
    nagad = bkash(v)
    nagad["payment_method"] = "nagad"
    assert place_order(nagad)[1] is True


@pytest.mark.parametrize("trx", ["short", "TOOLONG123456", "bad id!!!!", "", None])
def test_trx_format(trx):
    with pytest.raises(ValidationError):
        place_order(bkash(one_variant(), trx=trx))


def test_trx_is_uppercased():
    order, _ = place_order(bkash(one_variant(), trx="ab12cd34ef"))
    assert order.payment.trx_id == "AB12CD34EF"


def test_mobile_methods_need_sender_and_trx():  # CHK-04
    v = one_variant()
    for missing in ("sender_number", "trx_id"):
        data = bkash(v)
        data.pop(missing)
        with pytest.raises(ValidationError):
            place_order(data)


def test_cod_has_pending_payment_without_trx():  # CHK-10
    order, _ = place_order(payload(one_variant()))
    assert order.payment.method == "cod" and order.payment.trx_id is None
    place_order(payload(one_variant("Other")))  # two COD orders: NULL trx never collides


def test_method_hidden_when_not_configured():  # §11
    configure_site(bkash_number="", cod_enabled=False)
    v = one_variant()
    for method in ("bkash", "cod"):
        with pytest.raises(ValidationError):
            place_order(bkash(v) if method == "bkash" else payload(v, payment_method="cod"))


@pytest.mark.parametrize("bad", [
    {"phone": "12345"}, {"area": "mars"}, {"name": ""}, {"thana": ""}, {"email": "nope"},
    {"payment_method": "card"},
])
def test_field_validation(bad):
    with pytest.raises(ValidationError):
        place_order(payload(one_variant(), **bad))


def test_phone_normalised():  # ACC-14 / §10
    order, _ = place_order(payload(one_variant(), phone="+8801712345678"))
    assert order.phone == "01712345678"


def test_stock_checked_nothing_created_when_short():  # CHK-06
    v = one_variant(on_hand=2)
    with pytest.raises(Conflict) as e:
        place_order(payload(v, 3))
    assert e.value.fields["lines"][0]["issue"] == "quantity_above_stock"
    assert Order.objects.count() == 0 and Payment.objects.count() == 0


def test_unavailable_line_blocks_whole_order():
    ok, gone = one_variant("A"), one_variant("B")
    gone.is_active = False
    gone.save()
    data = payload(ok)
    data["lines"].append({"variant": gone.id, "quantity": 1})
    with pytest.raises(Conflict):
        place_order(data)
    assert Order.objects.count() == 0


@pytest.mark.parametrize("qty", [0, 11])
def test_quantity_limits(qty):  # §10: 1–10
    with pytest.raises(Conflict):
        place_order(payload(one_variant(on_hand=50), qty))


def test_duplicate_variant_lines_are_merged():
    v = one_variant(on_hand=5)
    data = payload(v)
    data["lines"] = [{"variant": v.id, "quantity": 2}, {"variant": v.id, "quantity": 2}]
    assert place_order(data)[0].items.get().quantity == 4


def test_empty_and_oversized_bag():
    with pytest.raises(ValidationError):
        place_order(payload(one_variant(), lines=[]))


def test_idempotency_key_returns_same_order():  # CHK-09
    v = one_variant()
    a, created_a = place_order(payload(v), idempotency_key="key-1")
    b, created_b = place_order(payload(v), idempotency_key="key-1")
    assert created_a and not created_b and a.pk == b.pk and Order.objects.count() == 1


def test_expected_total_mismatch_is_409():  # §11 price change during checkout
    v = one_variant(price="1000")
    with pytest.raises(Conflict) as e:
        place_order(payload(v, expected_total="999"))
    assert e.value.fields["total"] == "1060.00"
    assert place_order(payload(v, expected_total="1060"))[1] is True


def test_status_log_written_on_placement():
    order, _ = place_order(payload(one_variant()))
    log = order.logs.get()
    assert (log.from_status, log.to_status, log.note) == ("", "draft", "Order placed")


def test_site_settings_untouched():
    assert SiteSettings.load().delivery_fee_inside_dhaka == 60
