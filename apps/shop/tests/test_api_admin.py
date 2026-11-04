# Covers SRS ORD-07..12, PAY-03/06/08, CPN-01/05, INV-07/08/10/11/12, PRM-03/05/08;
# scenarios 4, 5, 8, 9, 11, 14.
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.catalog.models import Category
from apps.core.models import AuditLog
from apps.inventory.models import StockMovement
from apps.shop.checkout import place_order
from apps.shop.models import Coupon, Order

from .helpers import bkash, configure_site, one_variant, payload

pytestmark = pytest.mark.django_db
A = "/api/v1/admin"


@pytest.fixture(autouse=True)
def site(db):
    return configure_site()


@pytest.fixture
def admin(as_user, owner):
    return as_user(owner)


def new(v=None, qty=1, **kw):
    return place_order(payload(v or one_variant(), qty, **kw))[0]


# ------------------------------------------------------------------ orders


def test_list_filters_search_counts_and_stale_flag(admin):
    a = new(one_variant("A"), name="Alice Rahman")
    b, _ = place_order(bkash(one_variant("B"), trx="TRXBKASH01", name="Bob", phone="01811111111",
                             area="outside_dhaka"))
    Order.objects.filter(pk=a.pk).update(placed_at=timezone.now() - timedelta(hours=30))
    r = admin.get(f"{A}/orders/")
    rows = {x["number"]: x for x in r.data["results"]}
    assert r.data["count"] == 2 and r.data["counts"]["draft"] == 2 and r.data["counts"]["confirmed"] == 0
    assert rows[a.number]["is_stale"] is True and rows[b.number]["is_stale"] is False  # ORD-12
    for q, expect in [("alice", 1), ("01811111111", 1), ("TRXBKASH01", 1), ("trxbkash01", 1),
                      (b.number, 1), ("zzz", 0)]:
        assert admin.get(f"{A}/orders/", {"q": q}).data["count"] == expect, q
    assert admin.get(f"{A}/orders/", {"payment_method": "bkash"}).data["count"] == 1
    assert admin.get(f"{A}/orders/", {"payment_status": "pending", "payment_method": "bkash"}
                     ).data["count"] == 1  # PAY-08 queue
    assert admin.get(f"{A}/orders/", {"area": "outside_dhaka"}).data["count"] == 1
    assert admin.get(f"{A}/orders/", {"stale": 1}).data["count"] == 1
    assert admin.get(f"{A}/orders/", {"status": "confirmed"}).data["count"] == 0
    today = timezone.localdate().isoformat()
    assert admin.get(f"{A}/orders/", {"date_from": today, "date_to": today}).data["count"] == 1
    assert admin.get(f"{A}/orders/", {"ordering": "total"}).status_code == 200


def test_scenario_8_stale_draft_does_not_touch_stock(admin):
    v = one_variant(on_hand=5)
    o = new(v)
    Order.objects.filter(pk=o.pk).update(placed_at=timezone.now() - timedelta(hours=48))
    assert admin.get(f"{A}/orders/{o.id}/").data["is_stale"] is True
    v.refresh_from_db()
    assert v.on_hand == 5 and Order.objects.get().status == "draft"  # never auto-cancelled


def test_detail_has_items_payment_and_full_timeline(admin):
    o, _ = place_order(bkash(one_variant()))
    d = admin.get(f"{A}/orders/{o.id}/").data
    assert d["payment"]["trx_id"] == "8N7A6D5EE7" and d["items"][0]["quantity"] == 1
    assert d["logs"][0]["note"] == "Order placed" and "idempotency_key" not in d


def test_scenarios_4_5_verify_then_confirm_via_api_audited(admin, owner):
    v = one_variant(on_hand=5)
    o, _ = place_order(bkash(v, 2))
    r = admin.post(f"{A}/orders/{o.id}/transition/", {"to": "confirmed"}, format="json")
    assert r.status_code == 409 and "Verify the payment" in r.data["message"]  # scenario 5
    v.refresh_from_db()
    assert v.on_hand == 5
    ok = admin.post(f"{A}/orders/{o.id}/payment/verify/", format="json")
    assert ok.status_code == 200 and ok.data["payment"]["status"] == "verified"
    r = admin.post(f"{A}/orders/{o.id}/transition/", {"to": "confirmed"}, format="json")
    assert r.status_code == 200 and r.data["status"] == "confirmed"
    v.refresh_from_db()
    assert v.on_hand == 3  # scenario 4
    assert AuditLog.objects.filter(action="payment_verified").exists()
    assert AuditLog.objects.filter(action="order_confirmed", model="Order").exists()


def test_reject_and_edit_payment_via_api(admin):  # PAY-03/06
    a, _ = place_order(bkash(one_variant("A"), trx="AAAAAAAA11"))
    b, _ = place_order(bkash(one_variant("B"), trx="BBBBBBBB22"))
    assert admin.post(f"{A}/orders/{b.id}/payment/reject/", {}, format="json").status_code == 400
    r = admin.post(f"{A}/orders/{b.id}/payment/reject/", {"reason": "not in statement"}, format="json")
    assert r.data["payment"]["status"] == "rejected" and r.data["status"] == "draft"
    dup = admin.patch(f"{A}/orders/{b.id}/payment/", {"trx_id": "AAAAAAAA11"}, format="json")
    assert dup.status_code == 400
    fixed = admin.patch(f"{A}/orders/{b.id}/payment/", {"trx_id": "CCCCCCCC33"}, format="json")
    assert fixed.data["trx_id"] == "CCCCCCCC33" and fixed.data["status"] == "pending"
    assert a.payment.trx_id == "AAAAAAAA11"


def test_invalid_transition_and_missing_reason(admin):
    o = new()
    assert admin.post(f"{A}/orders/{o.id}/transition/", {"to": "delivered"},
                      format="json").status_code == 409
    assert admin.post(f"{A}/orders/{o.id}/transition/", {"to": "cancelled"},
                      format="json").status_code == 400
    assert admin.post(f"{A}/orders/{o.id}/transition/", {"to": "bogus"},
                      format="json").status_code == 400
    assert admin.post(f"{A}/orders/9999/transition/", {"to": "confirmed"},
                      format="json").status_code == 404


def test_full_flow_with_courier_and_return_scenario_9(admin):
    v = one_variant(on_hand=5)
    o = new(v, 2)
    url = f"{A}/orders/{o.id}/transition/"
    admin.post(url, {"to": "confirmed"}, format="json")
    sh = admin.post(url, {"to": "shipping", "courier": "Pathao", "tracking": "PT-9"}, format="json")
    assert sh.data["courier"] == "Pathao" and sh.data["tracking_ref"] == "PT-9"
    admin.post(url, {"to": "delivered"}, format="json")
    ret = admin.post(url, {"to": "returned", "reason": "did not fit"}, format="json")
    assert ret.data["status"] == "returned"
    v.refresh_from_db()
    assert v.on_hand == 5
    assert [m.reason for m in StockMovement.objects.order_by("id")] == ["order_confirmed",
                                                                        "order_returned"]
    assert [x["to_status"] for x in ret.data["logs"]] == ["draft", "confirmed", "shipping",
                                                          "delivered", "returned"]


def test_bulk_confirm_skips_failures_and_reports_each(admin):  # ORD-07
    ok = new(one_variant("A", on_hand=5))
    pending, _ = place_order(bkash(one_variant("B"), trx="PENDING001"))
    r = admin.post(f"{A}/orders/bulk-transition/", {"ids": [ok.id, pending.id], "to": "confirmed"},
                   format="json")
    assert r.data["done"] == [ok.number]
    assert r.data["failed"][0]["number"] == pending.number
    assert "Verify the payment" in r.data["failed"][0]["message"]
    assert admin.post(f"{A}/orders/bulk-transition/", {"ids": [ok.id], "to": "delivered"},
                      format="json").status_code == 400
    assert admin.post(f"{A}/orders/bulk-transition/", {"ids": [], "to": "confirmed"},
                      format="json").status_code == 400
    c = admin.post(f"{A}/orders/bulk-transition/", {"ids": [pending.id], "to": "cancelled"},
                   format="json")  # reason missing is reported, not a crash
    assert c.data["failed"] and "Reason" in c.data["failed"][0]["message"]
    c = admin.post(f"{A}/orders/bulk-transition/",
                   {"ids": [pending.id], "to": "cancelled", "reason": "unpaid draft"}, format="json")
    assert c.data["done"] == [pending.number]


def test_edit_and_notes(admin):  # ORD-08/09
    o = new()
    r = admin.patch(f"{A}/orders/{o.id}/", {"address_line": "Road 7", "note": "gate code 42"},
                    format="json")
    assert r.data["address_line"] == "Road 7" and "Edited" in r.data["logs"][-1]["note"]
    n = admin.post(f"{A}/orders/{o.id}/notes/", {"note": "Called the customer"}, format="json")
    assert n.status_code == 201 and n.data["logs"][-1]["internal"] is True
    assert admin.post(f"{A}/orders/{o.id}/notes/", {"note": ""}, format="json").status_code == 400
    assert admin.patch(f"{A}/orders/{o.id}/", {"phone": "bad"}, format="json").status_code == 400


# ------------------------------------------------------------------ permissions (PRM-03/05/08)


def test_scenario_11_28_per_action_permissions(as_user, make_staff, owner):
    o, _ = place_order(bkash(one_variant()))
    viewer = as_user(make_staff("orders.view"))
    assert viewer.get(f"{A}/orders/").status_code == 200
    assert viewer.post(f"{A}/orders/{o.id}/transition/", {"to": "cancelled", "reason": "x y z"},
                       format="json").status_code == 403
    assert viewer.post(f"{A}/orders/{o.id}/payment/verify/").status_code == 403
    manager = as_user(make_staff("orders.manage"))  # implies orders.view (PRM-05)
    assert manager.get(f"{A}/orders/").status_code == 200
    assert manager.post(f"{A}/orders/{o.id}/notes/", {"note": "hi there"}, format="json"
                        ).status_code == 201
    assert manager.post(f"{A}/orders/{o.id}/payment/verify/").status_code == 403
    verifier = as_user(make_staff("payments.verify"))
    assert verifier.get(f"{A}/orders/{o.id}/").status_code == 200  # implied view
    assert verifier.post(f"{A}/orders/{o.id}/payment/verify/").status_code == 200
    assert verifier.post(f"{A}/orders/{o.id}/transition/", {"to": "confirmed"},
                         format="json").status_code == 403
    assert as_user(make_staff("products.view")).get(f"{A}/orders/").status_code == 403


def test_permission_removed_takes_effect_next_request(as_user, make_staff, owner):
    from rest_framework.test import APIClient

    s = make_staff("orders.view", "orders.manage")
    c = as_user(s)
    o = new()
    assert c.post(f"{A}/orders/{o.id}/notes/", {"note": "first note"}, format="json"
                  ).status_code == 201
    oc = APIClient()
    oc.force_authenticate(owner)
    oc.put(f"{A}/users/{s.id}/permissions/", {"permissions": ["orders.view"]}, format="json")
    assert c.post(f"{A}/orders/{o.id}/notes/", {"note": "second"}, format="json").status_code == 403


# ------------------------------------------------------------------ inventory


def test_inventory_list_chips_summary_filters(admin):  # INV-10/11
    configure_site(low_stock_threshold=5)
    one_variant("InStock", on_hand=20)
    one_variant("LowOne", on_hand=3)
    one_variant("OutOne", on_hand=0)
    r = admin.get(f"{A}/inventory/")
    levels = {x["product"]: x["level"] for x in r.data["results"]}
    assert levels == {"InStock": "in_stock", "LowOne": "low", "OutOne": "out"}
    assert r.data["summary"] == {"in_stock": 1, "low": 1, "out": 1, "threshold": 5}
    assert admin.get(f"{A}/inventory/", {"status": "low"}).data["count"] == 1
    assert admin.get(f"{A}/inventory/", {"status": "out"}).data["results"][0]["product"] == "OutOne"
    assert admin.get(f"{A}/inventory/", {"status": "in_stock"}).data["count"] == 1
    assert admin.get(f"{A}/inventory/", {"q": "lowone"}).data["count"] == 1
    cat = Category.objects.get(slug="bras").id
    assert admin.get(f"{A}/inventory/", {"category": cat}).data["count"] == 3
    assert admin.get(f"{A}/inventory/", {"category": 9999}).data["count"] == 0


def test_adjust_rules_lot_remarks_never_below_zero(admin, owner):  # INV-07/12
    v = one_variant(on_hand=5)
    url = f"{A}/inventory/adjust/"
    base = {"variant": v.id, "kind": "stock_in", "quantity": 10, "lot_reference": "LOT-1",
            "remarks": "Supplier delivery"}
    r = admin.post(url, base, format="json")
    assert r.status_code == 201 and r.data["on_hand_after"] == 15 and r.data["reason"] == "adjustment_in"
    assert r.data["lot_reference"] == "LOT-1" and r.data["user"] == str(owner)
    bad = [{"kind": "x"}, {"quantity": "a"}, {"quantity": 0}, {"quantity": -2}, {"lot_reference": ""},
           {"remarks": "ab"}, {"remarks": "x" * 301}, {"variant": 9999}]
    for patch in bad:
        assert admin.post(url, {**base, **patch}, format="json").status_code == 400, patch
    dmg = admin.post(url, {**base, "kind": "damage", "quantity": 3, "remarks": "water damage"},
                     format="json")
    assert dmg.data["delta_on_hand"] == -3 and dmg.data["reason"] == "adjustment_out"
    assert dmg.data["remarks"].startswith("[damage]")
    corr = admin.post(url, {"variant": v.id, "kind": "correction", "quantity": -2,
                            "remarks": "recount found fewer"}, format="json")  # lot optional
    assert corr.status_code == 201 and corr.data["on_hand_after"] == 10
    assert admin.post(url, {"variant": v.id, "kind": "correction", "quantity": 0,
                            "remarks": "nothing"}, format="json").status_code == 400
    below = admin.post(url, {**base, "kind": "loss", "quantity": 999, "remarks": "stolen"},
                       format="json")
    assert below.status_code == 400  # INV-12
    v.refresh_from_db()
    assert v.on_hand == 10
    assert AuditLog.objects.filter(action="stock_adjust").count() == 3


def test_movements_history_and_export(admin):  # INV-08/10
    v = one_variant(on_hand=5)
    admin.post(f"{A}/inventory/adjust/", {"variant": v.id, "kind": "stock_in", "quantity": 1,
                                          "lot_reference": "L", "remarks": "top up"}, format="json")
    new(v)
    from apps.shop import orders

    orders.transition(Order.objects.get().id, "confirmed")
    m = admin.get(f"{A}/inventory/movements/", {"variant": v.id}).data
    assert [x["reason"] for x in m["results"]] == ["order_confirmed", "adjustment_in"]  # newest first
    assert m["results"][0]["order_ref"].startswith("SLL-")
    assert admin.get(f"{A}/inventory/movements/", {"reason": "adjustment_in"}).data["count"] == 1
    csv_resp = admin.get(f"{A}/inventory/export/")
    assert csv_resp["Content-Type"].startswith("text/csv") and v.sku in csv_resp.content.decode()
    assert admin.get(f"{A}/inventory/export/", {"status": "out"}).content.decode().count("\n") == 1


def test_inventory_permissions(as_user, make_staff):  # PRM-05
    v = one_variant()
    body = {"variant": v.id, "kind": "stock_in", "quantity": 1, "lot_reference": "L", "remarks": "abc"}
    view_only = as_user(make_staff("inventory.view"))
    assert view_only.get(f"{A}/inventory/").status_code == 200
    assert view_only.get(f"{A}/inventory/movements/").status_code == 200
    assert view_only.get(f"{A}/inventory/export/").status_code == 200
    assert view_only.post(f"{A}/inventory/adjust/", body, format="json").status_code == 403
    adjuster = as_user(make_staff("inventory.adjust"))  # implies view
    assert adjuster.get(f"{A}/inventory/").status_code == 200
    assert adjuster.post(f"{A}/inventory/adjust/", body, format="json").status_code == 201
    assert as_user(make_staff("orders.view")).get(f"{A}/inventory/").status_code == 403


# ------------------------------------------------------------------ coupons


def coupon_body(**kw):
    return {"code": "summer25", "type": "percent", "value": "25", "min_subtotal": "1000", **kw}


def test_coupon_crud_toggle_validation_and_used_by(admin):  # CPN-01/05
    r = admin.post(f"{A}/coupons/", coupon_body(max_discount="500", per_customer_limit=1,
                                                usage_limit=100), format="json")
    assert r.status_code == 201 and r.data["code"] == "SUMMER25" and r.data["used_count"] == 0
    cid = r.data["id"]
    assert admin.post(f"{A}/coupons/", coupon_body(code="Summer25"), format="json"
                      ).status_code == 400  # unique, case-insensitive
    for bad in ({"code": "abc"}, {"code": "has space1"}, {"value": "0"}, {"value": "150"},
                {"code": "OTHER1", "starts_at": "2026-12-02T00:00:00Z",
                 "ends_at": "2026-12-01T00:00:00Z"}):
        assert admin.post(f"{A}/coupons/", coupon_body(**{"code": "NEWCODE", **bad}),
                          format="json").status_code == 400, bad
    assert admin.get(f"{A}/coupons/").data[0]["code"] == "SUMMER25"
    assert admin.get(f"{A}/coupons/{cid}/").status_code == 200
    off = admin.patch(f"{A}/coupons/{cid}/", {"is_active": False}, format="json")
    assert off.data["is_active"] is False
    assert admin.put(f"{A}/coupons/{cid}/", coupon_body(value="30"), format="json").data["value"] == "30.00"
    assert admin.delete(f"{A}/coupons/{cid}/").status_code == 405
    Coupon.objects.filter(pk=cid).update(is_active=True, min_subtotal=0, value=10)
    o = new(one_variant(), coupon_code="SUMMER25")
    used = admin.get(f"{A}/coupons/{cid}/orders/").data
    assert used[0]["number"] == o.number and used[0]["discount"] == "100.00"
    assert admin.get(f"{A}/coupons/{cid}/").data["used_count"] == 1


def test_coupon_scope_lists(admin):
    cat = Category.objects.get(slug="bras")
    p = one_variant().product
    r = admin.post(f"{A}/coupons/", coupon_body(code="SCOPED1", scope="categories",
                                                categories=[cat.id], products=[p.id]), format="json")
    assert r.status_code == 201 and r.data["categories"] == [cat.id] and r.data["products"] == [p.id]


def test_coupons_need_manage_permission(as_user, make_staff):
    assert as_user(make_staff("orders.view")).get(f"{A}/coupons/").status_code == 403
    s = as_user(make_staff("coupons.manage"))
    assert s.get(f"{A}/coupons/").status_code == 200
    assert s.post(f"{A}/coupons/", coupon_body(code="STAFF10"), format="json").status_code == 201
