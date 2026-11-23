"""Update category tile_media from /tmp/from_web photos."""

from __future__ import annotations

import sys
from pathlib import Path

import django
from django.core.files.base import ContentFile
from django.db import transaction
from PIL import Image

sys.path.insert(0, "/app")
django.setup()

from apps.catalog.models import Category  # noqa: E402
from apps.core import cache  # noqa: E402
from apps.core.models import MediaAsset, MediaKind  # noqa: E402

ROOT = Path("/tmp/from_web")
ALLOWED = {"JPEG", "PNG", "WEBP"}
MAX_BYTES = 5 * 1024 * 1024

# Best portrait / soft product shots per category slug
TILES = {
    "bras": ("bras/lace_bras_flatlay.jpg", "Bras category"),
    "panties": ("panties/beige_panty_flatlay.jpg", "Panties category"),
    "lingerie-sets": ("sets/cream_lingerie_set.jpg", "Lingerie Sets category"),
    "shapewear": ("shapewear/bodysuit_soft.jpg", "Shapewear category"),
    "sleepwear": ("sleepwear/pink_floral_pajama.jpg", "Sleepwear category"),
    "tops": ("tops/linen_fashion.jpg", "Tops category"),
    "accessories": ("accessories/elegant_detail.jpg", "Accessories category"),
    "gift-sets": ("carousel/hero_cream_set.jpg", "Gift Sets category"),
}


def _media(path: Path, alt: str) -> MediaAsset:
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES:
        raise ValueError(f"{path.name}: >5MB")
    with Image.open(path) as im:
        fmt, size = im.format, im.size
    if fmt not in ALLOWED:
        raise ValueError(f"{path.name}: bad format {fmt}")
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in path.name)
    # Avoid colliding with product uploads of the same basename
    safe = f"cat-{safe}"
    m = MediaAsset(kind=MediaKind.CATEGORY, alt_text=alt[:200], width=size[0], height=size[1])
    m.file.save(safe, ContentFile(raw, name=safe), save=False)
    m.save()
    return m


def main() -> int:
    if not ROOT.is_dir():
        print(f"Missing {ROOT}", file=sys.stderr)
        return 1
    with transaction.atomic():
        for slug, (rel, alt) in TILES.items():
            cat = Category.objects.filter(slug=slug).first()
            if not cat:
                print(f"SKIP {slug}: not found")
                continue
            path = ROOT / rel
            if not path.is_file():
                print(f"MISS {slug}: {rel}")
                continue
            media = _media(path, alt)
            cat.tile_media = media
            cat.save(update_fields=["tile_media", "updated_at"])
            print(f"OK {slug}: {media.file.name} ({media.width}x{media.height})")
    cache.invalidate()
    print("cache invalidated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
