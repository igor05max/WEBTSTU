import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("directory", "0008_publicationtopic_formattingtemplate"),
        ("submissions", "0008_submission_document_metadata"),
    ]

    operations = [
        migrations.AddField(
            model_name="submission",
            name="formatting_check_requested",
            field=models.BooleanField(default=True, verbose_name="Проверять оформление по шаблону"),
        ),
        migrations.AddField(
            model_name="submission",
            name="formatting_rules_snapshot",
            field=models.JSONField(blank=True, default=dict, verbose_name="Правила оформления на момент отправки"),
        ),
        migrations.AddField(
            model_name="submission",
            name="formatting_template",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="submissions",
                to="directory.formattingtemplate",
                verbose_name="Шаблон оформления",
            ),
        ),
        migrations.AddField(
            model_name="submission",
            name="publication_topic",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="submissions",
                to="directory.publicationtopic",
                verbose_name="Тема или событие",
            ),
        ),
        migrations.AlterField(
            model_name="submission",
            name="journal",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="submissions",
                to="directory.journal",
                verbose_name="Журнал",
            ),
        ),
    ]
