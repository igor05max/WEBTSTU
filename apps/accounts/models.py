from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    position = models.ForeignKey(
        "directory.Position",
        on_delete=models.SET_NULL,
        related_name="users",
        null=True,
        blank=True,
        verbose_name="Должность",
    )
    org_unit = models.ForeignKey(
        "directory.OrgUnit",
        on_delete=models.SET_NULL,
        related_name="users",
        null=True,
        blank=True,
        verbose_name="Группа",
        help_text="Начните вводить название группы и выберите вариант из списка. Кнопка '+' создает новую группу.",
    )
    chair_org_unit = models.ForeignKey(
        "directory.OrgUnit",
        on_delete=models.SET_NULL,
        related_name="chair_users",
        null=True,
        blank=True,
        verbose_name="Кафедра",
        limit_choices_to={"name__startswith": "Кафедра"},
        help_text="Кафедра из кадрового справочника. Не используется как рабочая группа маршрута.",
    )
    external_directory_id = models.CharField(
        max_length=32,
        unique=True,
        null=True,
        blank=True,
        verbose_name="ID сотрудника во внешнем справочнике",
    )

    class Meta:
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"

    def get_chair_name(self):
        if self.chair_org_unit is None:
            return ""

        name = self.chair_org_unit.name.strip()
        if name.startswith("Кафедра "):
            return name[len("Кафедра ") :].strip().strip('"')
        return name

    def __str__(self):
        full_name = self.get_full_name().strip()
        return full_name or self.username


def publication_plan_upload_to(instance, filename):
    user_id = instance.user_id or "user"
    return f"publication_plans/{user_id}/{filename}"


class PublicationPlan(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="publication_plan",
        verbose_name="Пользователь",
    )
    file = models.FileField(upload_to=publication_plan_upload_to, verbose_name="Файл плана")
    original_filename = models.CharField(max_length=255, blank=True, verbose_name="Имя файла")
    uploaded_at = models.DateTimeField(auto_now=True, verbose_name="Загружен")
    parsed_at = models.DateTimeField(null=True, blank=True, verbose_name="Разобран")

    class Meta:
        verbose_name = "Публикационный план"
        verbose_name_plural = "Публикационные планы"

    def __str__(self):
        return f"План публикаций: {self.user}"


class PublicationPlanItem(models.Model):
    plan = models.ForeignKey(
        PublicationPlan,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="План",
    )
    level = models.CharField(max_length=16, db_index=True, verbose_name="Уровень")
    journal_name = models.CharField(max_length=255, blank=True, verbose_name="Журнал")
    article_title = models.CharField(max_length=700, blank=True, verbose_name="Название статьи")
    raw_text = models.TextField(blank=True, verbose_name="Исходный текст")
    source_sheet = models.CharField(max_length=128, blank=True, verbose_name="Лист")
    source_cell = models.CharField(max_length=32, blank=True, verbose_name="Ячейка")
    order = models.PositiveIntegerField(default=0, verbose_name="Порядок")

    class Meta:
        ordering = ("order", "id")
        verbose_name = "Пункт публикационного плана"
        verbose_name_plural = "Пункты публикационного плана"

    def __str__(self):
        return f"{self.level}: {self.journal_name}"
