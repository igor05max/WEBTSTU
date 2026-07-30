from django.db import migrations, models


TERMINAL_STATUSES = {
    "passed",
    "failed",
    "partial",
    "not_performed",
}


def deduplicate_check_runs(apps, schema_editor):
    CheckRun = apps.get_model("checks", "CheckRun")
    database = schema_editor.connection.alias
    keepers = {}
    delete_ids = []
    runs = CheckRun.objects.using(database).all().order_by(
        "submission_id",
        "version_id",
        "check_definition_id",
        "id",
    )
    for run in runs.iterator():
        key = (
            run.submission_id,
            run.version_id,
            run.check_definition_id,
        )
        timestamp = run.finished_at or run.started_at or run.created_at
        score = (
            run.status in TERMINAL_STATUSES,
            timestamp,
            run.id,
        )
        existing = keepers.get(key)
        if existing is None:
            keepers[key] = (run.id, score)
            continue
        existing_id, existing_score = existing
        if score > existing_score:
            delete_ids.append(existing_id)
            keepers[key] = (run.id, score)
        else:
            delete_ids.append(run.id)

    if delete_ids:
        CheckRun.objects.using(database).filter(id__in=delete_ids).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("checks", "0004_deactivate_article_recommendations"),
    ]

    operations = [
        migrations.AddField(
            model_name="checkrun",
            name="claim_token",
            field=models.UUIDField(
                blank=True,
                editable=False,
                null=True,
                verbose_name="Токен исполнителя",
            ),
        ),
        migrations.AddField(
            model_name="checkrun",
            name="heartbeat_at",
            field=models.DateTimeField(
                blank=True,
                editable=False,
                null=True,
                verbose_name="Активность исполнителя",
            ),
        ),
        migrations.RunPython(
            deduplicate_check_runs,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="checkrun",
            constraint=models.UniqueConstraint(
                fields=("submission", "version", "check_definition"),
                name="unique_check_run_per_submission_version_definition",
            ),
        ),
    ]
