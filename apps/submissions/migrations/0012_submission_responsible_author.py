from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def populate_responsible_authors(apps, schema_editor):
    Submission = apps.get_model("submissions", "Submission")
    Submission.objects.filter(responsible_author__isnull=True).update(
        responsible_author_id=models.F("author_id")
    )


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("submissions", "0011_alter_submission_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="submission",
            name="responsible_author",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Основной автор материала. По его кафедре определяется заведующий "
                    "кафедрой для маршрута согласования."
                ),
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="responsible_submissions",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Ответственный автор",
            ),
        ),
        migrations.RunPython(
            populate_responsible_authors,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="submission",
            name="authors",
            field=models.ManyToManyField(
                blank=True,
                help_text="Все зарегистрированные в системе авторы материала.",
                related_name="authored_submissions",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Авторы",
            ),
        ),
    ]
