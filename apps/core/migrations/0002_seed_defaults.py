"""Seed data: SRS THM-18 (12 built-in palettes), THM-13 (Signature default theme),
WEB-02 (two fixed banner placements), WEB-07 (static pages), WEB-08 (trust strip)."""

from django.db import migrations

PAGES = [
    ("about", "About"),
    ("delivery-returns", "Delivery & Returns"),
    ("privacy", "Privacy Policy"),
    ("terms", "Terms & Conditions"),
]
TRUST = [
    ("return", "Easy returns", "Simple return policy"),
    ("shield", "Secure payments", "bKash, Nagad or Cash on Delivery"),
    ("truck", "Fast delivery", "Across Bangladesh"),
]


def seed(apps, schema_editor):
    from apps.core.theme import BUILTIN_PALETTES, derive_tokens

    Palette = apps.get_model("core", "ThemePalette")
    Theme = apps.get_model("core", "ThemeSettings")
    for i, (name, primary, accent) in enumerate(BUILTIN_PALETTES):
        Palette.objects.create(name=name, primary_hex=primary, accent_hex=accent,
                               is_builtin=True, sort=i)
    sig = Palette.objects.get(name="Signature")
    Theme.objects.create(pk=1, palette=sig, primary_hex=sig.primary_hex,
                         accent_hex=sig.accent_hex,
                         tokens=derive_tokens(sig.primary_hex, sig.accent_hex))
    apps.get_model("core", "SiteSettings").objects.create(pk=1)
    Banner = apps.get_model("core", "Banner")
    for placement in ("home_promo", "home_offer"):
        Banner.objects.create(placement=placement)
    Page = apps.get_model("core", "Page")
    for slug, title in PAGES:
        Page.objects.create(slug=slug, title=title, body="")
    Trust = apps.get_model("core", "TrustItem")
    for i, (icon, title, text) in enumerate(TRUST):
        Trust.objects.create(icon=icon, title=title, text=text, sort=i)


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
