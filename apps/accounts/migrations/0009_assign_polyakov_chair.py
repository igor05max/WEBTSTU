from django.db import migrations


POLYAKOV_USERNAME = "polyakov_dv"
INFORMATION_SECURITY_CHAIR_CODE = (
    "kafedra-informatsionnye-sistemy-i-zaschita-informatsii"
)


def assign_polyakov_chair(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    OrgUnit = apps.get_model("directory", "OrgUnit")

    chair = (
        OrgUnit.objects.filter(code=INFORMATION_SECURITY_CHAIR_CODE)
        .order_by("id")
        .first()
    )
    if chair is None:
        return

    User.objects.filter(username=POLYAKOV_USERNAME).update(
        chair_org_unit_id=chair.id,
    )


def unassign_polyakov_chair(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    OrgUnit = apps.get_model("directory", "OrgUnit")

    chair = (
        OrgUnit.objects.filter(code=INFORMATION_SECURITY_CHAIR_CODE)
        .order_by("id")
        .first()
    )
    if chair is None:
        return

    User.objects.filter(
        username=POLYAKOV_USERNAME,
        chair_org_unit_id=chair.id,
    ).update(chair_org_unit_id=None)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0008_publication_plan"),
        ("directory", "0008_publicationtopic_formattingtemplate"),
    ]

    operations = [
        migrations.RunPython(assign_polyakov_chair, unassign_polyakov_chair),
    ]
