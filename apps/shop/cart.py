"""Cart validation. SRS CRT-02/03/08/09: re-validate lines against the server; never trusted
for pricing (checkout recomputes everything, CHK-06)."""

from decimal import Decimal

from apps.catalog.models import Variant

MAX_QTY = 10  # CRT-02 / §10: quantity per line 1–10
MAX_LINES = 30  # CRT-07


def effective_unit_price(variant: Variant) -> Decimal:
    return variant.price


def variant_is_orderable(variant: Variant) -> bool:
    p = variant.product
    return variant.is_active and p.is_active and p.category.is_active


def load_variants(ids):
    return {v.id: v for v in Variant.objects.filter(pk__in=ids)
            .select_related("product__category", "product__subcategory")}


def validate_lines(lines):
    """Per line: current price, available quantity (capped at the 10-per-line limit so exact
    stock is never exposed), active flag, and issue codes the UI turns into one-click fixes."""
    ids = [int(ln["variant"]) for ln in lines]
    variants = load_variants(ids)
    out, subtotal = [], Decimal(0)
    for raw in lines:
        vid, qty = int(raw["variant"]), int(raw["quantity"])
        v = variants.get(vid)
        if v is None:
            out.append({"variant": vid, "quantity": qty, "active": False, "price": None,
                        "available_quantity": 0, "issues": ["unavailable"]})
            continue
        active = variant_is_orderable(v)
        avail = min(v.on_hand, MAX_QTY) if active else 0
        issues = []
        if not active:
            issues.append("unavailable")  # deactivated product / variant
        elif avail == 0:
            issues.append("sold_out")
        elif qty > avail:
            issues.append("quantity_above_stock")
        if not 1 <= qty <= MAX_QTY:
            issues.append("quantity_invalid")
        price = effective_unit_price(v)
        if active and avail and not issues:
            subtotal += price * qty
        img = next(iter(v.product.images.all()), None)
        out.append({
            "variant": vid, "quantity": qty, "active": active, "price": str(price),
            "available_quantity": avail, "issues": issues,
            "product_name": v.product.name, "product_slug": v.product.slug,
            "variant_label": " / ".join(x for x in (v.color_name, v.size_label) if x),
            "sku": v.sku, "image": img.media.file.url if img else None,
        })
    return out, subtotal
