from django.db import models


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
