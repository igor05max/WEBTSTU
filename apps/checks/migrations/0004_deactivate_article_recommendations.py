from django.db import migrations


def deactivate_article_recommendations(apps, schema_editor):
    CheckDefinition = apps.get_model("checks", "CheckDefinition")
    CheckDefinition.objects.filter(code="article_recommendations").update(is_active=False)


class Migration(migrations.Migration):
    dependencies = [
        ("checks", "0003_alter_checkrun_status"),
    ]

    operations = [
        migrations.RunPython(
            deactivate_article_recommendations,
            migrations.RunPython.noop,
        ),
    ]
