from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

import apps.accounts.models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0007_chair_head_role"),
    ]

    operations = [
        migrations.CreateModel(
            name="PublicationPlan",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "file",
                    models.FileField(
                        upload_to=apps.accounts.models.publication_plan_upload_to,
                        verbose_name="Файл плана",
                    ),
                ),
                ("original_filename", models.CharField(blank=True, max_length=255, verbose_name="Имя файла")),
                ("uploaded_at", models.DateTimeField(auto_now=True, verbose_name="Загружен")),
                ("parsed_at", models.DateTimeField(blank=True, null=True, verbose_name="Разобран")),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="publication_plan",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Пользователь",
                    ),
                ),
            ],
            options={
                "verbose_name": "Публикационный план",
                "verbose_name_plural": "Публикационные планы",
            },
        ),
        migrations.CreateModel(
            name="PublicationPlanItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("level", models.CharField(db_index=True, max_length=16, verbose_name="Уровень")),
                ("journal_name", models.CharField(blank=True, max_length=255, verbose_name="Журнал")),
                ("article_title", models.CharField(blank=True, max_length=700, verbose_name="Название статьи")),
                ("raw_text", models.TextField(blank=True, verbose_name="Исходный текст")),
                ("source_sheet", models.CharField(blank=True, max_length=128, verbose_name="Лист")),
                ("source_cell", models.CharField(blank=True, max_length=32, verbose_name="Ячейка")),
                ("order", models.PositiveIntegerField(default=0, verbose_name="Порядок")),
                (
                    "plan",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="items",
                        to="accounts.publicationplan",
                        verbose_name="План",
                    ),
                ),
            ],
            options={
                "verbose_name": "Пункт публикационного плана",
                "verbose_name_plural": "Пункты публикационного плана",
                "ordering": ("order", "id"),
            },
        ),
    ]
