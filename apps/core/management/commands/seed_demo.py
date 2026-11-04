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


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))  # type: ignore[return-value]


def _hex(h: str, default=(140, 100, 100)) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) if len(h) == 6 else default  # type: ignore[return-value]


# Which line drawing goes with which category (same idea as the design boards' placeholder art)
KIND = {"bras": "bra", "panties": "panty", "lingerie-sets": "set", "shapewear": "shaper",
        "sleepwear": "gown", "tops": "top", "accessories": "top", "gift-sets": "set"}


def _garment(d: ImageDraw.ImageDraw, kind: str, cx: int, ink, fill, w: int):
    """Simple garment outlines in a thin line, centred on x=cx (900x1200 canvas)."""
    def bra(y):
        for sx in (-1, 1):
            d.pieslice([cx + sx * 150 - 150, y, cx + sx * 150 + 150, y + 300], 0, 180, fill=fill)
            d.arc([cx + sx * 150 - 150, y, cx + sx * 150 + 150, y + 300], 0, 180, fill=ink, width=w)
            d.line([cx + sx * 220, y + 150, cx + sx * 250, y - 230], fill=ink, width=w)  # strap
        d.line([cx - 300, y + 150, cx + 300, y + 150], fill=ink, width=w)

    def panty(y, s=1.0):
        pts = [(cx - 300 * s, y), (cx + 300 * s, y), (cx + 70 * s, y + 300 * s), (cx - 70 * s, y + 300 * s)]
        d.polygon(pts, fill=fill, outline=ink, width=w)
        d.line([pts[0], pts[1]], fill=ink, width=w + 2)

    if kind == "bra":
        bra(500)
    elif kind == "panty":
        panty(450)
    elif kind == "set":
        bra(330)
        panty(740, 0.75)
    elif kind == "shaper":
        pts = [(cx - 220, 280), (cx + 220, 280), (cx + 270, 640), (cx + 210, 940), (cx - 210, 940), (cx - 270, 640)]
        d.polygon(pts, fill=fill, outline=ink, width=w)
        d.line([cx - 160, 280, cx - 190, 150], fill=ink, width=w)
        d.line([cx + 160, 280, cx + 190, 150], fill=ink, width=w)
    elif kind == "gown":
        pts = [(cx - 120, 200), (cx + 120, 200), (cx + 340, 420), (cx + 250, 480), (cx + 280, 1000),
               (cx - 280, 1000), (cx - 250, 480), (cx - 340, 420)]
        d.polygon(pts, fill=fill, outline=ink, width=w)
        d.arc([cx - 90, 160, cx + 90, 300], 0, 180, fill=ink, width=w)
    else:  # top
        pts = [(cx - 130, 240), (cx + 130, 240), (cx + 360, 360), (cx + 280, 520), (cx + 210, 470),
               (cx + 210, 860), (cx - 210, 860), (cx - 210, 470), (cx - 280, 520), (cx - 360, 360)]
        d.polygon(pts, fill=fill, outline=ink, width=w)
        d.arc([cx - 100, 190, cx + 100, 320], 0, 180, fill=ink, width=w)


def placeholder(label: str, tone: tuple[int, int, int], size=(900, 1200), kind: str | None = None):
    """A generated line drawing (no third-party images): a soft tone background, a thin garment
    outline and the product label. Used for staging; the owner replaces them with real photos."""
    img = Image.new("RGB", size, _mix(tone, (255, 255, 255), 0.72))
    d = ImageDraw.Draw(img)
    if kind and size == (900, 1200):
        _garment(d, kind, size[0] // 2, _mix(tone, (0, 0, 0), 0.35), _mix(tone, (255, 255, 255), 0.45), 6)
    else:  # wide banners / slides: soft bands instead of a garment
        d.rectangle([0, int(size[1] * 0.62), size[0], size[1]], fill=_mix(tone, (255, 255, 255), 0.55))
    d.text((40, size[1] - 70), label[:48], fill=_mix(tone, (0, 0, 0), 0.5))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return ContentFile(buf.getvalue())


def own_photos(folder, slug: str):
    """Photos the owner supplied: files in `folder` whose name starts with the product slug."""
    from pathlib import Path

    return sorted(p for p in Path(folder).glob(f"{slug}*") if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))


class Command(BaseCommand):
    help = "Create (or with --reset hide) demo content for STAGING review. Never for production."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true",
                            help="Hide demo products and remove the other demo content.")
        parser.add_argument("--photos-dir", help="Folder of your own licensed photos: files named "
                            "<product-slug>*.jpg|png|webp are used instead of generated drawings.")
        parser.add_argument("--i-know-this-is-not-production", action="store_true",
                            dest="override", help="Run even though DEBUG is off (staging).")

    def handle(self, *args, reset, override, photos_dir=None, **opts):
        self.photos_dir = photos_dir
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
    def _media(self, label, tone, kind, size=(900, 1200), art=None, upload=None):
        """A MediaAsset from the owner's own file (`upload`) or a generated line drawing."""
        if upload is not None:
            with Image.open(upload) as im:
                size = im.size
            content = ContentFile(upload.read_bytes())
            name = f"demo-{upload.stem}{upload.suffix.lower()}"
        else:
            content = placeholder(label, tone, size, art)
            name = f"demo-{label.lower().replace(' ', '-')}.png"
        m = MediaAsset(kind=kind, alt_text=f"{TAG} {label}", width=size[0], height=size[1])
        m.file.save(name, content, save=False)
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
            mine = own_photos(self.photos_dir, p.slug) if self.photos_dir else []
            for j, colour in enumerate(colours):
                tone = _hex(COLOURS.get(colour, ""), (150 + 12 * i, 110 + 8 * j, 120 + 10 * i))
                m = self._media(f"{name} {colour}", tone, "product", art=KIND.get(cslug),
                                upload=mine[j % len(mine)] if mine else None)
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

