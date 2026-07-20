from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import migrations


def sync_non_root_users(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.exclude(username=settings.ROOT_ADMIN_USERNAME).update(
        password=make_password(settings.DEFAULT_USER_PASSWORD),
        is_staff=False,
        is_superuser=False,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_alter_user_org_unit"),
    ]

    operations = [
        migrations.RunPython(sync_non_root_users, migrations.RunPython.noop),
    ]
