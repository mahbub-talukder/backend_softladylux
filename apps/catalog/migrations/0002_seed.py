"""Seed: CAT-01 eight categories; CAT-08 default size charts (values from the SizeGuide board)."""

from django.db import migrations

CATEGORIES = ["Bras", "Panties", "Lingerie Sets", "Shapewear", "Sleepwear", "Tops",
              "Accessories", "Gift Sets"]
BRA = {
    "bands": [{"size": s, "min": a, "max": b}
              for s, a, b in [(30, 74, 78), (32, 79, 83), (34, 84, 88), (36, 89, 93), (38, 94, 98)]],
    "cups": [{"cup": c, "min": a, "max": b}
             for c, a, b in [("A", 11, 13), ("B", 14, 16), ("C", 17, 19), ("D", 20, 22),
                             ("DD", 23, 25)]],
}
APPAREL = {"rows": [{"size": s, "bust": [b1, b2], "waist": [w1, w2], "hip": [h1, h2]}
                    for s, b1, b2, w1, w2, h1, h2 in [
                        ("S", 80, 86, 62, 68, 88, 94), ("M", 86, 92, 68, 74, 94, 100),
                        ("L", 92, 98, 74, 80, 100, 106), ("XL", 98, 104, 80, 86, 106, 112)]]}


def seed(apps, schema_editor):
    from django.utils.text import slugify

    Category = apps.get_model("catalog", "Category")
    for i, name in enumerate(CATEGORIES):
        Category.objects.create(name=name, slug=slugify(name), sort=i)
    SizeChart = apps.get_model("catalog", "SizeChart")
    SizeChart.objects.create(name="Bras", type="bra", rows=BRA)
    SizeChart.objects.create(name="Panties, shapewear & tops", type="apparel", rows=APPAREL)


class Migration(migrations.Migration):
    dependencies = [("catalog", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
