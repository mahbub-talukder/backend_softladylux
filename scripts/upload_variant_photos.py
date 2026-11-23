"""Append category photos onto products, tagged by variant colour (ProductImage.color).

Sources (copied into the container):
  /tmp/from_web2/          — freshly downloaded royalty-free shots
  /tmp/category_unused/    — leftover picture_to_upload/category-wise-pics files
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
from apps.core.models import MediaAsset, MediaKind  # noqa: E402

ALLOWED = {"JPEG", "PNG", "WEBP"}
MAX_BYTES = 5 * 1024 * 1024
MIN_SIDE = 300

# slug -> list of (absolute-or-rooted path relative to /tmp, color)
# Every colour variant of a product gets dedicated gallery shots.
MAPPING: dict[str, list[tuple[str, str]]] = {
    # --- Bras ---
    "silk-air-bra": [  # Rose only
        ("from_web2/bras/b1.jpg", "Rose"),
        ("from_web2/bras/b2.jpg", "Rose"),
        ("category_unused/bras/NBPSB1.webp", "Rose"),
        ("category_unused/bras/aoyXdBFNmJSGCmu7XLc0FqfmGBRFe4aJT5jtMM5H-800.webp", "Rose"),
    ],
    "silk-touch-plunge-bra": [  # Nude + Black
        ("from_web2/bras/b3.jpg", "Nude"),
        ("from_web2/bras/b4.jpg", "Nude"),
        ("category_unused/bras/432e315144a9c987e71a29336ad503b50a423c2d_477274_1_B_"
         "3ccfa6f8-f73f-4984-8495-f086ec293db7.jpg", "Nude"),
        ("from_web2/bras/b5.jpg", "Black"),
        ("from_web2/bras/b6.jpg", "Black"),
        ("category_unused/bras/71pkp0u68ZL._AC_UY1100_.jpg", "Black"),
        ("category_unused/bras/pic3.webp", "Black"),
    ],
    "contour-balconette-bra": [  # Rose only
        ("from_web2/bras/b4.jpg", "Rose"),
        ("from_web2/bras/b5.jpg", "Rose"),
        ("category_unused/bras/images3.jpeg", "Rose"),
    ],
    "wireless-comfort-bralette": [  # Nude + Rose + Red
        ("from_web2/bras/b1.jpg", "Nude"),
        ("from_web2/bras/b3.jpg", "Nude"),
        ("from_web2/bras/b2.jpg", "Rose"),
        ("from_web2/sets/s1.jpg", "Rose"),
        ("from_web2/bras/b6.jpg", "Red"),
        ("from_web2/sets/s3.jpg", "Red"),
    ],
    # --- Panties ---
    "seamless-brief": [  # Nude + Black + Red
        ("from_web2/panties/p1.jpg", "Nude"),
        ("from_web2/panties/p4.jpg", "Nude"),
        ("category_unused/panties/pantie1.jpeg", "Nude"),
        ("category_unused/panties/pant5.jpeg", "Nude"),
        ("from_web2/panties/p2.jpg", "Black"),
        ("category_unused/panties/H588d4bd84a744b56aac66205841ccb18u.jpg", "Black"),
        ("category_unused/panties/images (2).jpeg", "Black"),
        ("from_web2/panties/p3.jpg", "Red"),
        ("category_unused/panties/dRLRujz9QSPkuVEQREdtAHLPlw1HJKaVXGq7YSNy-800.webp", "Red"),
        ("category_unused/panties/pant6.jpeg", "Red"),
    ],
    # --- Lingerie set ---
    "lace-bralette-brief-set": [  # Rosewood only
        ("from_web2/sets/s1.jpg", "Rosewood"),
        ("from_web2/sets/s2.jpg", "Rosewood"),
        ("from_web2/sets/s3.jpg", "Rosewood"),
        ("category_unused/langies_set/pic2.jpeg", "Rosewood"),
    ],
    # --- Shapewear ---
    "sculpt-shaper": [  # Nude only
        ("from_web2/shapewear/sh1.jpg", "Nude"),
        ("from_web2/shapewear/sh2.jpg", "Nude"),
        ("from_web2/shapewear/sh3.jpg", "Nude"),
        ("category_unused/shapner/tile.jpeg", "Nude"),
        ("category_unused/shapner/tile2.jpeg", "Nude"),
    ],
    # --- Sleepwear ---
    "satin-sleep-shirt": [  # Ivory only
        ("from_web2/sleepwear/sl1.jpg", "Ivory"),
        ("from_web2/sleepwear/sl2.jpg", "Ivory"),
        ("from_web2/sleepwear/sl3.jpg", "Ivory"),
    ],
    # --- Tops ---
    "linen-wrap-top": [  # Sand only
        ("from_web2/tops/t1.jpg", "Sand"),
        ("from_web2/tops/t2.jpg", "Sand"),
        ("from_web2/tops/t3.jpg", "Sand"),
    ],
    # --- Accessories ---
    "new-arabian-t-shirt": [  # Ivory only
        ("from_web2/accessories/a1.jpg", "Ivory"),
        ("from_web2/accessories/a2.jpg", "Ivory"),
        ("from_web2/accessories/a3.jpg", "Ivory"),
    ],
}


def _load(path: Path) -> tuple[ContentFile, tuple[int, int], str]:
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES:
        raise ValueError(f"{path.name}: >5MB")
    with Image.open(path) as im:
        fmt, size = im.format, im.size
    if fmt not in ALLOWED:
        raise ValueError(f"{path.name}: format {fmt}")
    if max(size) < MIN_SIDE:
        raise ValueError(f"{path.name}: long side {max(size)} < {MIN_SIDE}")
    safe = "var-" + "".join(c if c.isalnum() or c in "._-" else "_" for c in path.name)
    return ContentFile(raw, name=safe), size, safe


def main() -> int:
    lines: list[str] = []
    with transaction.atomic():
        for slug, rows in MAPPING.items():
            product = Product.objects.filter(slug=slug).first()
            if not product:
                lines.append(f"SKIP {slug}: missing product")
                continue
            colours = set(
                product.variants.exclude(color_name="")
                .values_list("color_name", flat=True))
            sort0 = (product.images.order_by("-sort").values_list("sort", flat=True).first() or 0) + 1
            added = 0
            by_colour: dict[str, int] = {}
            for i, (rel, color) in enumerate(rows):
                if color and color not in colours:
                    lines.append(f"WARN {slug}: colour {color!r} not in variants {colours}")
                path = Path("/tmp") / rel
                if not path.is_file():
                    lines.append(f"MISS {slug}: {rel}")
                    continue
                try:
                    content, size, fname = _load(path)
                except ValueError as e:
                    lines.append(f"REJECT {slug}: {e}")
                    continue
                media = MediaAsset(
                    kind=MediaKind.PRODUCT,
                    alt_text=f"{product.name} — {color}" if color else product.name,
                    width=size[0], height=size[1])
                media.file.save(fname, content, save=False)
                media.save()
                ProductImage.objects.create(
                    product=product, media=media, color=color,
                    is_primary=False, sort=sort0 + i)
                added += 1
                by_colour[color or "(none)"] = by_colour.get(color or "(none)", 0) + 1
            # Ensure every variant colour has at least one tagged image
            for c in colours:
                if not product.images.filter(color__iexact=c).exists():
                    lines.append(f"GAP {slug}: no image tagged {c!r} yet")
            lines.append(
                f"OK {slug}: +{added} ({by_colour}) → total {product.images.count()}")

    cache.invalidate()
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
