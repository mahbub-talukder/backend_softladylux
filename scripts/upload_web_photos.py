"""Append royalty-free web photos to products and refresh carousel slides.

Expects images at /tmp/from_web (copied from picture_to_upload/from_web).
"""

from __future__ import annotations

import sys
from pathlib import Path

import django
from django.core.files.base import ContentFile
from django.db import transaction
from PIL import Image

sys.path.insert(0, "/app")
django.setup()

from apps.catalog.models import Product, ProductImage  # noqa: E402
from apps.core import cache  # noqa: E402
from apps.core.models import CarouselSlide, LinkType, MediaAsset, MediaKind  # noqa: E402

ROOT = Path("/tmp/from_web")
ALLOWED = {"JPEG", "PNG", "WEBP"}
MAX_BYTES = 5 * 1024 * 1024
MIN_SIDE = 300

# Append gallery photos (not replacing existing)
PRODUCT_ADD: dict[str, list[tuple[str, str]]] = {
    "silk-air-bra": [
        ("bras/soft_bra_vials.jpg", "Rose"),
        ("bras/lace_bras_flatlay.jpg", ""),
    ],
    "silk-touch-plunge-bra": [
        ("bras/white_lace_unsplash.jpg", "Nude"),
        ("bras/extra_lace_candle.jpg", ""),
    ],
    "contour-balconette-bra": [
        ("bras/fashion_soft_unsplash.jpg", "Rose"),
        ("bras/lace_bras_flatlay.jpg", "Rose"),
    ],
    "wireless-comfort-bralette": [
        ("bras/soft_bra_vials.jpg", "Nude"),
        ("accessories/elegant_detail.jpg", ""),
    ],
    "seamless-brief": [
        ("panties/beige_panty_flatlay.jpg", "Nude"),
        ("panties/extra_silk_brief.jpg", ""),
    ],
    "lace-bralette-brief-set": [
        ("sets/cream_lingerie_set.jpg", "Rosewood"),
        ("sets/black_lace_mood.jpg", ""),
    ],
    "sculpt-shaper": [
        ("shapewear/bodysuit_soft.jpg", "Nude"),
        ("shapewear/extra_shape.jpg", "Nude"),
    ],
    "satin-sleep-shirt": [
        ("sleepwear/pink_floral_pajama.jpg", "Ivory"),
        ("sleepwear/lounge_soft.jpg", "Ivory"),
    ],
    "linen-wrap-top": [
        ("tops/linen_fashion.jpg", "Sand"),
    ],
    "new-arabian-t-shirt": [
        ("accessories/elegant_detail.jpg", "Ivory"),
    ],
}

# Clean carousel (SRS: max 8 active). Deactivate previous, create these.
CAROUSEL = [
    {
        "file": "carousel/slide_new_season.jpg",
        "eyebrow": "New season",
        "heading": "Quiet luxury, worn close",
        "subtext": "Soft essentials for everyday comfort.",
        "button_label": "Shop new",
        "link_value": "/shop/new",
        "alt": "New season lingerie flat lay",
    },
    {
        "file": "carousel/slide_lace_edit.jpg",
        "eyebrow": "Signature",
        "heading": "The lace edit",
        "subtext": "Delicate lace pieces for every silhouette.",
        "button_label": "Shop signature",
        "link_value": "/shop/lingerie-sets",
        "alt": "Lace lingerie with candlelight",
    },
    {
        "file": "carousel/slide_soft_essentials.jpg",
        "eyebrow": "Everyday",
        "heading": "Soft essentials",
        "subtext": "Breathable bras and briefs you’ll reach for daily.",
        "button_label": "Shop bras",
        "link_value": "/shop/bras",
        "alt": "Soft lingerie essentials flat lay",
    },
    {
        "file": "carousel/slide_sleep_edit.jpg",
        "eyebrow": "At home",
        "heading": "The sleep edit",
        "subtext": "Satin and soft loungewear for slower evenings.",
        "button_label": "Shop sleepwear",
        "link_value": "/shop/sleepwear",
        "alt": "Woman in soft floral sleepwear",
    },
]


def _load(path: Path) -> tuple[ContentFile, tuple[int, int], str]:
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES:
        raise ValueError(f"{path.name}: larger than 5 MB")
    with Image.open(path) as im:
        fmt, size = im.format, im.size
    if fmt not in ALLOWED:
        raise ValueError(f"{path.name}: format {fmt} not allowed")
    if max(size) < MIN_SIDE:
        raise ValueError(f"{path.name}: long side {max(size)} < {MIN_SIDE}")
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in path.name)
    return ContentFile(raw, name=safe), size, safe


def _media(path: Path, kind: str, alt: str) -> MediaAsset:
    content, size, fname = _load(path)
    m = MediaAsset(kind=kind, alt_text=alt[:200], width=size[0], height=size[1])
    m.file.save(fname, content, save=False)
    m.save()
    return m


def main() -> int:
    if not ROOT.is_dir():
        print(f"Missing {ROOT}", file=sys.stderr)
        return 1

    lines: list[str] = []
    with transaction.atomic():
        # --- products: append ---
        for slug, rows in PRODUCT_ADD.items():
            product = Product.objects.filter(slug=slug).first()
            if not product:
                lines.append(f"SKIP product {slug}")
                continue
            sort0 = (product.images.order_by("-sort").values_list("sort", flat=True).first() or 0) + 1
            added = 0
            for i, (rel, color) in enumerate(rows):
                path = ROOT / rel
                if not path.is_file():
                    lines.append(f"MISS {slug}: {rel}")
                    continue
                try:
                    media = _media(path, MediaKind.PRODUCT, f"{product.name}" + (f" — {color}" if color else ""))
                except ValueError as e:
                    lines.append(f"REJECT {slug}: {e}")
                    continue
                ProductImage.objects.create(
                    product=product, media=media, color=color,
                    is_primary=False, sort=sort0 + i)
                added += 1
            lines.append(f"OK product {slug}: +{added} photos (total {product.images.count()})")

        # --- carousel: deactivate all, create fresh active set ---
        deactivated = CarouselSlide.objects.filter(is_active=True).update(is_active=False)
        lines.append(f"carousel: deactivated {deactivated} old slides")
        for sort, row in enumerate(CAROUSEL):
            path = ROOT / row["file"]
            media = _media(path, MediaKind.CAROUSEL, row["alt"])
            CarouselSlide.objects.create(
                media=media,
                eyebrow=row["eyebrow"],
                heading=row["heading"],
                subtext=row["subtext"],
                button_label=row["button_label"],
                link_type=LinkType.URL,
                link_value=row["link_value"],
                sort=sort,
                is_active=True,
            )
            lines.append(f"OK carousel[{sort}]: {row['heading']} ← {row['file']}")

    cache.invalidate()
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
