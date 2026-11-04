# Covers SRS ADM-03, CAT-03/04/14/15/18/19/21/22, PRM-03/05, INV-08; scenarios 2, 22.
import pytest

from apps.catalog.models import Category, Product, ProductImage, SubCategory, Variant
from apps.core.models import AuditLog
from apps.core.tests.helpers import image_file
from apps.inventory.models import StockMovement

from .factories import category, product

pytestmark = pytest.mark.django_db
A = "/api/v1/admin"


@pytest.fixture
def admin(as_user, owner):
    return as_user(owner)


# ----------------------------------------------------------------- categories


def test_scenario_22_category_with_subcategories_in_one_save(admin):
    bras = Category.objects.get(slug="bras")
    r = admin.patch(f"{A}/categories/{bras.id}/", {"subcategories": [
        {"name": "Maternity Bra", "sort": 0}, {"name": "Air Bra", "sort": 1},
        {"name": "Tube Bra", "sort": 2}]}, format="json")
    assert r.status_code == 200
    assert [s["slug"] for s in r.data["subcategories"]] == ["maternity-bra", "air-bra", "tube-bra"]
    pub = {c["slug"]: c for c in admin.get("/api/v1/categories/").data}
    assert len(pub["bras"]["subcategories"]) == 3
    assert AuditLog.objects.filter(model="Category", action="update").exists()


def test_create_category_with_subs_and_list(admin):
    r = admin.post(f"{A}/categories/", {"name": "Hosiery", "subcategories": [
        {"name": "Socks"}]}, format="json")
    assert r.status_code == 201 and r.data["slug"] == "hosiery"
    assert admin.get(f"{A}/categories/").data[-1]["name"] == "Hosiery"
    assert admin.get(f"{A}/categories/{r.data['id']}/").data["subcategories"][0]["name"] == "Socks"


def test_subcategory_validation(admin):  # CAT-17
    bras = Category.objects.get(slug="bras")
    url = f"{A}/categories/{bras.id}/"
    assert admin.patch(url, {"subcategories": [{"name": "A"}]}, format="json").status_code == 400
    assert admin.patch(url, {"subcategories": [{"name": "Air Bra"}, {"name": "air bra"}]},
                       format="json").status_code == 400


def test_rename_keeps_slug_and_reorder_deactivate(admin):  # CAT-21
    category("bras", ["Air Bra"])
    bras = Category.objects.get(slug="bras")
    sub = bras.subcategories.get()
    r = admin.patch(f"{A}/categories/{bras.id}/", {"subcategories": [
        {"id": sub.id, "name": "Airy Bra", "sort": 5, "is_active": False}]}, format="json")
    row = r.data["subcategories"][0]
    assert (row["name"], row["slug"], row["sort"], row["is_active"]) == (
        "Airy Bra", "air-bra", 5, False)


def test_removing_sub_with_products_blocked_without_products_ok(admin):  # CAT-21
    category("bras", ["Air Bra", "Tube Bra"])
    product(sub="air-bra")
    bras = Category.objects.get(slug="bras")
    air, tube = (bras.subcategories.get(slug=s) for s in ("air-bra", "tube-bra"))
    keep_air = [{"id": air.id, "name": "Air Bra"}]
    assert admin.patch(f"{A}/categories/{bras.id}/", {"subcategories": keep_air},
                       format="json").status_code == 200  # tube had no products: removed
    assert not SubCategory.objects.filter(pk=tube.pk).exists()
    r = admin.patch(f"{A}/categories/{bras.id}/", {"subcategories": []}, format="json")
    assert r.status_code == 400 and "Deactivate" in str(r.data)  # air has a product


def test_category_delete_blocked_with_products(admin):
    product()
    bras = Category.objects.get(slug="bras")
    assert admin.delete(f"{A}/categories/{bras.id}/").status_code == 400
    empty = Category.objects.get(slug="tops")
    assert admin.delete(f"{A}/categories/{empty.id}/").status_code == 204


# ----------------------------------------------------------------- products


def body(**kw):
    cat = Category.objects.get(slug="tops")
    return {"name": "Silk Top", "category": cat.id, "price": "1200", **kw}


def test_create_product_draft_with_variants_and_opening_stock(admin, owner):  # scenario 2
    r = admin.post(f"{A}/products/", body(variants=[
        {"size_label": "S", "color_name": "Rose", "on_hand": 4},
        {"size_label": "M", "color_name": "Rose", "on_hand": 6}]), format="json")
    assert r.status_code == 201 and r.data["slug"] == "silk-top" and r.data["is_active"] is False
    assert r.data["stock"] == 10 and len(r.data["variants"]) == 2
    assert StockMovement.objects.filter(reason="opening_stock").count() == 2
    assert StockMovement.objects.first().user == owner


def test_default_variant_created_when_none_given(admin):  # CAT-03
    r = admin.post(f"{A}/products/", body(), format="json")
    assert len(r.data["variants"]) == 1 and r.data["variants"][0]["sku"]


def test_publish_flow_needs_image(admin):  # CAT-04, scenario 2
    pid = admin.post(f"{A}/products/", body(), format="json").data["id"]
    assert admin.post(f"{A}/products/", body(is_active=True), format="json").status_code == 400
    r = admin.patch(f"{A}/products/{pid}/", {"is_active": True}, format="json")
    assert r.status_code == 400 and "image" in str(r.data).lower()
    up = admin.post(f"{A}/products/{pid}/images/", {"files": [image_file("a.png", size=(700, 900))]},
                    format="multipart")
    assert up.status_code == 201 and up.data[0]["is_primary"] is True
    assert admin.patch(f"{A}/products/{pid}/", {"is_active": True}, format="json").status_code == 200


def test_validation_rules(admin):  # CAT-02, CAT-19
    assert admin.post(f"{A}/products/", body(compare_at_price="1000", price="1200"),
                      format="json").status_code == 400
    assert admin.post(f"{A}/products/", body(price="-1"), format="json").status_code == 400
    category("bras", ["Air Bra"])
    bras = Category.objects.get(slug="bras")
    assert admin.post(f"{A}/products/", {"name": "B", "category": bras.id, "price": "10"},
                      format="json").status_code == 400  # sub-category required
    other = SubCategory.objects.create(category=Category.objects.get(slug="panties"),
                                       name="Thong", slug="thong")
    assert admin.post(f"{A}/products/", {"name": "B", "category": bras.id, "price": "10",
                                         "subcategory": other.id}, format="json").status_code == 400
    ok = admin.post(f"{A}/products/", {"name": "B", "category": bras.id, "price": "10",
                                       "subcategory": bras.subcategories.get().id}, format="json")
    assert ok.status_code == 201 and ok.data["subcategory_name"] == "Air Bra"


def test_changing_category_clears_subcategory(admin):  # CAT-19
    category("bras", ["Air Bra"])
    p = product(sub="air-bra")
    tops = Category.objects.get(slug="tops")
    r = admin.patch(f"{A}/products/{p.id}/", {"category": tops.id}, format="json")
    assert r.status_code == 200 and r.data["subcategory"] is None


def test_variant_sync_update_add_deactivate_no_stock_edit(admin):
    pid = admin.post(f"{A}/products/", body(variants=[
        {"size_label": "S", "on_hand": 5}, {"size_label": "M", "on_hand": 5}]),
        format="json").data
    s, m = pid["variants"]
    r = admin.patch(f"{A}/products/{pid['id']}/", {"variants": [
        {"id": s["id"], "size_label": "S", "price_override": "999", "on_hand": 999},
        {"size_label": "L", "on_hand": 2}]}, format="json")
    by = {v["size_label"]: v for v in r.data["variants"]}
    assert by["S"]["on_hand"] == 5 and by["S"]["price_override"] == "999.00"  # stock untouched
    assert by["M"]["is_active"] is False and by["L"]["on_hand"] == 2
    assert Variant.objects.filter(pk=m["id"]).exists()  # never deleted


def test_duplicate_sku_rejected(admin):
    a = admin.post(f"{A}/products/", body(variants=[{"sku": "dup-1"}]), format="json")
    assert a.data["variants"][0]["sku"] == "DUP-1"
    b = admin.post(f"{A}/products/", body(name="Other", variants=[{"sku": "DUP-1"}]), format="json")
    assert b.status_code == 400
    bad = admin.post(f"{A}/products/", body(name="X", variants=[{"sku": "bad sku!"}]), format="json")
    assert bad.status_code == 400


def test_list_filters_and_row_shape(admin):
    p = product("Alpha")
    product("Beta", cat="panties", active=False)
    rows = admin.get(f"{A}/products/").data
    assert rows["count"] == 2 and {"image", "stock", "category_name"} <= set(rows["results"][0])
    assert admin.get(f"{A}/products/", {"q": "alph"}).data["count"] == 1
    assert admin.get(f"{A}/products/", {"status": "draft"}).data["count"] == 1
    assert admin.get(f"{A}/products/", {"category": p.category_id}).data["count"] == 1
    assert admin.get(f"{A}/products/", {"subcategory": 999}).data["count"] == 0
    assert admin.get(f"{A}/products/{p.id}/").data["name"] == "Alpha"


def test_ordered_product_is_only_deactivated_never_deleted(admin):  # CAT-14
    from apps.shop.checkout import place_order
    from apps.shop.tests.helpers import configure_site, payload

    configure_site()
    p = product("Ordered")
    place_order(payload(p.variants.get()))
    assert admin.delete(f"{A}/products/{p.id}/").status_code == 204
    p.refresh_from_db()
    assert p.is_active is False and Product.objects.filter(pk=p.pk).exists()


def test_delete_deactivates_when_stock_history_else_deletes(admin):  # CAT-14
    a = admin.post(f"{A}/products/", body(variants=[{"on_hand": 3}]), format="json").data
    assert admin.delete(f"{A}/products/{a['id']}/").status_code == 204
    assert Product.objects.get(pk=a["id"]).is_active is False  # has movements: kept
    b = admin.post(f"{A}/products/", body(name="Plain"), format="json").data
    admin.delete(f"{A}/products/{b['id']}/")
    assert not Product.objects.filter(pk=b["id"]).exists()


def test_bulk_actions(admin):  # CAT-15, CAT-21
    category("bras", ["Air Bra"])
    a, b = product("A1"), product("B1")
    url = f"{A}/products/bulk/"
    assert admin.post(url, {"ids": [a.id, b.id], "action": "deactivate"}, format="json").data["updated"] == 2
    assert not Product.objects.filter(is_active=True).exists()
    assert admin.post(url, {"ids": [a.id], "action": "activate"}, format="json").status_code == 200
    assert admin.post(url, {"ids": [a.id, b.id], "action": "set_flag", "flag": "is_new"},
                      format="json").status_code == 200
    assert Product.objects.filter(is_new=True).count() == 2
    assert admin.post(url, {"ids": [a.id], "action": "set_flag", "flag": "x"}, format="json").status_code == 400
    bras = Category.objects.get(slug="bras")
    assert admin.post(url, {"ids": [a.id], "action": "set_category", "category": bras.id},
                      format="json").status_code == 400  # needs a new sub-category
    ok = admin.post(url, {"ids": [a.id], "action": "set_category", "category": bras.id,
                          "subcategory": bras.subcategories.get().id}, format="json")
    assert ok.status_code == 200
    assert admin.post(url, {"ids": [], "action": "activate"}, format="json").status_code == 400
    assert admin.post(url, {"ids": [a.id], "action": "nope"}, format="json").status_code == 400
    assert AuditLog.objects.filter(action="bulk_deactivate").exists()


def test_bulk_activate_requires_publishable(admin):
    p = product("Bare", images=0, active=False)
    assert admin.post(f"{A}/products/bulk/", {"ids": [p.id], "action": "activate"},
                      format="json").status_code == 400


def test_export_csv_includes_subcategory(admin):  # CAT-15, CAT-22
    category("bras", ["Air Bra"])
    product("Csv One", sub="air-bra")
    r = admin.get(f"{A}/products/export/")
    text = r.content.decode()
    assert r["Content-Type"].startswith("text/csv") and "Air Bra" in text and "sub_category" in text


# ----------------------------------------------------------------- variants & images


def test_variants_endpoint(admin):
    p = product(sizes=("M",), colours=("Rose",))
    url = f"{A}/products/{p.id}/variants/"
    assert len(admin.get(url).data) == 1
    r = admin.post(url, {"size_label": "L", "color_name": "Rose", "on_hand": 2}, format="json")
    assert r.status_code == 201 and r.data["on_hand"] == 2
    assert StockMovement.objects.filter(variant_id=r.data["id"], reason="opening_stock").exists()
    upd = admin.patch(f"{url}{r.data['id']}/", {"is_active": False, "on_hand": 99}, format="json")
    assert upd.status_code == 200 and upd.data["is_active"] is False and upd.data["on_hand"] == 2


def test_images_multi_upload_primary_reorder_delete(admin):  # CAT-04
    p = product(images=0, active=False)
    url = f"{A}/products/{p.id}/images/"
    files = [image_file(f"{i}.png", size=(700, 900)) for i in range(3)]
    made = admin.post(url, {"files": files, "alt_text": "Front"}, format="multipart").data
    assert [i["is_primary"] for i in made] == [True, False, False]
    ids = [i["id"] for i in made]
    r = admin.patch(f"{url}{ids[2]}/", {"is_primary": True, "color": "Rose"}, format="json")
    assert r.data["is_primary"] is True and r.data["color"] == "Rose"
    assert ProductImage.objects.filter(product=p, is_primary=True).count() == 1
    rev = admin.post(f"{url}reorder/", {"ids": ids[::-1]}, format="json")
    assert [i["id"] for i in rev.data if True][:1] == [ids[2]]  # primary first, then sort
    assert admin.post(f"{url}reorder/", {"ids": ids[:1]}, format="json").status_code == 400
    assert admin.delete(f"{url}{ids[2]}/").status_code == 204
    assert ProductImage.objects.filter(product=p, is_primary=True).count() == 1  # promoted
    assert len(admin.get(url).data) == 2


def test_image_rules(admin):  # §10: >= 600 px, WEB-09
    p = product(images=0, active=False)
    url = f"{A}/products/{p.id}/images/"
    assert admin.post(url, {"files": [image_file(size=(200, 200))]}, format="multipart").status_code == 400
    assert admin.post(url, {}, format="multipart").status_code == 400
    assert not ProductImage.objects.filter(product=p).exists()


def test_deleting_last_image_unpublishes(admin):  # CAT-04
    p = product(images=1, active=True)
    img = p.images.get()
    admin.delete(f"{A}/products/{p.id}/images/{img.id}/")
    p.refresh_from_db()
    assert p.is_active is False


def test_size_chart_admin(admin):  # CAT-08
    chart = admin.get(f"{A}/size-charts/").data[0]
    assert admin.get(f"{A}/size-charts/{chart['id']}/").status_code == 200
    assert admin.patch(f"{A}/size-charts/{chart['id']}/", {"rows": [1]}, format="json").status_code == 400
    ok = dict(chart["rows"])
    ok["cups"] = ok["cups"][:3]
    r = admin.patch(f"{A}/size-charts/{chart['id']}/", {"rows": ok}, format="json")
    assert r.status_code == 200 and len(r.data["rows"]["cups"]) == 3
    assert admin.post(f"{A}/size-charts/", {}, format="json").status_code == 405


def test_product_writes_clear_public_cache_and_audit(admin, api_client):
    api_client.get("/api/v1/home/")
    p = product("Sig", is_signature=True)
    admin.patch(f"{A}/products/{p.id}/", {"price": "1100"}, format="json")
    assert api_client.get("/api/v1/home/").data["signature"][0]["price"] == "1100.00"
    assert AuditLog.objects.filter(model="Product", action="update").exists()


def test_put_full_update_product_and_category(admin):
    p = product("Putty", cat="tops")
    tops = Category.objects.get(slug="tops")
    r = admin.put(f"{A}/products/{p.id}/", {"name": "Putty 2", "category": tops.id, "price": "50",
                                            "is_active": True}, format="json")
    assert r.status_code == 200 and r.data["name"] == "Putty 2" and r.data["slug"] == "putty"
    c = admin.put(f"{A}/categories/{tops.id}/", {"name": "Tops & Tees", "slug": "tops"},
                  format="json")
    assert c.status_code == 200 and c.data["name"] == "Tops & Tees"


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(bands=[]),
    lambda r: r.update(cups=[{"cup": "", "min": 1, "max": 2}]),
    lambda r: r["bands"].__setitem__(0, {"size": 32, "min": 90, "max": 80}),
    lambda r: r["bands"].__setitem__(0, {"size": "x", "min": 1, "max": 2}),
    lambda r: r.update(cups=[{"cup": "B", "min": 14, "max": 16}, {"cup": "A", "min": 11, "max": 13}]),
])
def test_bra_chart_shape_enforced(admin, mutate):  # CAT-08: a bad edit can't break the calculator
    from apps.catalog.models import SizeChart

    chart = SizeChart.objects.get(type="bra")
    rows = {"bands": [dict(b) for b in chart.rows["bands"]], "cups": [dict(c) for c in chart.rows["cups"]]}
    mutate(rows)
    assert admin.patch(f"{A}/size-charts/{chart.id}/", {"rows": rows}, format="json").status_code == 400


def test_apparel_chart_shape_enforced(admin):
    from apps.catalog.models import SizeChart

    chart = SizeChart.objects.get(type="apparel")
    good = {"rows": [dict(r) for r in chart.rows["rows"]]}
    assert admin.patch(f"{A}/size-charts/{chart.id}/", {"rows": good}, format="json").status_code == 200
    for bad in ({"rows": []}, {"rows": [{"size": "", "bust": [1, 2], "waist": [1, 2], "hip": [1, 2]}]},
                {"rows": [{"size": "S", "bust": [9, 2], "waist": [1, 2], "hip": [1, 2]}]},
                {"rows": [{"size": "S", "bust": [1, 2], "waist": [1, 2], "hip": [100, 110]},
                          {"size": "M", "bust": [1, 2], "waist": [1, 2], "hip": [90, 99]}]}):
        assert admin.patch(f"{A}/size-charts/{chart.id}/", {"rows": bad}, format="json").status_code == 400
