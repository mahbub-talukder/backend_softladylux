# Covers SRS v1.7 CAT-24, CAT-25, CAT-26 and §10 weight bounds; scenario 31.
import pytest

from apps.catalog.models import Category
from apps.shop.checkout import place_order
from apps.shop.tests.helpers import configure_site, one_variant, payload

pytestmark = pytest.mark.django_db
A = "/api/v1/admin"


@pytest.fixture
def admin(as_user, owner):
    return as_user(owner)


def body(**kw):
    cat = Category.objects.get(slug="tops")
    return {"name": "Silk Top", "category": cat.id, "price": "1200", **kw}


def test_new_product_defaults_to_100_grams_and_variant_inherits(admin):  # CAT-24, CAT-25
    r = admin.post(f"{A}/products/", body(variants=[{"size_label": "S", "color_name": "Rose"}]),
                   format="json")
    assert r.status_code == 201
    assert r.data["weight_grams"] == 100 and r.data["variants"][0]["weight_grams"] is None


def test_variant_override_and_effective_weight(admin):  # CAT-25: variant ?? product
    r = admin.post(f"{A}/products/", body(weight_grams=180, variants=[
        {"size_label": "S", "color_name": "Rose"},
        {"size_label": "M", "color_name": "Rose", "weight_grams": 250}]), format="json")
    assert r.status_code == 201, r.data
    from apps.catalog.models import Variant

    weights = {v.size_label: v.weight for v in Variant.objects.filter(product_id=r.data["id"])}
    assert weights == {"S": 180, "M": 250}


@pytest.mark.parametrize("bad", [0, 30001])
def test_weight_bounds_rejected(admin, bad):  # §10: 1–30,000 grams
    r = admin.post(f"{A}/products/", body(weight_grams=bad), format="json")
    assert r.status_code == 400 and "weight_grams" in str(r.data)
    r = admin.post(f"{A}/products/", body(variants=[
        {"size_label": "S", "color_name": "Rose", "weight_grams": bad}]), format="json")
    assert r.status_code == 400 and "weight_grams" in str(r.data)


def test_scenario_31_order_line_keeps_weight_snapshot(as_user, owner):  # CAT-26, rule 10
    configure_site()
    v = one_variant()
    v.weight_grams = 250
    v.save()
    order, _ = place_order(payload(v, 2))
    v.weight_grams = 300
    v.save()
    line = order.items.get()
    line.refresh_from_db()
    assert line.weight_grams == 250 and order.total_weight_grams == 500
    r = as_user(owner).get(f"{A}/orders/{order.id}/")
    assert r.data["total_weight_grams"] == 500 and r.data["items"][0]["weight_grams"] == 250


def test_customer_order_payload_has_no_weight(api_client):  # amendment: public payload unchanged
    configure_site()
    v = one_variant()
    order, _ = place_order(payload(v))
    r = api_client.post("/api/v1/track/", {"number": order.number, "phone": order.phone},
                        format="json")
    assert r.status_code == 200 and "weight_grams" not in r.data["items"][0]
