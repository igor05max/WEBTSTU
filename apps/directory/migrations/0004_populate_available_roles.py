from django.db import migrations


def populate_available_roles(apps, schema_editor):
    OrgUnit = apps.get_model("directory", "OrgUnit")
    User = apps.get_model("accounts", "User")
    RouteStepTemplate = apps.get_model("workflow", "RouteStepTemplate")
    WorkflowStep = apps.get_model("workflow", "WorkflowStep")
    ApprovalTask = apps.get_model("workflow", "ApprovalTask")

    org_units = {org_unit.pk: org_unit for org_unit in OrgUnit.objects.all()}
    role_pairs = set()

    for user in User.objects.exclude(org_unit_id__isnull=True):
        for role_id in user.groups.values_list("id", flat=True):
            role_pairs.add((user.org_unit_id, role_id))

    for step in RouteStepTemplate.objects.exclude(target_unit_id__isnull=True).exclude(target_group_id__isnull=True):
        role_pairs.add((step.target_unit_id, step.target_group_id))

    for step in WorkflowStep.objects.exclude(assigned_unit_id__isnull=True).exclude(assigned_group_id__isnull=True):
        role_pairs.add((step.assigned_unit_id, step.assigned_group_id))

    for task in ApprovalTask.objects.exclude(assigned_unit_id__isnull=True).exclude(assigned_group_id__isnull=True):
        role_pairs.add((task.assigned_unit_id, task.assigned_group_id))

    for org_unit_id, role_id in role_pairs:
        org_unit = org_units.get(org_unit_id)
        if org_unit is not None:
            org_unit.available_roles.add(role_id)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_alter_user_org_unit"),
        ("directory", "0003_alter_orgunit_options_orgunit_available_roles_and_more"),
        ("workflow", "0005_alter_approvaltask_assigned_unit_and_more"),
    ]

    operations = [
        migrations.RunPython(populate_available_roles, migrations.RunPython.noop),
    ]
