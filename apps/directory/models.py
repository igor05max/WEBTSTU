import re
import unicodedata
from pathlib import Path

from django.conf import settings
from django.db import models


SPACE_RE = re.compile(r"\s+")
NON_WORD_RE = re.compile(r"[^\w]+", re.UNICODE)


def normalize_catalog_name(value):
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold().replace("ё", "е")
    normalized = NON_WORD_RE.sub(" ", normalized)
    return SPACE_RE.sub(" ", normalized).strip()


def formatting_template_upload_to(instance, filename):
    target_kind = "journal" if instance.journal_id else "topic"
    target_id = instance.journal_id or instance.publication_topic_id or "draft"
    safe_name = Path(filename or "template").name
    return (
        f"formatting_templates/{target_kind}/{target_id}/"
        f"{instance.article_type_id or 'material'}/v{instance.version_number}/{safe_name}"
    )


class OrgUnitType(models.TextChoices):
    DEPARTMENT = "department", "Кафедра"
    OFFICE = "office", "Отдел"
    COMMITTEE = "committee", "Комиссия"


class OrgUnit(models.Model):
    name = models.CharField(max_length=255, unique=True, verbose_name="Название")
    code = models.CharField(max_length=64, blank=True, verbose_name="Код")
    type = models.CharField(
        max_length=32,
        choices=OrgUnitType.choices,
        default=OrgUnitType.DEPARTMENT,
        verbose_name="Тип группы",
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        related_name="children",
        null=True,
        blank=True,
        verbose_name="Родительская группа",
    )
    available_roles = models.ManyToManyField(
        "auth.Group",
        related_name="org_units",
        blank=True,
        verbose_name="Роли группы",
        help_text="Выберите роли, которые могут существовать внутри этой группы.",
    )
    is_active = models.BooleanField(default=True, verbose_name="Активно")

    class Meta:
        ordering = ("name",)
        verbose_name = "Группа"
        verbose_name_plural = "Группы"

    def __str__(self):
        return self.name


class Journal(models.Model):
    name = models.CharField(max_length=255, unique=True, verbose_name="Название")
    issn = models.CharField(max_length=128, blank=True, db_index=True, verbose_name="ISSN")
    search_index = models.TextField(blank=True, verbose_name="Поисковый индекс")
    white_list_level = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Уровень белого списка",
    )
    editorial_policy = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Политика автоматической проверки",
        help_text=(
            "JSON с required_sections, min_words, max_words и "
            "disallow_uncited_references. Пустое значение использует общие правила."
        ),
    )
    is_active = models.BooleanField(default=True, verbose_name="Активен")

    class Meta:
        ordering = ("name",)
        verbose_name = "Журнал"
        verbose_name_plural = "Журналы"

    def __str__(self):
        return self.name


class Position(models.Model):
    name = models.CharField(max_length=255, unique=True, verbose_name="Название")
    is_active = models.BooleanField(default=True, verbose_name="Активна")

    class Meta:
        ordering = ("name",)
        verbose_name = "Должность"
        verbose_name_plural = "Должности"

    def __str__(self):
        return self.name


class ArticleType(models.Model):
    code = models.CharField(max_length=64, unique=True, verbose_name="Код")
    name = models.CharField(max_length=255, verbose_name="Название")
    min_word_count = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Минимум слов",
    )
    max_word_count = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Максимум слов",
    )
    is_active = models.BooleanField(default=True, verbose_name="Активен")

    class Meta:
        ordering = ("name",)
        verbose_name = "Тип материала"
        verbose_name_plural = "Типы материалов"

    def __str__(self):
        return self.name


class PublicationTopic(models.Model):
    name = models.CharField(max_length=500, verbose_name="Название темы или события")
    normalized_name = models.CharField(
        max_length=500,
        unique=True,
        db_index=True,
        verbose_name="Нормализованное название",
    )
    aliases = models.JSONField(default=list, blank=True, verbose_name="Варианты названия")
    search_index = models.TextField(blank=True, verbose_name="Поисковый индекс")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_publication_topics",
        null=True,
        blank=True,
        verbose_name="Создал",
    )
    merged_into = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        related_name="merged_duplicates",
        null=True,
        blank=True,
        verbose_name="Объединено с",
    )
    last_used_at = models.DateTimeField(null=True, blank=True, db_index=True, verbose_name="Последнее использование")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")
    is_active = models.BooleanField(default=True, verbose_name="Активно")

    class Meta:
        ordering = ("-last_used_at", "-updated_at", "name")
        verbose_name = "Тема или событие публикации"
        verbose_name_plural = "Темы и события публикаций"

    def save(self, *args, **kwargs):
        self.name = SPACE_RE.sub(" ", str(self.name or "").strip())
        self.normalized_name = normalize_catalog_name(self.name)
        alias_values = [
            SPACE_RE.sub(" ", str(value or "").strip())
            for value in (self.aliases or [])
        ]
        self.aliases = list(dict.fromkeys(value for value in alias_values if value))
        searchable = [self.name, self.normalized_name, *self.aliases]
        self.search_index = "\n".join(dict.fromkeys(value.casefold() for value in searchable if value))
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class FormattingTemplateStatus(models.TextChoices):
    PENDING = "pending", "Ожидает обработки"
    PROCESSING = "processing", "Обрабатывается"
    READY = "ready", "Правила извлечены"
    PARTIAL = "partial", "Извлечено частично"
    FAILED = "failed", "Не удалось обработать"


class FormattingTemplate(models.Model):
    journal = models.ForeignKey(
        Journal,
        on_delete=models.PROTECT,
        related_name="formatting_templates",
        null=True,
        blank=True,
        verbose_name="Журнал",
    )
    publication_topic = models.ForeignKey(
        PublicationTopic,
        on_delete=models.PROTECT,
        related_name="formatting_templates",
        null=True,
        blank=True,
        verbose_name="Тема или событие",
    )
    article_type = models.ForeignKey(
        ArticleType,
        on_delete=models.PROTECT,
        related_name="formatting_templates",
        verbose_name="Тип материала",
    )
    version_number = models.PositiveIntegerField(default=1, verbose_name="Версия")
    file = models.FileField(upload_to=formatting_template_upload_to, verbose_name="Файл шаблона")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="uploaded_formatting_templates",
        verbose_name="Загрузил",
    )
    analysis_status = models.CharField(
        max_length=16,
        choices=FormattingTemplateStatus.choices,
        default=FormattingTemplateStatus.PENDING,
        verbose_name="Статус обработки",
    )
    analysis_message = models.TextField(blank=True, verbose_name="Результат обработки")
    source_text = models.TextField(blank=True, verbose_name="Извлечённый текст")
    extracted_rules = models.JSONField(default=dict, blank=True, verbose_name="Извлечённые правила")
    rule_conflicts = models.JSONField(default=list, blank=True, verbose_name="Конфликты правил")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Загружен")

    class Meta:
        ordering = ("-created_at", "-version_number")
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(journal__isnull=False, publication_topic__isnull=True)
                    | models.Q(journal__isnull=True, publication_topic__isnull=False)
                ),
                name="formatting_template_exactly_one_target",
            ),
            models.UniqueConstraint(
                fields=("journal", "article_type", "version_number"),
                condition=models.Q(journal__isnull=False),
                name="unique_journal_formatting_template_version",
            ),
            models.UniqueConstraint(
                fields=("publication_topic", "article_type", "version_number"),
                condition=models.Q(publication_topic__isnull=False),
                name="unique_topic_formatting_template_version",
            ),
        ]
        verbose_name = "Шаблон оформления"
        verbose_name_plural = "Шаблоны оформления"

    @property
    def target_name(self):
        if self.journal_id:
            return self.journal.name
        if self.publication_topic_id:
            return self.publication_topic.name
        return ""

    @property
    def file_basename(self):
        return Path(self.file.name or "").name

    def __str__(self):
        return f"{self.target_name} / {self.article_type} / v{self.version_number}"


class Direction(models.Model):
    code = models.CharField(max_length=64, unique=True, verbose_name="Код")
    name = models.CharField(max_length=255, unique=True, verbose_name="Название")
    description = models.TextField(blank=True, verbose_name="Описание")
    is_active = models.BooleanField(default=True, verbose_name="Активно")

    class Meta:
        ordering = ("name",)
        verbose_name = "Область экспертизы"
        verbose_name_plural = "Области экспертизы"

    def __str__(self):
        return self.name
