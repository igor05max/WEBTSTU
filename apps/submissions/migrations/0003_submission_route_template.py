import django.db.models.deletion
from django.db import migrations, models


def backfill_submission_route_template(apps, schema_editor):
    Submission = apps.get_model("submissions", "Submission")
    WorkflowRun = apps.get_model("workflow", "WorkflowRun")

    for submission in Submission.objects.filter(route_template__isnull=True):
        latest_run = WorkflowRun.objects.filter(submission_id=submission.id).order_by("-created_at", "-pk").first()
        if latest_run is None:
            continue
        submission.route_template_id = latest_run.route_template_id
        submission.save(update_fields=["route_template"])


def noop_reverse(apps, schema_editor):
    return


class Migration(migrations.Migration):

    dependencies = [
        ("workflow", "0006_alter_workflowstep_step_template"),
        ("submissions", "0002_submission_direction"),
    ]

    operations = [
        migrations.AddField(
            model_name="submission",
            name="route_template",
            field=models.ForeignKey(
                blank=True,
                help_text="Маршрут выбирается пользователем при отправке статьи и должен соответствовать выбранному направлению.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="submissions",
                to="workflow.routetemplate",
                verbose_name="Шаблон маршрута",
            ),
        ),
        migrations.RunPython(backfill_submission_route_template, noop_reverse),
    ]
