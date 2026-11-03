"""Catalogue business rules. SRS CAT-02/05/06/07, CAT-14, CAT-17, CAT-19, CAT-21, §10."""

from datetime import timedelta

from django.db.models import F, Q
from django.utils import timezone
from django.utils.text import slugify
from rest_framework.exceptions import ValidationError

from .models import Category, Product, SubCategory


def unique_slug(model, base: str, exclude_pk=None, **scope) -> str:
    """CAT-02 / §10: lower-case slug, unique (within `scope`, e.g. a category)."""
    root = slugify(base)[:60] or "item"
    slug, n = root, 2
    qs = model.objects.filter(**scope)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    while qs.filter(slug=slug).exists():
        slug, n = f"{root}-{n}", n + 1
    return slug


def check_taxonomy(category: Category, subcategory: SubCategory | None):
    """CAT-19: sub-category must belong to the category and is required when the category has
    active sub-categories."""
    if subcategory is not None and subcategory.category_id != category.id:
        raise ValidationError({"subcategory": "This sub-category does not belong to the category."})
    if subcategory is None and category.subcategories.filter(is_active=True).exists():
        raise ValidationError({"subcategory": "Choose a sub-category for this category."})


def check_publishable(product: Product):
    """CAT-04: a product needs at least one image (and one active variant) to be published."""
    if not product.images.exists():
        raise ValidationError({"is_active": "Add at least one image before publishing."})
    if not product.variants.filter(is_active=True).exists():
        raise ValidationError({"is_active": "Add at least one active variant before publishing."})


def storefront() -> "Q":
    """Products visible to customers: active, in an active category."""
    return Q(is_active=True, category__is_active=True)


def new_arrivals_q() -> Q:
    """CAT-07: `is_new` flagged; when none are flagged, products created in the last 30 days."""
    if Product.objects.filter(storefront(), is_new=True).exists():
        return Q(is_new=True)
    return Q(created_at__gte=timezone.now() - timedelta(days=30))


def offers_q() -> Q:
    """CAT-06: compare-at above price, or flagged as an offer."""
    return Q(is_offer=True) | Q(compare_at_price__gt=F("price"))


def delete_subcategory_allowed(sub: SubCategory):
    """CAT-21: a sub-category with products can't be deleted; deactivate or move the products."""
    if sub.products.exists():
        raise ValidationError(
            f"“{sub.name}” has products. Deactivate it or move the products first.")


def make_sku(product: Product, size: str, color: str) -> str:
    """CAT-03 / ADM-03: generated variant SKU, editable; unique and upper-case."""
    from re import sub

    from .models import Variant

    prefix = product.sku_prefix or sub(r"[^A-Z0-9]", "", product.name.upper())[:8] or "SKU"
    parts = [prefix] + [sub(r"[^A-Z0-9]", "", x.upper())[:4] for x in (color, size) if x]
    base, sku, n = "-".join(parts), "-".join(parts), 2
    while Variant.objects.filter(sku=sku).exists():
        sku, n = f"{base}-{n}", n + 1
    return sku


def create_variant(product: Product, data: dict, user=None):
    """ADM-03: opening stock on creation writes an `opening_stock` movement (INV-08)."""
    from apps.inventory.models import Reason
    from apps.inventory.services import record_movement

    from .models import Variant

    data = dict(data)
    opening = int(data.pop("on_hand", 0) or 0)
    if not data.get("sku"):
        data["sku"] = make_sku(product, data.get("size_label", ""), data.get("color_name", ""))
    data["sku"] = data["sku"].upper()
    variant = Variant.objects.create(product=product, on_hand=0, **data)
    if opening:
        record_movement(variant, opening, Reason.OPENING_STOCK, user=user, remarks="Opening stock")
    return variant
