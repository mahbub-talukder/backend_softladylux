# SRS WEB-02 / WEB-11 (amendment v1.6): Signature panel banner and the promotional popup.

from django.db import migrations, models

PLACEMENTS = [("home_promo", "Home promo"), ("home_offer", "Home offer"),
              ("signature", "Signature collection"), ("popup", "Promotional popup")]


def add_rows(apps, schema_editor):
    Banner = apps.get_model("core", "Banner")
    for placement in ("signature", "popup"):
        Banner.objects.get_or_create(placement=placement)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0007_sms_mimsms_json_api"),
    ]

    operations = [
        migrations.AlterField(
            model_name="banner",
            name="placement",
            field=models.CharField(choices=PLACEMENTS, max_length=20, unique=True),
        ),
        migrations.AddField(
            model_name="banner",
            name="button_label",
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.RunPython(add_rows, migrations.RunPython.noop),
    ]
