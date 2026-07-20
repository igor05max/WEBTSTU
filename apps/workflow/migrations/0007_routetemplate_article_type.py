import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("directory", "0003_alter_orgunit_options_orgunit_available_roles_and_more"),
        ("workflow", "0006_alter_workflowstep_step_template"),
    ]

    operations = [
        migrations.AddField(
            model_name="routetemplate",
            name="article_type",
            field=models.ForeignKey(
                blank=True,
                help_text="Шаблон можно привязать к конкретному типу материала: статье, тезисам или монографии.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="route_templates",
                to="directory.articletype",
                verbose_name="Тип материала",
            ),
        ),
    ]
