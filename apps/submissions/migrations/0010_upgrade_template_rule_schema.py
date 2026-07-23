from django.db import migrations


def upgrade_template_rules(apps, schema_editor):
    from document_template_engine import normalize_template_rules

    FormattingTemplate = apps.get_model("directory", "FormattingTemplate")
    Submission = apps.get_model("submissions", "Submission")

    for template in FormattingTemplate.objects.exclude(extracted_rules={}):
        normalized = normalize_template_rules(template.extracted_rules)
        if normalized != template.extracted_rules:
            FormattingTemplate.objects.filter(pk=template.pk).update(
                extracted_rules=normalized
            )

    for submission in Submission.objects.exclude(formatting_rules_snapshot={}):
        snapshot = dict(submission.formatting_rules_snapshot or {})
        effective = snapshot.get("effective")
        if not isinstance(effective, dict) or not effective:
            continue
        normalized = normalize_template_rules(effective)
        if normalized != effective:
            snapshot["effective"] = normalized
            Submission.objects.filter(pk=submission.pk).update(
                formatting_rules_snapshot=snapshot
            )


class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0009_submission_formatting_check_requested_and_more"),
    ]

    operations = [
        migrations.RunPython(upgrade_template_rules, migrations.RunPython.noop),
    ]
