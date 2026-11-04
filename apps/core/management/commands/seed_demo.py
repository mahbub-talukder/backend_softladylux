"""Staging demo content. M6 / SRS §16: lets the owner review staging with realistic content.

NOT for production: refuses to run unless DEBUG is on (or the explicit flag is given). Everything it
creates is tagged so `--reset` removes exactly that and nothing else. Photos are generated
placeholders (Pillow); the owner replaces them with real photos. Settings values are filled only
where still empty, and are clearly marked as placeholders."""

import io
from decimal import Decimal

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from PIL import Image, ImageDraw

from apps.catalog import services
from apps.catalog.models import Category, Product, ProductImage, SubCategory
from apps.core import cache
from apps.core.models import Banner, CarouselSlide, MediaAsset, SiteSettings
from apps.shop.models import Coupon

TAG = "[demo]"  # alt-text prefix on every demo MediaAsset: how --reset finds them
SUBS = {"bras": ["Maternity Bra", "Air Bra", "Tube Bra"]}
# (name, category slug, sub-category slug, price, compare-at, flags, sizes, colours)
PRODUCTS = [
    ("Silk-Touch Plunge Bra", "bras", "air-bra", "2450", None, dict(is_new=True), ["34B", "36C"], ["Nude", "Black"]),
    ("Contour Balconette Bra", "bras", "tube-bra", "2750", "3450", dict(is_offer=True), ["34B", "36B"], ["Rose"]),
    ("Wireless Comfort Bralette", "bras", "maternity-bra", "1990", "2490", dict(), ["S", "M"], ["Nude", "Rose"]),
    ("Seamless Brief", "panties", None, "690", None, dict(), ["S", "M", "L"], ["Nude", "Black"]),
    ("Lace Bralette & Brief Set", "lingerie-sets", None, "3290", None, dict(is_signature=True), ["S", "M"], ["Rosewood"]),
    ("Sculpt Shaper", "shapewear", None, "2190", None, dict(), ["M", "L"], ["Nude"]),
    ("Satin Sleep Shirt", "sleepwear", None, "2890", "3290", dict(is_offer=True), ["S", "M", "L"], ["Ivory"]),
    ("Linen Wrap Top", "tops", None, "1590", None, dict(is_new=True), ["S", "M", "L"], ["Sand"]),
]
COLOURS = {"Nude": "#D9B8A4", "Black": "#231E1C", "Rose": "#C98A9A", "Rosewood": "#8C5550",
           "Ivory": "#EFE8DB", "Sand": "#CDB99A"}


def placeholder(label: str, tone: tuple[int, int, int], size=(900, 1200)) -> ContentFile:
    img = Image.new("RGB", size, tone)
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, size[0] - 40, size[1] - 40], outline=(255, 255, 255), width=4)
    d.text((size[0] // 2 - 6 * len(label), size[1] // 2), label, fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return ContentFile(buf.getvalue())


class Command(BaseCommand):
    help = "Create (or with --reset hide) demo content for STAGING review. Never for production."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true",
                            help="Hide demo products and remove the other demo content.")
        parser.add_argument("--i-know-this-is-not-production", action="store_true",
                            dest="override", help="Run even though DEBUG is off (staging).")

    def handle(self, *args, reset, override, **opts):
        if not settings.DEBUG and not override:
            raise CommandError(
                "seed_demo is for staging only and refuses to run with DEBUG off. If this really "
                "is staging, add --i-know-this-is-not-production.")
        with transaction.atomic():
            if reset:
                self._reset()
                self.stdout.write(self.style.SUCCESS("Demo content removed (demo products are hidden)."))
            else:
                n = self._seed()
                self.stdout.write(self.style.SUCCESS(f"Demo content ready ({n} new products)."))
        cache.invalidate()

    # ------------------------------------------------------------------ seed
    def _media(self, label, tone, kind, size=(900, 1200)):
        m = MediaAsset(kind=kind, alt_text=f"{TAG} {label}", width=size[0], height=size[1])
        m.file.save(f"demo-{label.lower().replace(' ', '-')}.png", placeholder(label, tone, size),
                    save=False)
        m.save()
        return m

    def _seed(self) -> int:
        for slug, names in SUBS.items():
            cat = Category.objects.get(slug=slug)
            for i, name in enumerate(names):
                if not SubCategory.objects.filter(category=cat, name__iexact=name).exists():
                    SubCategory.objects.create(
                        category=cat, name=name, sort=i,
                        slug=services.unique_slug(SubCategory, name, category=cat))
        created = 0
        for i, (name, cslug, sslug, price, compare, flags, sizes, colours) in enumerate(PRODUCTS):
            existing = Product.objects.filter(name=name, collection="Demo").first()
            if existing or Product.objects.filter(name=name).exists():
                if existing and not existing.is_active:  # re-seeding after --reset brings it back
                    existing.is_active = True
                    existing.save(update_fields=["is_active", "updated_at"])
                continue
            cat = Category.objects.get(slug=cslug)
            sub = SubCategory.objects.filter(category=cat, slug=sslug).first() if sslug else None
            p = Product.objects.create(
                name=name, slug=services.unique_slug(Product, name), category=cat, subcategory=sub,
                price=Decimal(price), compare_at_price=Decimal(compare) if compare else None,
                short_desc=f"{name}: sample product for staging review.",
                description="Demo content. Replace with your own description, photos and prices.",
                collection="Demo", is_active=True, **flags)
            for j, colour in enumerate(colours):
                m = self._media(f"{name} {colour}", (150 + 12 * i, 110 + 8 * j, 120 + 10 * i),
                                "product")
                ProductImage.objects.create(product=p, media=m, is_primary=j == 0, sort=j,
                                            color=colour)
                for size in sizes:
                    services.create_variant(p, {
                        "size_label": size, "color_name": colour,
                        "color_hex": COLOURS.get(colour, ""), "on_hand": 12})
            created += 1
        self._content()
        self._settings()
        return created

    def _content(self):
        if not CarouselSlide.objects.filter(heading__startswith=TAG).exists():
            for i, (eyebrow, heading) in enumerate([("New season", "Quiet luxury, worn close"),
                                                    ("Signature", "The lace edit")]):
                media = self._media(f"slide {i + 1}", (120 + 30 * i, 90, 100), "carousel",
                                    (1600, 900))
                CarouselSlide.objects.create(
                    media=media, eyebrow=eyebrow, heading=f"{TAG} {heading}",
                    button_label="Shop now", link_type="url", link_value="/shop/new", sort=i)
        for placement, text in (("home_promo", "New arrivals"), ("home_offer", "Offers")):
            b = Banner.objects.get(placement=placement)
            if b.media_id is None:
                b.media = self._media(text, (140, 100, 110), "banner", (1200, 600))
                b.heading, b.text, b.link, b.is_active = f"{TAG} {text}", "Sample banner", "/", True
                b.save()
        if not Coupon.objects.filter(code="WELCOME10").exists():
            Coupon.objects.create(code="WELCOME10", description="Demo: 10% off", type="percent",
                                  value=Decimal(10), min_subtotal=Decimal(0))

    def _settings(self):
        """Only fills what is still empty; every value is an obvious placeholder."""
        s = SiteSettings.load()
        defaults = {"tagline": "Quiet luxury, worn close [demo]",
                    "address_line1": "[STORE ADDRESS]", "city": "[CITY]",
                    "working_hours": "[SUPPORT HOURS]", "footer_about": "[ABOUT TEXT: demo content]"}
        for field, value in defaults.items():
            if not getattr(s, field):
                setattr(s, field, value)
        s.save()

    # ------------------------------------------------------------------ reset
    def _reset(self):
        # Stock movements are immutable and never deleted (INV-08), so demo products that received
        # opening stock cannot be deleted either: they are hidden from the storefront instead.
        Product.objects.filter(collection="Demo").update(is_active=False)
        CarouselSlide.objects.filter(heading__startswith=TAG).delete()
        for b in Banner.objects.filter(heading__startswith=TAG):
            b.media, b.heading, b.text, b.link, b.is_active = None, "", "", "", False
            b.save()
        Coupon.objects.filter(code="WELCOME10", description="Demo: 10% off").delete()
        for sub in SubCategory.objects.filter(category__slug="bras", name__in=SUBS["bras"]):
            if not sub.products.exists():
                sub.delete()
        # product photos stay (a product still points at them); unused demo media is removed
        used = ProductImage.objects.values("media")
        MediaAsset.objects.filter(alt_text__startswith=TAG).exclude(pk__in=used).delete()

