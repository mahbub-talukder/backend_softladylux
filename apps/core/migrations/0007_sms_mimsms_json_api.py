# SRS ACC-02 / NTF-02 (amendment v1.6): MiMSMS JSON API settings, order-received SMS switch.

from django.db import migrations, models

DEFAULT_URL = "https://api.mimsms.com/api/V2/SMS"


def default_url(apps, schema_editor):
    SiteSettings = apps.get_model("core", "SiteSettings")
    SiteSettings.objects.filter(mimsms_url="").update(mimsms_url=DEFAULT_URL)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0006_theme_background_text"),
    ]

    operations = [
        migrations.RenameField("sitesettings", "mimsms_sender_id", "mimsms_sender_name"),
        migrations.RenameField("sitesettings", "mimsms_endpoint", "mimsms_url"),
        migrations.AlterField(
            model_name="sitesettings",
            name="mimsms_url",
            field=models.URLField(blank=True, default=DEFAULT_URL),
        ),
        migrations.AddField(
            model_name="sitesettings",
            name="mimsms_username",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="sitesettings",
            name="sms_order_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.RemoveField(model_name="sitesettings", name="mimsms_method"),
        migrations.RemoveField(model_name="sitesettings", name="mimsms_params"),
        migrations.RemoveField(model_name="sitesettings", name="mimsms_success_contains"),
        migrations.RunPython(default_url, migrations.RunPython.noop),
    ]
