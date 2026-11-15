# SRS CAT-23 (amendment v1.6): hand-picked "Best seller" flag.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0002_seed"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="is_bestseller",
            field=models.BooleanField(default=False),
        ),
    ]
