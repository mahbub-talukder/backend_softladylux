"""One-shot: replace demo/line-art product images with classified photos from /tmp/picture_to_upload.

Run inside the backend container:
  python /app/scripts/upload_classified_photos.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import django
from django.core.files.base import ContentFile
from django.db import transaction
from PIL import Image

# Allow `python /app/scripts/...` without manage.py
sys.path.insert(0, "/app")
django.setup()

from apps.catalog.models import Product, ProductImage  # noqa: E402
from apps.core import cache  # noqa: E402
from apps.core.models import MediaAsset, MediaKind  # noqa: E402

ROOT = Path("/tmp/picture_to_upload")
ALLOWED = {"JPEG", "PNG", "WEBP"}
MAX_BYTES = 5 * 1024 * 1024
MIN_SIDE = 300

# slug -> list of (relative path, color, is_primary)
MAPPING: dict[str, list[tuple[str, str, bool]]] = {
    "silk-air-bra": [
        ("New Arrival/ruma_air_china.webp", "Rose", True),
        ("New Arrival/chian.webp", "Rose", False),
        ("category-wise-pics/bras/white-bpc-1001x1001-1-400x400.webp", "Rose", False),
        ("category-wise-pics/bras/23328b-400x400.webp", "Rose", False),
    ],
    "silk-touch-plunge-bra": [
        ("signature_item_bra1.webp", "Nude", True),
        ("Gemini_Generated_Image_s93742s93742s937_00bc7fc9-b872-49f4-8273-680ee3bbb6ed.webp",
         "Nude", False),
        ("O1CN01tfEwCa1fGtBNxVCGK__3093593980-0-cib.600x600.webp", "Black", False),
        ("category-wise-pics/bras/pic1.webp", "", False),
    ],
    "contour-balconette-bra": [
        ("New Arrival/SpandexwhitenonpaddedfulllacedesignBra.webp", "Rose", True),
        ("category-wise-pics/bras/432e315144a9c987e71a29336ad503b50a423c2d_477274_1_B_"
         "3ccfa6f8-f73f-4984-8495-f086ec293db7.jpg", "Rose", False),
        ("category-wise-pics/bras/wall_paper.jpg", "Rose", False),
        ("category-wise-pics/bras/23326c-400x400.webp", "Rose", False),
    ],
    "wireless-comfort-bralette": [
        ("New Arrival/fitted_bra_new_arrival.webp", "Nude", True),
        ("New Arrival/thin_wireless.webp", "Nude", False),
        ("category-wise-pics/bras/pic2.webp", "Rose", False),
        ("category-wise-pics/bras/pic4.webp", "Red", False),
    ],
    "seamless-brief": [
        ("category-wise-pics/panties/newhd.webp", "Red", True),
        ("category-wise-pics/panties/63031-2020-main_800x.webp", "Black", False),
        ("category-wise-pics/panties/pantie2.jpeg", "Nude", False),
        ("category-wise-pics/panties/signuture_item.jpeg", "Nude", False),
        ("category-wise-pics/panties/pant4.jpeg", "Nude", False),
    ],
    "lace-bralette-brief-set": [
        ("category-wise-pics/langies_set/pic1.webp", "Rosewood", True),
        ("category-wise-pics/langies_set/AKzr8f4kBK4sjgzIFv2yPYtqY2LugWPoFT9eR0Dc-800.webp",
         "Rosewood", False),
        ("category-wise-pics/langies_set/Evmax6IVEcMvgqF8rN77nnOnNLcKnBXjCIQRLcm3-800.webp",
         "Rosewood", False),
        ("category-wise-pics/langies_set/XMPM99WuUsI6MouXhZsVsMOtzOSJxQtnpC6ebwJR-800.webp",
         "Rosewood", False),
    ],
    "sculpt-shaper": [
        ("category-wise-pics/shapner/aLJs0H7suFyAToQZbqiYVJJ7YOMtQKjGOKPJ5IfD-800.webp",
         "Nude", True),
        ("category-wise-pics/shapner/IIbcjVoPlvtw3KdmPqDK0GKXkfpp8xYJEtFGtDPM-800.webp",
         "Nude", False),
        ("category-wise-pics/shapner/Pud8iwRSGcPhK2zOYaTJYoSGMEk4KERVsecztCpA-800.webp",
         "Nude", False),
    ],
    "satin-sleep-shirt": [
        ("category-wise-pics/SLEEPER/TILE1.jpeg", "Ivory", True),
        ("New Arrival/sleeper_new_arrival.webp", "Ivory", False),
        ("category-wise-pics/SLEEPER/tile_final.webp", "Ivory", False),
    ],
    "linen-wrap-top": [
        ("category-wise-pics/tops/tile.jpg", "Sand", True),
        ("category-wise-pics/tops/images1.jpeg", "Sand", False),
        ("category-wise-pics/tops/images2.jpeg", "Sand", False),
    ],
    "new-arabian-t-shirt": [
        ("category-wise-pics/accessories/image1.jpeg", "Ivory", True),
        ("category-wise-pics/accessories/AHivhwVNeyL3ye3xd2n5zRYAqXXHe9Ve7g0HBqkS-800.webp",
         "Ivory", False),
        ("category-wise-pics/accessories/tile2.jpg", "Ivory", False),
    ],
}


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
    name = path.name
    # Keep a safe filename for ImageField
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
    return ContentFile(raw, name=safe), size, safe


def main() -> int:
    if not ROOT.is_dir():
        print(f"Missing {ROOT}", file=sys.stderr)
        return 1

    report = []
    with transaction.atomic():
        for slug, rows in MAPPING.items():
            product = Product.objects.filter(slug=slug).first()
            if not product:
                report.append(f"SKIP {slug}: product not found")
                continue

            old_ids = list(product.images.values_list("id", flat=True))
            ProductImage.objects.filter(id__in=old_ids).delete()

            made = 0
            for sort, (rel, color, primary) in enumerate(rows):
                path = ROOT / rel
                if not path.is_file():
                    report.append(f"MISS {slug}: {rel}")
                    continue
                try:
                    content, size, fname = _load(path)
                except ValueError as e:
                    report.append(f"REJECT {slug}: {e}")
                    continue
                media = MediaAsset(
                    kind=MediaKind.PRODUCT,
                    alt_text=f"{product.name}" + (f" — {color}" if color else ""),
                    width=size[0],
                    height=size[1],
                )
                media.file.save(fname, content, save=False)
                media.save()
                ProductImage.objects.create(
                    product=product,
                    media=media,
                    color=color,
                    is_primary=primary if made == 0 or primary else False,
                    sort=sort,
                )
                # Ensure exactly one primary: first successful upload wins if flagged ones failed
                if made == 0:
                    ProductImage.objects.filter(product=product).update(is_primary=False)
                    ProductImage.objects.filter(product=product, media=media).update(
                        is_primary=True)
                made += 1

            # Guarantee one primary
            if product.images.exists() and not product.images.filter(is_primary=True).exists():
                first = product.images.order_by("sort", "id").first()
                first.is_primary = True
                first.save(update_fields=["is_primary", "updated_at"])

            report.append(
                f"OK {slug} (id={product.id}): removed {len(old_ids)} old, added {made} photos")

    cache.invalidate()
    print("\n".join(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
