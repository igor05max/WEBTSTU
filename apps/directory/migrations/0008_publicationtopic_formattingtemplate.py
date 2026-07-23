import apps.directory.models
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("directory", "0007_journal_policy_article_type_limits"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PublicationTopic",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=500, verbose_name="Название темы или события")),
                (
                    "normalized_name",
                    models.CharField(
                        db_index=True,
                        max_length=500,
                        unique=True,
                        verbose_name="Нормализованное название",
                    ),
                ),
                ("aliases", models.JSONField(blank=True, default=list, verbose_name="Варианты названия")),
                ("search_index", models.TextField(blank=True, verbose_name="Поисковый индекс")),
                (
                    "last_used_at",
                    models.DateTimeField(blank=True, db_index=True, null=True, verbose_name="Последнее использование"),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлено")),
                ("is_active", models.BooleanField(default=True, verbose_name="Активно")),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_publication_topics",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Создал",
                    ),
                ),
                (
                    "merged_into",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="merged_duplicates",
                        to="directory.publicationtopic",
                        verbose_name="Объединено с",
                    ),
                ),
            ],
            options={
                "verbose_name": "Тема или событие публикации",
                "verbose_name_plural": "Темы и события публикаций",
                "ordering": ("-last_used_at", "-updated_at", "name"),
            },
        ),
        migrations.CreateModel(
            name="FormattingTemplate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("version_number", models.PositiveIntegerField(default=1, verbose_name="Версия")),
                (
                    "file",
                    models.FileField(
                        upload_to=apps.directory.models.formatting_template_upload_to,
                        verbose_name="Файл шаблона",
                    ),
                ),
                (
                    "analysis_status",
                    models.CharField(
                        choices=[
                            ("pending", "Ожидает обработки"),
                            ("processing", "Обрабатывается"),
                            ("ready", "Правила извлечены"),
                            ("partial", "Извлечено частично"),
                            ("failed", "Не удалось обработать"),
                        ],
                        default="pending",
                        max_length=16,
                        verbose_name="Статус обработки",
                    ),
                ),
                ("analysis_message", models.TextField(blank=True, verbose_name="Результат обработки")),
                ("source_text", models.TextField(blank=True, verbose_name="Извлечённый текст")),
                ("extracted_rules", models.JSONField(blank=True, default=dict, verbose_name="Извлечённые правила")),
                ("rule_conflicts", models.JSONField(blank=True, default=list, verbose_name="Конфликты правил")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Загружен")),
                (
                    "article_type",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="formatting_templates",
                        to="directory.articletype",
                        verbose_name="Тип материала",
                    ),
                ),
                (
                    "journal",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="formatting_templates",
                        to="directory.journal",
                        verbose_name="Журнал",
                    ),
                ),
                (
                    "uploaded_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="uploaded_formatting_templates",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Загрузил",
                    ),
                ),
                (
                    "publication_topic",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="formatting_templates",
                        to="directory.publicationtopic",
                        verbose_name="Тема или событие",
                    ),
                ),
            ],
            options={
                "verbose_name": "Шаблон оформления",
                "verbose_name_plural": "Шаблоны оформления",
                "ordering": ("-created_at", "-version_number"),
                "constraints": [
                    models.CheckConstraint(
                        condition=(
                            models.Q(journal__isnull=False, publication_topic__isnull=True)
                            | models.Q(journal__isnull=True, publication_topic__isnull=False)
                        ),
                        name="formatting_template_exactly_one_target",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(journal__isnull=False),
                        fields=("journal", "article_type", "version_number"),
                        name="unique_journal_formatting_template_version",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(publication_topic__isnull=False),
                        fields=("publication_topic", "article_type", "version_number"),
                        name="unique_topic_formatting_template_version",
                    ),
                ],
            },
        ),
    ]
