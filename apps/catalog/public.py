"""Customer-facing catalogue shapes and queries. SRS CAT-05, CAT-09..CAT-13, CAT-20, CAT-22.

Exact stock counts are never exposed: only availability, and "Only N left" at or below the
low-stock threshold (CAT-11)."""

from django.db.models import Count, Exists, Max, Min, OuterRef, Prefetch, Q

from apps.core.models import SiteSettings

from . import services
from .models import Product, ProductImage, SubCategory, Variant


def products_qs():
    return (Product.objects.filter(services.storefront())
            .select_related("category", "subcategory")
            .prefetch_related(
                Prefetch("images", ProductImage.objects.select_related("media")),
                Prefetch("variants", Variant.objects.filter(is_active=True))))


def _image(img):
    m = img.media
    return {"url": m.file.url, "alt_text": m.alt_text, "width": m.width, "height": m.height,
            "color": img.color}


def card(p: Product) -> dict:
    """Listing/card shape (ProductCard board): badge, discount, swatches, sold-out."""
    variants = list(p.variants.all())
    images = list(p.images.all())
    sold_out = not any(v.on_hand > 0 for v in variants)
    seen, colors = set(), []
    for v in variants:
        if v.color_name and v.color_name.lower() not in seen:
            seen.add(v.color_name.lower())
            colors.append({"name": v.color_name, "hex": v.color_hex})
    return {
        "id": p.id, "name": p.name, "slug": p.slug,
        "price": str(p.price),
        "compare_at_price": str(p.compare_at_price) if p.compare_at_price else None,
        "discount_pct": p.discount_pct,
        "tag": "New" if p.is_new else ("Signature" if p.is_signature else None),
        "image": _image(images[0]) if images else None,
        "category": {"name": p.category.name, "slug": p.category.slug},
        "subcategory": ({"name": p.subcategory.name, "slug": p.subcategory.slug}
                        if p.subcategory else None),
        "colors": colors, "sizes": sorted({v.size_label for v in variants if v.size_label}),
        "sold_out": sold_out,  # CAT-13
    }


def detail(p: Product) -> dict:
    low = SiteSettings.load().low_stock_threshold
    out = card(p)
    out.update({
        "short_desc": p.short_desc, "description": p.description, "care_notes": p.care_notes,
        "collection": p.collection, "uom": p.uom, "tags": p.tags,
        "seo_title": p.seo_title, "seo_description": p.seo_description,
        "images": [_image(i) for i in p.images.all()],
        "variants": [{
            "id": v.id, "sku": v.sku, "size_label": v.size_label, "color_name": v.color_name,
            "color_hex": v.color_hex, "price": str(v.price),
            "in_stock": v.on_hand > 0,
            "low_stock_left": v.on_hand if 0 < v.on_hand <= low else None,  # "Only N left"
        } for v in p.variants.all()],
        # CAT-22: Home › Category › Sub-category
        "breadcrumb": [{"name": p.category.name, "slug": p.category.slug}] + (
            [{"name": p.subcategory.name, "slug": p.subcategory.slug}] if p.subcategory else []),
    })
    return out


def _csv(request, key):
    return [x.strip() for x in request.query_params.get(key, "").split(",") if x.strip()]


def filtered(request, base=None, skip_sub=False):
    """CAT-09/CAT-20: all listing filters. Sub-categories OR together; other groups AND."""
    qs = base if base is not None else products_qs()
    q = request.query_params
    if cat := q.get("category"):
        qs = qs.filter(category__slug=cat)
    if (subs := _csv(request, "sub")) and not skip_sub:
        qs = qs.filter(subcategory__slug__in=subs, subcategory__is_active=True)
    if sizes := _csv(request, "size"):
        qs = qs.filter(variants__in=Variant.objects.filter(is_active=True, size_label__in=sizes))
    if colours := _csv(request, "colour"):
        cq = Q()
        for c in colours:
            cq |= Q(variants__color_name__iexact=c, variants__is_active=True)
        qs = qs.filter(cq)
    if cols := _csv(request, "collection"):
        qs = qs.filter(collection__in=cols)
    if q.get("price_min"):
        qs = qs.filter(price__gte=q["price_min"])
    if q.get("price_max"):
        qs = qs.filter(price__lte=q["price_max"])
    if q.get("available") in ("1", "true"):
        qs = qs.filter(Exists(Variant.objects.filter(product=OuterRef("pk"), is_active=True,
                                                     on_hand__gt=0)))
    if q.get("offers") in ("1", "true"):
        qs = qs.filter(services.offers_q())
    if q.get("new") in ("1", "true"):
        qs = qs.filter(services.new_arrivals_q())
    if term := q.get("q", "").strip():
        qs = qs.filter(search_q(term))
    return qs.distinct()


def search_q(term: str) -> Q:
    """CAT-10: name, SKU, category, tags and sub-category names."""
    return (Q(name__icontains=term) | Q(variants__sku__icontains=term)
            | Q(category__name__icontains=term) | Q(subcategory__name__icontains=term)
            | Q(tags__icontains=term))


SORTS = {
    "featured": ["-is_signature", "-is_new", "-created_at"],
    "newest": ["-created_at"],
    "price_asc": ["price", "-created_at"],
    "price_desc": ["-price", "-created_at"],
}


def facets(request) -> dict:
    """Left-filter data. Sub-category counts ignore the sub filter itself (CAT-20)."""
    scoped = filtered(request, skip_sub=True)
    ids = scoped.values("id")
    subs = []
    if cat := request.query_params.get("category"):
        rows = (SubCategory.objects.filter(category__slug=cat, is_active=True)
                .annotate(n=Count("products", filter=Q(products__in=ids))))
        subs = [{"name": s.name, "slug": s.slug, "count": s.n} for s in rows]
    var = Variant.objects.filter(product__in=ids, is_active=True)
    price = Product.objects.filter(pk__in=ids).aggregate(lo=Min("price"), hi=Max("price"))
    return {
        "subcategories": subs,
        "sizes": sorted({s for s in var.values_list("size_label", flat=True) if s}),
        "colours": [{"name": n, "hex": h} for n, h in
                    var.exclude(color_name="").order_by("color_name")
                    .values_list("color_name", "color_hex").distinct()],
        "collections": sorted({c for c in Product.objects.filter(pk__in=ids)
                               .values_list("collection", flat=True) if c}),
        "price": {"min": str(price["lo"] or 0), "max": str(price["hi"] or 0)},
    }
