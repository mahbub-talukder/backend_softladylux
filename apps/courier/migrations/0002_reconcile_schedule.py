# CRR-11 (SRS v1.7): re-read Steadfast parcels every 2 hours so a missed webhook is harmless and
# environments without a public webhook URL still get status updates.
from django.db import migrations

FUNC = "apps.courier.services.reconcile"


def create(apps, schema_editor):
    Schedule = apps.get_model("django_q", "Schedule")
    Schedule.objects.update_or_create(
        func=FUNC, defaults={"name": "Steadfast reconciliation", "schedule_type": "I",
                             "minutes": 120, "repeats": -1})


def remove(apps, schema_editor):
    apps.get_model("django_q", "Schedule").objects.filter(func=FUNC).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("courier", "0001_initial"),
        ("django_q", "0019_alter_task_options_alter_ormq_key_alter_ormq_lock_and_more"),
    ]

    operations = [migrations.RunPython(create, remove)]
