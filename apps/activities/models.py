from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


def get_current_academic_year():
    """Return the academic year that contains today's date."""
    today = timezone.localdate()
    start_year = today.year if today.month >= 8 else today.year - 1
    return f"{start_year}/{start_year + 1}"


class ActivityArea(models.TextChoices):
    RESEARCH = "research", "Научная работа"
    METHODICAL = "methodical", "Методическая работа"
    ORGANISATIONAL = "organisational", "Организационная и воспитательная работа"
    DEVELOPMENT = "development", "Повышение квалификации"
    OTHER = "other", "Другое"


class ActivityPeriod(models.TextChoices):
    FIRST_HALF = "first_half", "I полугодие"
    SECOND_HALF = "second_half", "II полугодие"
    WHOLE_YEAR = "whole_year", "Весь учебный год"


class ActivityStatus(models.TextChoices):
    PLANNED = "planned", "Запланировано"
    IN_PROGRESS = "in_progress", "В работе"
    COMPLETED = "completed", "Выполнено"


class ActivityType(models.Model):
    code = models.CharField(max_length=64, unique=True, verbose_name="Код")
    name = models.CharField(max_length=255, unique=True, verbose_name="Название")
    area = models.CharField(
        max_length=32,
        choices=ActivityArea.choices,
        default=ActivityArea.RESEARCH,
        verbose_name="Направление",
    )
    requires_grant_type = models.BooleanField(
        default=False,
        verbose_name="Требует вида гранта",
    )
    is_active = models.BooleanField(default=True, verbose_name="Активен")

    class Meta:
        ordering = ("area", "name")
        verbose_name = "Тип результата"
        verbose_name_plural = "Типы результатов"

    def __str__(self):
        return self.name


class GrantType(models.Model):
    code = models.CharField(max_length=64, unique=True, verbose_name="Код")
    name = models.CharField(max_length=255, unique=True, verbose_name="Название")
    is_active = models.BooleanField(default=True, verbose_name="Активен")

    class Meta:
        ordering = ("name",)
        verbose_name = "Вид гранта"
        verbose_name_plural = "Виды грантов"

    def __str__(self):
        return self.name


class PlanningRosterEntry(models.Model):
    """A teaching staff member found in an individual plan for an academic year."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="planning_roster_entries",
        verbose_name="Сотрудник",
    )
    academic_year = models.CharField(max_length=9, db_index=True, verbose_name="Учебный год")
    department_code = models.CharField(max_length=64, db_index=True, verbose_name="Кафедра")
    full_name = models.CharField(max_length=255, verbose_name="ФИО из плана")
    source_files = models.JSONField(default=list, blank=True, verbose_name="Файлы-источники")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        ordering = ("academic_year", "department_code", "full_name")
        constraints = [
            models.UniqueConstraint(
                fields=("academic_year", "department_code", "user"),
                name="unique_planning_roster_member",
            )
        ]
        verbose_name = "Сотрудник из индивидуального плана"
        verbose_name_plural = "Состав преподавателей из индивидуальных планов"

    def __str__(self):
        return f"{self.department_code}: {self.full_name} ({self.academic_year})"


class Activity(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="planned_activities",
        verbose_name="Ответственный",
    )
    activity_type = models.ForeignKey(
        ActivityType,
        on_delete=models.PROTECT,
        related_name="activities",
        verbose_name="Тип результата",
    )
    grant_type = models.ForeignKey(
        GrantType,
        on_delete=models.SET_NULL,
        related_name="activities",
        null=True,
        blank=True,
        verbose_name="Вид гранта",
    )
    title = models.CharField(max_length=700, verbose_name="Планируемый результат")
    quantity = models.PositiveSmallIntegerField(default=1, verbose_name="Количество")
    academic_year = models.CharField(
        max_length=9,
        default=get_current_academic_year,
        db_index=True,
        verbose_name="Учебный год",
    )
    period = models.CharField(
        max_length=16,
        choices=ActivityPeriod.choices,
        default=ActivityPeriod.WHOLE_YEAR,
        verbose_name="Период выполнения",
    )
    status = models.CharField(
        max_length=16,
        choices=ActivityStatus.choices,
        default=ActivityStatus.PLANNED,
        db_index=True,
        verbose_name="Статус",
    )
    source_file = models.CharField(max_length=500, blank=True, verbose_name="Файл-источник")
    source_sheet = models.CharField(max_length=64, blank=True, verbose_name="Лист-источник")
    source_cell = models.CharField(max_length=16, blank=True, verbose_name="Ячейка-источник")
    source_text = models.TextField(blank=True, verbose_name="Исходная формулировка")
    source_key = models.CharField(
        max_length=64,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Ключ импорта",
    )
    source_is_overridden = models.BooleanField(
        default=False,
        verbose_name="Отредактировано пользователем после импорта",
    )
    collaborators = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="joint_activities",
        blank=True,
        verbose_name="Соисполнители",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        ordering = ("academic_year", "period", "-updated_at", "-id")
        verbose_name = "Планируемый результат"
        verbose_name_plural = "Планируемые результаты"

    def __str__(self):
        return self.title

    def clean(self):
        super().clean()
        if self.quantity < 1:
            raise ValidationError({"quantity": "Количество должно быть не меньше 1."})
        activity_type = self.activity_type
        if activity_type and activity_type.requires_grant_type and not self.grant_type_id:
            raise ValidationError({"grant_type": "Для гранта выберите его вид."})
        if activity_type and not activity_type.requires_grant_type and self.grant_type_id:
            raise ValidationError({"grant_type": "Вид гранта можно указать только для гранта."})

    def can_be_managed_by(self, user):
        return bool(user and user.is_authenticated and (user.is_superuser or user.pk == self.owner_id))

    @property
    def imported_from_plan(self):
        return bool(self.source_key)


class ScientificResult(models.Model):
    """A confirmed factual result imported from the university science registry."""

    source_key = models.CharField(max_length=64, unique=True, db_index=True, verbose_name="Ключ импорта")
    source_id = models.CharField(max_length=64, db_index=True, verbose_name="ID результата в источнике")
    external_author_id = models.CharField(max_length=32, blank=True, db_index=True, verbose_name="Кадровый ID автора")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="scientific_results",
        null=True,
        blank=True,
        verbose_name="Сотрудник",
    )
    activity_type = models.ForeignKey(
        ActivityType,
        on_delete=models.PROTECT,
        related_name="scientific_results",
        verbose_name="Тип результата",
    )
    planned_activity = models.ForeignKey(
        Activity,
        on_delete=models.SET_NULL,
        related_name="scientific_results",
        null=True,
        blank=True,
        verbose_name="Пункт плана",
    )
    title = models.CharField(max_length=700, verbose_name="Фактический результат")
    result_year = models.PositiveSmallIntegerField(db_index=True, verbose_name="Год результата")
    academic_year = models.CharField(max_length=9, db_index=True, verbose_name="Учебный год")
    publication_name = models.CharField(max_length=700, blank=True, verbose_name="Издание или мероприятие")
    publication_details = models.CharField(max_length=700, blank=True, verbose_name="Выходные сведения")
    bibliographic_data = models.TextField(blank=True, verbose_name="Библиографическое описание")
    source_file = models.CharField(max_length=500, verbose_name="Файл-источник")
    source_line = models.PositiveIntegerField(default=0, verbose_name="Строка источника")
    source_payload = models.JSONField(default=dict, blank=True, verbose_name="Исходные данные")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создан")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлён")

    class Meta:
        ordering = ("-result_year", "activity_type__name", "title", "id")
        verbose_name = "Фактический научный результат"
        verbose_name_plural = "Фактические научные результаты"

    def __str__(self):
        return self.title
