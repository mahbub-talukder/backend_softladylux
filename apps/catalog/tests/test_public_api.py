# Covers SRS CAT-05, CAT-09..13, CAT-20, CAT-22, WEB-03/06; scenarios 2, 13, 23.
import pytest

from apps.catalog.models import Product, SizeChart

from .factories import category, product

pytestmark = pytest.mark.django_db
P = "/api/v1/products/"


def slugs(r):
    return [p["slug"] for p in r.data["results"]]


@pytest.fixture
def bras(db):
    category("bras", ["Maternity Bra", "Air Bra", "Tube Bra"])
    return {
        "maternity": product("Maternity One", sub="maternity-bra", price="1200", sizes=("34B",), colours=("Rose",)),
        "air": product("Air One", sub="air-bra", price="1500", compare="1875", sizes=("34B", "36C"), colours=("Black",), collection="Silk"),
        "tube": product("Tube One", sub="tube-bra", price="900", sizes=("32A",), colours=("Rose",), on_hand=0),
    }


def test_categories_with_nested_subs_and_counts(api_client, bras):  # CAT-17/20
    data = {c["slug"]: c for c in api_client.get("/api/v1/categories/").data}
    assert len(data) == 8 and data["bras"]["count"] == 3
    assert {s["slug"]: s["count"] for s in data["bras"]["subcategories"]} == {
        "maternity-bra": 1, "air-bra": 1, "tube-bra": 1}
    assert data["tops"]["subcategories"] == []


def test_inactive_category_and_sub_hidden(api_client, bras):
    from apps.catalog.models import Category, SubCategory
    SubCategory.objects.filter(slug="tube-bra").update(is_active=False)
    subs = [s["slug"] for c in api_client.get("/api/v1/categories/").data if c["slug"] == "bras"
            for s in c["subcategories"]]
    assert "tube-bra" not in subs
    assert "tube-one" in slugs(api_client.get(P, {"category": "bras"}))  # products stay under category
    Category.objects.filter(slug="bras").update(is_active=False)
    assert slugs(api_client.get(P)) == []


def test_scenario_23_sub_filter_or_and_url(api_client, bras):
    r = api_client.get(P, {"category": "bras", "sub": "air-bra,tube-bra"})
    assert sorted(slugs(r)) == ["air-one", "tube-one"]  # OR across selected subs
    assert len(slugs(api_client.get(P, {"category": "bras"}))) == 3  # cleared = all of Bras
    assert slugs(api_client.get(P, {"category": "bras", "sub": "maternity-bra"})) == ["maternity-one"]


def test_scenario_13_category_page_only_that_category(api_client, bras):
    product("A Panty", cat="panties", sizes=("M",))
    assert "a-panty" not in slugs(api_client.get(P, {"category": "bras"}))
    assert slugs(api_client.get(P, {"category": "panties"})) == ["a-panty"]


def test_filters_combine_with_and(api_client, bras):  # CAT-20
    base = {"category": "bras"}
    assert slugs(api_client.get(P, {**base, "size": "36C"})) == ["air-one"]
    assert sorted(slugs(api_client.get(P, {**base, "colour": "rose"}))) == ["maternity-one", "tube-one"]
    assert slugs(api_client.get(P, {**base, "collection": "Silk"})) == ["air-one"]
    assert slugs(api_client.get(P, {**base, "price_max": "1000"})) == ["tube-one"]
    assert slugs(api_client.get(P, {**base, "price_min": "1300"})) == ["air-one"]
    assert sorted(slugs(api_client.get(P, {**base, "available": "1"}))) == ["air-one", "maternity-one"]
    assert slugs(api_client.get(P, {**base, "sub": "air-bra,tube-bra", "colour": "Rose"})) == ["tube-one"]


def test_offers_new_and_search(api_client, bras):
    assert slugs(api_client.get(P, {"offers": 1})) == ["air-one"]  # CAT-06
    product("Flagged", is_new=True)
    assert slugs(api_client.get(P, {"new": 1})) == ["flagged"]
    assert slugs(api_client.get(P, {"q": "maternity"})) == ["maternity-one"]
    assert slugs(api_client.get(P, {"q": "AIR-ONE-BLACK"})) == ["air-one"]  # by SKU
    assert "air-one" in slugs(api_client.get(P, {"q": "air bra"}))  # sub-category name


def test_sort_and_pagination_showing_x_of_y(api_client, bras):  # CAT-09
    asc = slugs(api_client.get(P, {"category": "bras", "sort": "price_asc"}))
    assert asc == ["tube-one", "maternity-one", "air-one"]
    assert slugs(api_client.get(P, {"category": "bras", "sort": "price_desc"}))[0] == "air-one"
    r = api_client.get(P, {"category": "bras", "page_size": 2})
    assert r.data["count"] == 3 and len(r.data["results"]) == 2 and r.data["next"]
    assert slugs(api_client.get(P, {"category": "bras", "sort": "newest"}))[0] == "tube-one"


def test_facets(api_client, bras):  # CAT-20
    f = api_client.get(P, {"category": "bras", "sub": "air-bra"}).data["facets"]
    assert {s["slug"]: s["count"] for s in f["subcategories"]} == {
        "maternity-bra": 1, "air-bra": 1, "tube-bra": 1}  # counts ignore the sub filter itself
    assert f["sizes"] == ["32A", "34B", "36C"] or "34B" in f["sizes"]
    assert f["price"] == {"min": "900.00", "max": "1500.00"}
    assert "Silk" in api_client.get(P, {"category": "bras"}).data["facets"]["collections"]


def test_card_shape_badge_soldout_no_stock_leak(api_client, bras):  # CAT-05/13
    r = {c["slug"]: c for c in api_client.get(P, {"category": "bras"}).data["results"]}
    assert r["air-one"]["discount_pct"] == 20 and r["air-one"]["compare_at_price"] == "1875.00"
    assert r["tube-one"]["sold_out"] is True and r["air-one"]["sold_out"] is False
    assert "on_hand" not in str(r["air-one"])
    v = r["air-one"]["variants"]  # CAT-12 Quick Add data
    assert [x["size_label"] for x in v] == ["34B", "36C"] and all(x["in_stock"] for x in v)
    assert r["tube-one"]["variants"][0]["in_stock"] is False


def test_draft_products_not_public(api_client):
    product("Hidden", active=False)
    assert slugs(api_client.get(P)) == []
    assert api_client.get(f"{P}hidden/").status_code == 404


def test_detail_variants_breadcrumb_related(api_client, bras):  # CAT-11/22
    d = api_client.get(f"{P}air-one/").data
    assert [b["slug"] for b in d["breadcrumb"]] == ["bras", "air-bra"]
    assert len(d["variants"]) == 2 and "on_hand" not in d["variants"][0]
    assert d["variants"][0]["in_stock"] is True and len(d["images"]) == 1
    assert {r["slug"] for r in d["related"]} == {"maternity-one", "tube-one"}
    assert api_client.get(f"{P}nope/").status_code == 404


def test_low_stock_only_n_left(api_client, db):  # CAT-11
    product("Few", on_hand=3, sizes=("M",), colours=("Rose",))
    v = api_client.get(f"{P}few/").data["variants"][0]
    assert v["low_stock_left"] == 3 and v["in_stock"] is True


def test_suggest_top_six_min_two_chars(api_client, db):  # CAT-10
    for i in range(8):
        product(f"Lace Bra {i}")
    assert api_client.get("/api/v1/search/suggest/", {"q": "l"}).data == []
    r = api_client.get("/api/v1/search/suggest/", {"q": "lace"}).data
    assert len(r) == 6 and {"name", "slug", "price", "image"} <= set(r[0])


def test_size_charts(api_client):  # CAT-08
    d = {c["type"]: c for c in api_client.get("/api/v1/size-charts/").data}
    assert {"bra", "apparel"} <= set(d) and d["bra"]["rows"]["cups"][0]["cup"] == "A"
    assert SizeChart.objects.count() == 2


def test_home_sections_fill_from_flags(api_client, db):  # WEB-03/06
    product("Sig", is_signature=True)
    product("Deal", compare="1500")
    product("Fresh", is_new=True)
    h = api_client.get("/api/v1/home/").data
    assert [p["slug"] for p in h["signature"]] == ["sig"]
    assert [p["slug"] for p in h["offers"]] == ["deal"]
    assert [p["slug"] for p in h["new_arrivals"]] == ["fresh"]
    assert len(h["categories"]) == 8 and Product.objects.count() == 3


def test_listing_query_count_is_flat(api_client, bras, django_assert_max_num_queries):
    for i in range(10):
        product(f"Extra {i}", cat="bras", sub="air-bra")
    with django_assert_max_num_queries(25):
        api_client.get(P, {"category": "bras"})


def test_category_csv_and_cross_category_facet(api_client, bras):  # CAT-20
    product("A Panty", cat="panties", compare="1500")
    both = slugs(api_client.get(P, {"category": "bras,panties"}))
    assert len(both) == 4
    r = api_client.get(P, {"offers": 1})
    cats = {c["slug"]: c["count"] for c in r.data["facets"]["categories"]}
    assert cats == {"bras": 1, "panties": 1} and r.data["facets"]["subcategories"] == []
    only = api_client.get(P, {"category": "bras"}).data["facets"]
    assert only["categories"] == [] and len(only["subcategories"]) == 3
