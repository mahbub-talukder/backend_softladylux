# Covers SRS CAT-02/03/05/06/07/14/17/19/21, INV-08/09, rule 9.
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.catalog import services
from apps.catalog.models import Product, SubCategory
from apps.inventory.models import Reason, StockMovement
from apps.inventory.services import record_movement

from .factories import category, product

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("price,compare,pct", [
    ("1000", "1250", 20), ("1000", "1500", 33), ("1000", "1000", 0), ("1000", None, 0),
    ("1000", "900", 0), ("2450", "3000", 18),
])
def test_discount_badge_rounding(price, compare, pct):  # CAT-05
    p = Product(price=Decimal(price), compare_at_price=Decimal(compare) if compare else None)
    assert p.discount_pct == pct


def test_offer_logic(db):  # CAT-06
    assert product("A", compare="1500").on_offer is True
    assert product("B", is_offer=True).on_offer is True
    assert product("C").on_offer is False


def test_new_arrivals_flag_then_30_day_fallback(db):  # CAT-07
    old = product("Old")
    Product.objects.filter(pk=old.pk).update(created_at=timezone.now() - timezone.timedelta(days=60))
    fresh = product("Fresh")
    ids = set(Product.objects.filter(services.new_arrivals_q()).values_list("id", flat=True))
    assert ids == {fresh.id}  # nothing flagged: recent ones
    flagged = product("Flagged", is_new=True)
    ids = set(Product.objects.filter(services.new_arrivals_q()).values_list("id", flat=True))
    assert ids == {flagged.id}  # flags win


def test_unique_slug_suffix(db):  # CAT-02
    product("Plunge Bra")
    assert services.unique_slug(Product, "Plunge Bra") == "plunge-bra-2"
    assert services.unique_slug(Product, "Plunge Bra", exclude_pk=Product.objects.first().pk) == "plunge-bra"


def test_subcategory_name_unique_case_insensitive_per_category(db):  # CAT-17
    bras = category("bras", ["Air Bra"])
    with pytest.raises(IntegrityError), transaction.atomic():
        SubCategory.objects.create(category=bras, name="air bra", slug="air-bra-2")
    other = category("panties")
    SubCategory.objects.create(category=other, name="Air Bra", slug="air-bra")  # other category ok


def test_taxonomy_rules(db):  # CAT-19
    bras = category("bras", ["Air Bra"])
    panties = category("panties", ["Thong"])
    air = bras.subcategories.get()
    with pytest.raises(ValidationError):
        services.check_taxonomy(bras, None)  # required: the category has active sub-categories
    with pytest.raises(ValidationError):
        services.check_taxonomy(panties, air)  # wrong category
    services.check_taxonomy(bras, air)
    services.check_taxonomy(category("tops"), None)  # optional: no sub-categories
    air.is_active = False
    air.save()
    services.check_taxonomy(bras, None)  # inactive sub-categories don't count


def test_cannot_delete_subcategory_with_products(db):  # CAT-21
    category("bras", ["Air Bra"])
    p = product(sub="air-bra")
    with pytest.raises(ValidationError):
        services.delete_subcategory_allowed(p.subcategory)


def test_publish_requires_image_and_variant(db):  # CAT-04
    p = product(images=0, active=False)
    with pytest.raises(ValidationError):
        services.check_publishable(p)
    p2 = product("With", images=1, active=False)
    services.check_publishable(p2)


def test_make_sku_unique_uppercase(db):  # CAT-03, §10
    p = product("Plunge Bra", sizes=(), colours=())
    a = services.create_variant(p, {"size_label": "34B", "color_name": "Rose"})
    b = services.create_variant(p, {"size_label": "34B", "color_name": "Rose"})
    assert a.sku == a.sku.upper() and a.sku != b.sku


def test_opening_stock_writes_movement(db, owner):  # ADM-03, INV-08
    p = product(sizes=(), colours=())
    v = services.create_variant(p, {"size_label": "M", "on_hand": 7}, owner)
    m = StockMovement.objects.get(variant=v)
    assert (m.reason, m.delta_on_hand, m.on_hand_after, m.user) == (Reason.OPENING_STOCK, 7, 7, owner)
    v.refresh_from_db()
    assert v.on_hand == 7


def test_movement_never_below_zero_and_immutable(db, owner):  # rule 9, rule 11
    p = product(sizes=("M",), colours=("Rose",), on_hand=3)
    v = p.variants.get()
    record_movement(v, -2, Reason.ADJUSTMENT_OUT, owner, lot_reference="L1", remarks="damaged")
    v.refresh_from_db()
    assert v.on_hand == 1
    with pytest.raises(ValidationError):
        record_movement(v, -5, Reason.ADJUSTMENT_OUT, owner)
    v.refresh_from_db()
    assert v.on_hand == 1 and StockMovement.objects.count() == 1
    m = StockMovement.objects.get()
    with pytest.raises(RuntimeError):
        m.save()
    with pytest.raises(RuntimeError):
        m.delete()


def test_product_without_orders_hook(db):  # CAT-14 (orders arrive in M3)
    assert product().has_orders() is False
