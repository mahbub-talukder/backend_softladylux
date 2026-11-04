# Covers SRS CRT-03/05/08/09, CHK-06/07/09, ACC-06/09, ORD-04, §7 public order routes;
# scenarios 3, 6, 10, 12 (guest tracking), 14, 20.
import pytest
from rest_framework.throttling import SimpleRateThrottle

from apps.accounts.models import User
from apps.shop.models import Coupon, Order

from .helpers import bkash, configure_site, one_variant, payload

pytestmark = pytest.mark.django_db
B = "/api/v1"


@pytest.fixture(autouse=True)
def site(db):
    return configure_site()


# ------------------------------------------------------------------ cart / coupons


def test_cart_validate_prices_availability_and_delivery(api_client):  # CRT-08
    v = one_variant(price="1000", on_hand=3)
    r = api_client.post(f"{B}/cart/validate/", {"lines": [{"variant": v.id, "quantity": 2}],
                                                "area": "outside_dhaka"}, format="json")
    assert r.status_code == 200
    line = r.data["lines"][0]
    assert line["price"] == "1000.00" and line["available_quantity"] == 3 and line["active"] is True
    assert line["issues"] == [] and "on_hand" not in line
    assert r.data["subtotal"] == "2000.00" and r.data["delivery"]["selected"] == "120.00"
    assert r.data["total"] == "2120.00"  # CRT-04


def test_cart_validate_flags_issues_with_one_click_fixes(api_client):  # CRT-03/09
    ok, few, gone = one_variant("A"), one_variant("B", on_hand=2), one_variant("C")
    out = one_variant("D", on_hand=0)
    gone.is_active = False
    gone.save()
    r = api_client.post(f"{B}/cart/validate/", {"lines": [
        {"variant": ok.id, "quantity": 1}, {"variant": few.id, "quantity": 5},
        {"variant": gone.id, "quantity": 1}, {"variant": out.id, "quantity": 1},
        {"variant": 99999, "quantity": 1}]}, format="json")
    issues = [ln["issues"] for ln in r.data["lines"]]
    assert issues == [[], ["quantity_above_stock"], ["unavailable"], ["sold_out"], ["unavailable"]]
    assert r.data["lines"][1]["available_quantity"] == 2
    assert r.data["subtotal"] == "1000.00"  # only the clean line counts


def test_cart_validate_bad_input(api_client):
    assert api_client.post(f"{B}/cart/validate/", {}, format="json").status_code == 400
    assert api_client.post(f"{B}/cart/validate/", {"lines": [{"variant": "x"}]},
                           format="json").status_code == 400
    assert api_client.post(f"{B}/cart/validate/", {"lines": [{"variant": 1, "quantity": 1}] * 31},
                           format="json").status_code == 400


def test_scenario_20_cart_validate_is_rate_limited(api_client, monkeypatch):
    monkeypatch.setitem(SimpleRateThrottle.THROTTLE_RATES, "cart_validate", "3/min")
    v = one_variant()
    body = {"lines": [{"variant": v.id, "quantity": 1}]}
    codes = [api_client.post(f"{B}/cart/validate/", body, format="json").status_code
             for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]


def test_coupon_messages_in_cart_and_endpoint(api_client):  # CRT-05, scenario 14
    Coupon.objects.create(code="WELCOME10", type="percent", value=10, min_subtotal=1500)
    v = one_variant(price="1000", on_hand=5)
    one = {"lines": [{"variant": v.id, "quantity": 1}], "coupon_code": "welcome10"}
    r = api_client.post(f"{B}/cart/validate/", one, format="json")
    assert r.data["coupon"]["valid"] is False and r.data["coupon"]["error"]["code"] == "minimum_not_met"
    two = {"lines": [{"variant": v.id, "quantity": 2}], "coupon_code": "welcome10", "area": "inside_dhaka"}
    r = api_client.post(f"{B}/cart/validate/", two, format="json")
    assert r.data["coupon"] == {"code": "WELCOME10", "valid": True, "discount": "200", "description": ""}
    assert r.data["total"] == "1860.00"
    r = api_client.post(f"{B}/coupons/validate/", {"code": "WELCOME10", "lines": two["lines"]},
                        format="json")
    assert r.data["valid"] is True and r.data["discount"] == "200"
    bad = api_client.post(f"{B}/coupons/validate/", {"code": "NOPE", "lines": two["lines"]},
                          format="json")
    assert bad.status_code == 200 and bad.data["error"]["code"] == "invalid"
    assert api_client.post(f"{B}/coupons/validate/", {"lines": two["lines"]},
                           format="json").status_code == 400


def test_coupon_validate_throttled(api_client, monkeypatch):
    monkeypatch.setitem(SimpleRateThrottle.THROTTLE_RATES, "coupon_validate", "2/min")
    v = one_variant()
    body = {"code": "X", "lines": [{"variant": v.id, "quantity": 1}]}
    codes = [api_client.post(f"{B}/coupons/validate/", body, format="json").status_code
             for _ in range(3)]
    assert codes == [200, 200, 429]


def test_payment_methods_follow_settings(api_client):  # §11
    assert api_client.get(f"{B}/payment-methods/").data == {"bkash": True, "nagad": True, "cod": True}
    configure_site(bkash_number="", cod_enabled=False)
    assert api_client.get(f"{B}/payment-methods/").data == {"bkash": False, "nagad": True,
                                                            "cod": False}


# ------------------------------------------------------------------ checkout


def test_checkout_creates_draft_and_returns_customer_safe_order(api_client):  # CHK-06/07
    v = one_variant(on_hand=5)
    r = api_client.post(f"{B}/checkout/", bkash(v, 2), format="json")
    assert r.status_code == 201
    d = r.data
    assert d["status"] == "draft" and d["number"].startswith("SLL-") and d["total"] == "2060.00"
    assert d["payment_status"] == "Awaiting verification" and d["can_cancel"] is True
    assert d["timeline"][0]["label"] == "Order placed" and len(d["items"]) == 1
    assert "trx_id" not in d and "logs" not in d
    v.refresh_from_db()
    assert v.on_hand == 5


def test_checkout_idempotency_header(api_client):  # CHK-09
    v = one_variant()
    a = api_client.post(f"{B}/checkout/", payload(v), format="json", HTTP_IDEMPOTENCY_KEY="abc")
    b = api_client.post(f"{B}/checkout/", payload(v), format="json", HTTP_IDEMPOTENCY_KEY="abc")
    assert (a.status_code, b.status_code) == (201, 200) and a.data["number"] == b.data["number"]
    assert Order.objects.count() == 1


def test_scenario_6_duplicate_trx_is_400_with_field_error(api_client):
    v = one_variant()
    api_client.post(f"{B}/checkout/", bkash(v), format="json")
    r = api_client.post(f"{B}/checkout/", bkash(v), format="json")
    assert r.status_code == 400 and "trx_id" in r.data["fields"]


def test_checkout_stock_conflict_is_409_with_lines(api_client):  # CHK-06
    v = one_variant(on_hand=1)
    r = api_client.post(f"{B}/checkout/", payload(v, 3), format="json")
    assert r.status_code == 409 and r.data["code"] == "lines_unavailable"
    assert r.data["fields"]["lines"][0]["variant"] == v.id and Order.objects.count() == 0


def test_checkout_price_change_409(api_client):  # §11
    v = one_variant(price="1000")
    r = api_client.post(f"{B}/checkout/", payload(v, expected_total="900"), format="json")
    assert r.status_code == 409 and r.data["code"] == "price_changed"


def test_checkout_throttled(api_client, monkeypatch):
    monkeypatch.setitem(SimpleRateThrottle.THROTTLE_RATES, "checkout", "1/min")
    v = one_variant(on_hand=9)
    codes = [api_client.post(f"{B}/checkout/", payload(v), format="json").status_code
             for _ in range(2)]
    assert codes == [201, 429]


def test_checkout_links_order_to_signed_in_customer_only(api_client, as_user, owner):
    c = User.objects.create_user(phone="01712345678", password="cust-pass-123", full_name="Cust")
    v = one_variant(on_hand=9)
    as_user(c).post(f"{B}/checkout/", payload(v), format="json")
    assert Order.objects.get().user == c
    Order.objects.all().delete()
    as_user(owner).post(f"{B}/checkout/", payload(v), format="json")  # staff are not customers
    assert Order.objects.get().user is None


# ------------------------------------------------------------------ mine / track / cancel


def test_my_orders_list_detail_and_isolation(as_user, api_client):  # ACC-06
    a = User.objects.create_user(phone="01712345678", password="cust-pass-123")
    b = User.objects.create_user(phone="01811111111", password="cust-pass-123")
    v = one_variant(on_hand=9)
    assert api_client.get(f"{B}/orders/").status_code in (401, 403)  # anonymous, before any login
    as_user(a).post(f"{B}/checkout/", payload(v), format="json")
    num = Order.objects.get().number
    assert as_user(a).get(f"{B}/orders/").data["count"] == 1
    assert as_user(a).get(f"{B}/orders/{num}/").data["number"] == num
    assert as_user(b).get(f"{B}/orders/").data["count"] == 0
    assert as_user(b).get(f"{B}/orders/{num}/").status_code == 404
    from apps.accounts.models import Role
    staff = User.objects.create_user(email="s@example.com", password="x-pass-1234", role=Role.STAFF)
    assert as_user(staff).get(f"{B}/orders/").data["count"] == 0  # staff are not customers


def test_guest_tracking_by_number_and_phone(api_client):  # ACC-09
    v = one_variant(on_hand=9)
    num = api_client.post(f"{B}/checkout/", payload(v), format="json").data["number"]
    ok = api_client.post(f"{B}/track/", {"number": num.lower(), "phone": "+8801712345678"},
                         format="json")
    assert ok.status_code == 200 and ok.data["number"] == num
    for bad in ({"number": num, "phone": "01999999999"}, {"number": "SLL-000000-0000",
                                                          "phone": "01712345678"},
                {"number": num, "phone": "bad"}, {}):
        assert api_client.post(f"{B}/track/", bad, format="json").status_code == 404


def test_track_throttled(api_client, monkeypatch):
    monkeypatch.setitem(SimpleRateThrottle.THROTTLE_RATES, "track", "2/min")
    codes = [api_client.post(f"{B}/track/", {"number": "x", "phone": "01712345678"},
                             format="json").status_code for _ in range(3)]
    assert codes == [404, 404, 429]


def test_guest_cancel_with_number_phone_and_reason(api_client):  # decision 3, ORD-04
    v = one_variant(on_hand=9)
    num = api_client.post(f"{B}/checkout/", payload(v), format="json").data["number"]
    url = f"{B}/orders/{num}/cancel/"
    assert api_client.post(url, {"reason": "changed my mind", "phone": "01999999999"},
                           format="json").status_code == 404  # wrong phone: no leak
    assert api_client.post(url, {"phone": "01712345678"}, format="json").status_code == 400  # reason
    r = api_client.post(url, {"reason": "changed my mind", "phone": "01712345678"}, format="json")
    assert r.status_code == 200 and r.data["status"] == "cancelled" and r.data["can_cancel"] is False


def test_customer_cancel_own_order_but_not_after_shipping(as_user, api_client):
    from apps.shop import orders

    c = User.objects.create_user(phone="01712345678", password="cust-pass-123")
    v = one_variant(on_hand=9)
    num = as_user(c).post(f"{B}/checkout/", payload(v), format="json").data["number"]
    o = Order.objects.get()
    orders.transition(o.id, "confirmed")
    orders.transition(o.id, "shipping")
    r = as_user(c).post(f"{B}/orders/{num}/cancel/", {"reason": "too late?"}, format="json")
    assert r.status_code == 409 and "contact" in r.data["message"].lower()  # ORD-04
    assert api_client.post(f"{B}/orders/NOPE/cancel/", {}, format="json").status_code == 404


def test_customer_timeline_hides_internal_entries(api_client, owner):
    from apps.shop import orders

    v = one_variant(on_hand=9)
    num = api_client.post(f"{B}/checkout/", payload(v), format="json").data["number"]
    o = Order.objects.get()
    orders.add_note(o, owner, "VIP customer, handle with care")
    orders.edit_order(o.id, owner, note="x")
    orders.transition(o.id, "confirmed", owner, note="internal reason text")
    d = api_client.post(f"{B}/track/", {"number": num, "phone": "01712345678"}, format="json").data
    assert [t["label"] for t in d["timeline"]] == ["Order placed", "Order confirmed"]
    assert "VIP" not in str(d) and "internal" not in str(d)
