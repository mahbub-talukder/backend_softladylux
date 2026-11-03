from decimal import Decimal

from apps.catalog.models import Category, Product, ProductImage, SubCategory, Variant
from apps.core.models import MediaAsset


def category(slug="bras", subs=()):
    cat = Category.objects.get(slug=slug)
    for i, name in enumerate(subs):
        SubCategory.objects.get_or_create(category=cat, slug=name.lower().replace(" ", "-"),
                                          defaults={"name": name, "sort": i})
    return cat


def product(name="Plunge Bra", cat="bras", sub=None, price="1000", compare=None, sizes=("34B",),
            colours=("Rose",), on_hand=5, images=1, active=True, **kw):
    c = category(cat)
    s = SubCategory.objects.filter(category=c, slug=sub).first() if sub else None
    p = Product.objects.create(name=name, slug=name.lower().replace(" ", "-"), category=c,
                               subcategory=s, price=Decimal(price),
                               compare_at_price=Decimal(compare) if compare else None,
                               is_active=active, **kw)
    for i in range(images):
        m = MediaAsset.objects.create(file=f"x{p.id}{i}.png", kind="product", alt_text="a",
                                      width=800, height=1000)
        ProductImage.objects.create(product=p, media=m, is_primary=i == 0, sort=i)
    for size in sizes:
        for colour in colours:
            Variant.objects.create(product=p, size_label=size, color_name=colour,
                                   color_hex="#AA5566", sku=f"{p.slug}-{colour}-{size}".upper(),
                                   on_hand=on_hand)
    return p
