# v1.11 owner request: a "Login page" banner placement for the login / register side panel.

from django.db import migrations, models

PLACEMENTS = [("home_promo", "Home promo"), ("home_offer", "Home offer"),
              ("signature", "Signature collection"), ("popup", "Promotional popup"),
              ("login", "Login page")]


def add_login_row(apps, schema_editor):
    # On by default, so uploading an image is all the owner needs to do.
    apps.get_model("core", "Banner").objects.get_or_create(placement="login",
                                                            defaults={"is_active": True})


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0016_map_location"),
    ]

    operations = [
        migrations.AlterField(
            model_name="banner",
            name="placement",
            field=models.CharField(choices=PLACEMENTS, max_length=20, unique=True),
        ),
        migrations.RunPython(add_login_row, migrations.RunPython.noop),
    ]
