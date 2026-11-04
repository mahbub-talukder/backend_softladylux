from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0004_default_privacy_text")]
    operations = [
        migrations.AddField(model_name="sitesettings", name="home_sections",
                            field=models.JSONField(blank=True, default=dict)),
    ]
