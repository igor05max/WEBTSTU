from django.db import migrations, models

import apps.submissions.models


class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0012_submission_responsible_author"),
    ]

    operations = [
        migrations.AddField(
            model_name="submissionversion",
            name="latex_compile_message",
            field=models.TextField(blank=True, verbose_name="Сообщение сборки LaTeX"),
        ),
        migrations.AddField(
            model_name="submissionversion",
            name="latex_compile_status",
            field=models.CharField(
                choices=[
                    ("pending", "Ожидает сборки"),
                    ("ready", "PDF собран"),
                    ("error", "Ошибка сборки"),
                    ("blocked", "Сборка заблокирована"),
                    ("unavailable", "Компилятор недоступен"),
                ],
                default="pending",
                max_length=16,
                verbose_name="Состояние сборки LaTeX",
            ),
        ),
        migrations.AddField(
            model_name="submissionversion",
            name="project_archive",
            field=models.FileField(
                blank=True,
                upload_to=apps.submissions.models.submission_project_upload_to,
                verbose_name="Архив LaTeX-проекта",
            ),
        ),
        migrations.AddField(
            model_name="submissionversion",
            name="project_main_path",
            field=models.CharField(
                blank=True,
                max_length=1000,
                verbose_name="Главный файл LaTeX-проекта",
            ),
        ),
        migrations.AddField(
            model_name="submissionversion",
            name="project_manifest",
            field=models.JSONField(
                blank=True,
                default=dict,
                verbose_name="Состав LaTeX-проекта",
            ),
        ),
        migrations.AddField(
            model_name="submissionversion",
            name="rendered_pdf",
            field=models.FileField(
                blank=True,
                upload_to=apps.submissions.models.submission_rendered_upload_to,
                verbose_name="Собранный PDF",
            ),
        ),
    ]
