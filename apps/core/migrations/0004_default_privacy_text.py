"""INS-04: the privacy page mentions Google Analytics. Only fills the page when it is still empty,
so an owner's own wording is never overwritten."""

from django.db import migrations

TEXT = (
    "<h2>Your privacy</h2>"
    "<p>We collect only what we need to take and deliver your orders: your name, mobile number, "
    "delivery address and, if you give one, your email.</p>"
    "<h2>Cookies and Google Analytics</h2>"
    "<p>With your consent, we use Google Analytics through Google Tag Manager to see which pages "
    "and buttons are used. It starts only after you accept the cookie notice; if you decline, no "
    "analytics are loaded. Analytics data is aggregated and is not linked to your account or "
    "orders, and IP addresses are anonymised by Google Analytics.</p>"
    "<p>You can change your choice at any time by clearing this site's data in your browser.</p>"
)


def fill(apps, schema_editor):
    Page = apps.get_model("core", "Page")
    Page.objects.filter(slug="privacy", body="").update(body=TEXT)


class Migration(migrations.Migration):
    dependencies = [("core", "0003_subscriber_sitesettings_mimsms_endpoint_and_more")]
    operations = [migrations.RunPython(fill, migrations.RunPython.noop)]
