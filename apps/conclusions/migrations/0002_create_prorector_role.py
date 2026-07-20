from django.db import migrations


PRORECTOR_ROLE_NAME = "Проректор по научной работе"


def create_prorector_role(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.get_or_create(name=PRORECTOR_ROLE_NAME)


class Migration(migrations.Migration):
    dependencies = [
        ("conclusions", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_prorector_role, migrations.RunPython.noop),
    ]
