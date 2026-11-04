# Covers SRS RPT-01..RPT-07, ADM-02, PAY-08, INS-01/06, PRM-03; scenarios 24 (route gate) and 28.
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.tests.factories import product
from apps.shop import orders
from apps.shop.checkout import place_order
from apps.shop.models import Coupon, Order, WishlistItem
from apps.shop.tests.helpers import bkash, configure_site, one_variant, payload

pytestmark = pytest.mark.django_db
A = "/api/v1/admin"


@pytest.fixture(autouse=True)
def site(db):
    return configure_site(support_email="owner@shop.test")


@pytest.fixture
def admin(as_user, owner):
    return as_user(owner)


def make(v, qty=1, to=None, **kw):
    o = place_order(payload(v, qty, **kw))[0]
    for step in {"confirmed": ["confirmed"], "delivered": ["confirmed", "shipping", "delivered"],
                 "cancelled": [], None: []}[to]:
        orders.transition(o.id, step)
    if to == "cancelled":
        orders.transition(o.id, "cancelled", reason="not wanted")
    return o


@pytest.fixture
def data(db):
    """Two categories, a confirmed + delivered + cancelled + returned + draft order."""
    bra = one_variant("A Bra", price="1000", on_hand=50)
    panty = product("A Panty", cat="panties", price="500", sizes=("M",), colours=("Rose",),
                    on_hand=50).variants.get()
    Coupon.objects.create(code="WELCOME10", type="percent", value=10)
    confirmed = make(bra, 2, "confirmed", coupon_code="WELCOME10")           # 2000-200+60 = 1860
    delivered = make(panty, 2, "delivered", area="outside_dhaka", phone="01811111111")  # 1000+120
    cancelled = make(bra, 1, "cancelled", phone="01822222222")
    returned = make(bra, 1, "delivered", phone="01833333333")
    orders.transition(returned.id, "returned", reason="did not fit")
    draft = make(bra, 1, None, phone="01844444444")
    return dict(bra=bra, panty=panty, confirmed=confirmed, delivered=delivered,
                cancelled=cancelled, returned=returned, draft=draft)


# ------------------------------------------------------------------ sales (RPT-02/03)


def test_sales_summary_excludes_cancelled_and_returned(admin, data):  # RPT-02
    d = admin.get(f"{A}/reports/sales/", {"range": "7d"}).data
    s = d["summary"]
    assert s["orders"] == 5  # all placed
    assert s["revenue"] == "2980.00"  # 1860 + 1120 only
    assert s["average_order_value"] == "1490.00" and s["returns"] == 1
    assert s["coupon_discounts"] == "200.00"
    assert d["period"]["range"] == "7d"


def test_sales_breakdowns(admin, data):  # RPT-03
    d = admin.get(f"{A}/reports/sales/").data  # default 30d
    cats = {c["name"]: (c["revenue"], c["units"]) for c in d["by_category"]}
    assert cats == {"Bras": ("2000.00", 2), "Panties": ("1000.00", 2)}
    assert [p["name"] for p in d["top_products"]] == ["A Bra", "A Panty"]
    assert d["top_products"][0] == {"name": "A Bra", "category": "Bras", "units": 2,
                                    "revenue": "2000.00"}
    assert {m["key"]: m["orders"] for m in d["by_payment_method"]} == {"cod": 2}
    assert {a["key"]: a["orders"] for a in d["by_area"]} == {"inside_dhaka": 1, "outside_dhaka": 1}
    assert len(d["by_day"]) == 30 and d["by_day"][-1]["orders"] == 2  # zero days are included
    assert d["by_day"][-1]["revenue"] == "2980.00" and d["by_day"][0]["revenue"] == "0.00"


def test_sales_empty_period_is_all_zero(admin, db):
    d = admin.get(f"{A}/reports/sales/", {"range": "today"}).data
    assert d["summary"] == {"orders": 0, "revenue": "0.00", "average_order_value": "0.00",
                            "returns": 0, "coupon_discounts": "0.00"}
    assert len(d["by_day"]) == 1 and d["top_products"] == []


@pytest.mark.parametrize("preset,days", [("today", 1), ("7d", 7), ("30d", 30)])
def test_presets_cover_the_expected_days(admin, db, preset, days):  # RPT-01
    assert len(admin.get(f"{A}/reports/sales/", {"range": preset}).data["by_day"]) == days


def test_this_month_and_custom_range(admin, data):  # RPT-01
    m = admin.get(f"{A}/reports/sales/", {"range": "month"}).data
    assert m["period"]["from"] == timezone.localdate().replace(day=1).isoformat()
    day = timezone.localdate().isoformat()
    c = admin.get(f"{A}/reports/sales/", {"range": "custom", "from": day, "to": day}).data
    assert c["summary"]["orders"] == 5 and len(c["by_day"]) == 1
    yesterday = (timezone.localdate() - timedelta(days=1)).isoformat()
    old = admin.get(f"{A}/reports/sales/", {"range": "custom", "from": yesterday, "to": yesterday})
    assert old.data["summary"]["orders"] == 0


@pytest.mark.parametrize("params", [
    {"range": "yearly"}, {"range": "custom"}, {"range": "custom", "from": "x", "to": "y"},
    {"range": "custom", "from": "2026-02-02", "to": "2026-02-01"},
    {"range": "custom", "from": "2024-01-01", "to": "2026-01-01"},
])
def test_bad_ranges_are_400(admin, params):
    assert admin.get(f"{A}/reports/sales/", params).status_code == 400


def test_old_orders_fall_outside_the_range(admin, data):
    Order.objects.update(placed_at=timezone.now() - timedelta(days=45))
    assert admin.get(f"{A}/reports/sales/", {"range": "30d"}).data["summary"]["orders"] == 0
    assert admin.get(f"{A}/reports/sales/", {"range": "custom", "from": (
        timezone.localdate() - timedelta(days=60)).isoformat(), "to": timezone.localdate(
        ).isoformat()}).data["summary"]["orders"] == 5


# ------------------------------------------------------------------ funnel / inventory / coupons


def test_funnel_counts_and_rates(admin, data):  # RPT-04
    rows = {r["stage"]: r for r in admin.get(f"{A}/reports/funnel/").data["rows"]}
    assert rows["placed"]["count"] == 5 and rows["placed"]["rate"] == 100
    assert (rows["confirmed"]["count"], rows["shipping"]["count"], rows["delivered"]["count"]) == (
        3, 2, 2)  # confirmed, delivered, returned all reached Confirmed
    assert (rows["cancelled"]["count"], rows["returned"]["count"]) == (1, 1)
    assert rows["confirmed"]["rate"] == 60.0 and rows["cancelled"]["rate"] == 20.0


def test_funnel_with_no_orders_has_zero_rates(admin, db):
    assert all(r["rate"] == 0 and r["count"] == 0 for r in admin.get(
        f"{A}/reports/funnel/").data["rows"])


def test_inventory_report_low_out_and_movement_summary(admin, data):  # RPT-05
    configure_site(low_stock_threshold=5)
    one_variant("Low One", on_hand=2)
    one_variant("Out One", on_hand=0)
    d = admin.get(f"{A}/reports/inventory/").data
    assert [r["product"] for r in d["low"]] == ["Low One"]
    assert [r["product"] for r in d["out"]] == ["Out One"]
    assert len(d["stock"]) >= 4 and d["threshold"] == 5
    by_reason = {m["reason"]: m for m in d["movements"]}
    assert by_reason["order_confirmed"]["units"] < 0 and by_reason["order_returned"]["units"] > 0


def test_coupon_report_counts_non_cancelled_usage(admin, data):  # RPT-06
    cancelled_with_code = make(data["bra"], 1, "cancelled", phone="01855555555",
                               coupon_code="WELCOME10")
    assert cancelled_with_code.coupon_code == "WELCOME10"
    rows = admin.get(f"{A}/reports/coupons/").data["rows"]
    assert rows == [{"code": "WELCOME10", "uses": 1, "discount": "200.00", "revenue": "1860.00"}]


# ------------------------------------------------------------------ CSV (RPT-07)


@pytest.mark.parametrize("report,section,header", [
    ("sales", "summary", "metric,value"), ("sales", "by_day", "day,revenue,orders"),
    ("sales", "by_category", "name,revenue,units"), ("sales", "top_products", "name,category"),
    ("sales", "by_payment_method", "key,orders,revenue"), ("sales", "by_area", "key,orders,revenue"),
    ("funnel", "funnel", "stage,count,rate"), ("inventory", "stock", "sku,product"),
    ("inventory", "low", "sku,product"), ("inventory", "out", "sku,product"),
    ("inventory", "movements", "reason,movements,units"), ("coupons", "coupons", "code,uses"),
])
def test_every_report_section_exports_csv(admin, data, report, section, header):
    r = admin.get(f"{A}/reports/{report}/", {"export": "csv", "section": section})
    assert r.status_code == 200 and r["Content-Type"].startswith("text/csv")
    assert f"{report}-{section}-" in r["Content-Disposition"]
    first = r.content.decode().splitlines()[0] if r.content else ""
    assert first.startswith(header) or (section in ("low", "out") and first == "")


def test_csv_default_section_and_unknown_section(admin, data):
    assert admin.get(f"{A}/reports/sales/", {"export": "csv"}).content.decode().startswith(
        "metric,value")
    assert admin.get(f"{A}/reports/sales/", {"export": "csv", "section": "nope"}
                     ).status_code == 400


# ------------------------------------------------------------------ dashboard (ADM-02)


def test_dashboard_owner_sees_everything(admin, data):
    d = admin.get(f"{A}/dashboard/").data
    assert d["today"]["orders"] == 5 and d["today"]["revenue"] == "2980.00"
    assert d["awaiting_confirmation"] == 1 and d["stale_drafts"] == 0
    assert len(d["sales_7d"]) == 7 and d["top_products"][0]["name"] == "A Bra"
    assert d["orders_by_stage"]["draft"] == 1 and d["orders_by_stage"]["returned"] == 1
    assert len(d["recent_orders"]) == 5 and d["stock"]["threshold"] >= 0


def test_dashboard_stale_drafts_and_payments_to_verify_oldest_first(admin, db):  # ORD-12, PAY-08
    v = one_variant(on_hand=50)
    a, _ = place_order(bkash(v, trx="TRXAAAAA01", phone="01711111111"))
    b, _ = place_order(bkash(v, trx="TRXBBBBB02", phone="01722222222"))
    cod = place_order(payload(v, phone="01733333333"))[0]
    Order.objects.filter(pk=a.pk).update(placed_at=timezone.now() - timedelta(hours=30))
    d = admin.get(f"{A}/dashboard/").data
    assert d["stale_drafts"] == 1 and d["awaiting_confirmation"] == 3
    assert [p["number"] for p in d["payments_to_verify"]] == [a.number, b.number]
    assert cod.number not in str(d["payments_to_verify"])
    orders.verify_payment(a.id, User.objects.first())
    assert [p["number"] for p in admin.get(f"{A}/dashboard/").data["payments_to_verify"]] == [
        b.number]


def test_dashboard_low_stock_counts(admin, db):  # INV-11
    configure_site(low_stock_threshold=5)
    one_variant("Low", on_hand=2)
    one_variant("Out", on_hand=0)
    s = admin.get(f"{A}/dashboard/").data["stock"]
    assert s["low"] == 1 and s["out"] == 1 and s["low_items"][0]["on_hand"] == 2


def test_dashboard_widgets_follow_permissions(as_user, make_staff, data):  # ADM-02, scenario 28
    only_dash = as_user(make_staff("dashboard.view")).get(f"{A}/dashboard/").data
    assert only_dash == {}  # no area permissions: no widgets
    orders_only = as_user(make_staff("dashboard.view", "orders.view")).get(f"{A}/dashboard/").data
    assert "today" in orders_only and "stock" not in orders_only
    stock_only = as_user(make_staff("dashboard.view", "inventory.view")).get(f"{A}/dashboard/").data
    assert "stock" in stock_only and "today" not in stock_only
    assert as_user(make_staff("orders.view")).get(f"{A}/dashboard/").status_code == 403


# ------------------------------------------------------------------ wishlist insights (INS-01)


def test_wishlist_overview_savers_and_conversion(admin, db):
    configure_site()
    hot, cold = one_variant("Hot", on_hand=50), one_variant("Cold", on_hand=50)
    users = [User.objects.create_user(phone=f"0171000000{i}", password="x-pass-1234",
                                      full_name=f"Cust {i}", is_active=True) for i in range(3)]
    for u in users:
        WishlistItem.objects.create(user=u, product=hot.product)
    WishlistItem.objects.create(user=users[0], product=cold.product)
    o = place_order(payload(hot), user=users[0])[0]
    orders.transition(o.id, "confirmed")
    d = admin.get(f"{A}/insights/wishlist/").data
    assert d["kpis"] == {"saves": 4, "customers": 3, "products": 2}
    top = d["products"][0]
    assert top["name"] == "Hot" and top["saved_by"] == 3 and top["bought_by_savers"] == 1
    assert top["conversion"] == 33.3 and top["units_sold"] == 1
    assert d["products"][1]["name"] == "Cold" and d["products"][1]["conversion"] == 0
    who = admin.get(f"{A}/insights/wishlist/{hot.product_id}/").data
    assert who["product"] == "Hot" and {c["name"] for c in who["customers"]} == {
        "Cust 0", "Cust 1", "Cust 2"}
    assert admin.get(f"{A}/insights/wishlist/99999/").status_code == 404


def test_wishlist_empty_state(admin, db):
    d = admin.get(f"{A}/insights/wishlist/").data
    assert d["kpis"] == {"saves": 0, "customers": 0, "products": 0} and d["products"] == []


# ------------------------------------------------------------------ permissions (PRM-03/08)


@pytest.mark.parametrize("path,code", [
    ("/reports/sales/", "reports.view"), ("/reports/funnel/", "reports.view"),
    ("/reports/inventory/", "reports.view"), ("/reports/coupons/", "reports.view"),
    ("/insights/wishlist/", "insights.view"), ("/dashboard/", "dashboard.view"),
])
def test_report_endpoints_need_their_permission(as_user, make_staff, path, code):
    other = "theme.manage"
    assert as_user(make_staff(other)).get(f"{A}{path}").status_code == 403
    assert as_user(make_staff(code)).get(f"{A}{path}").status_code == 200


def test_report_endpoints_refuse_anonymous_and_customers(api_client, as_user, db):
    assert api_client.get(f"{A}/reports/sales/").status_code in (401, 403)
    c = User.objects.create_user(phone="01712345678", password="x-pass-1234", is_active=True)
    assert as_user(c).get(f"{A}/reports/sales/").status_code == 403
    assert as_user(c).get(f"{A}/dashboard/").status_code == 403
