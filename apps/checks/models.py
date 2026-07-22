from django.db import models


class CheckRunStatus(models.TextChoices):
    PENDING = "pending", "Ожидает"
    RUNNING = "running", "Выполняется"
    PASSED = "passed", "Пройдена"
    FAILED = "failed", "Не пройдена"
    PARTIAL = "partial", "Выполнена частично"
    NOT_PERFORMED = "not_performed", "Не выполнена"


class CheckDefinition(models.Model):
    code = models.CharField(max_length=64, unique=True, verbose_name="Код")
    name = models.CharField(max_length=255, verbose_name="Название")
    description = models.TextField(blank=True, verbose_name="Описание")
    order = models.PositiveIntegerField(default=100, verbose_name="Порядок")
    is_active = models.BooleanField(default=True, verbose_name="Активна")
    is_blocking = models.BooleanField(default=True, verbose_name="Блокирующая")
    backend_code = models.CharField(
        max_length=64,
        default="mock_basic",
        verbose_name="Код обработчика",
    )

    class Meta:
        ordering = ("order", "id")
        verbose_name = "Определение проверки"
        verbose_name_plural = "Определения проверок"

    def __str__(self):
        return self.name


class CheckRun(models.Model):
    submission = models.ForeignKey(
        "submissions.Submission",
        on_delete=models.CASCADE,
        related_name="check_runs",
        verbose_name="Заявка",
    )
    version = models.ForeignKey(
        "submissions.SubmissionVersion",
        on_delete=models.CASCADE,
        related_name="check_runs",
        verbose_name="Версия",
    )
    check_definition = models.ForeignKey(
        CheckDefinition,
        on_delete=models.PROTECT,
        related_name="runs",
        verbose_name="Проверка",
    )
    status = models.CharField(
        max_length=16,
        choices=CheckRunStatus.choices,
        default=CheckRunStatus.PENDING,
        verbose_name="Статус",
    )
    result_payload = models.JSONField(default=dict, blank=True, verbose_name="Результат")
    started_at = models.DateTimeField(null=True, blank=True, verbose_name="Запущена")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="Завершена")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создана")

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Запуск проверки"
        verbose_name_plural = "Запуски проверок"

    def __str__(self):
        return f"{self.submission_id} / {self.check_definition.code} / {self.status}"


class GeminiConfiguration(models.Model):
    model_name = models.CharField(max_length=160, blank=True, verbose_name="Модель")
    available_models = models.JSONField(default=list, blank=True, verbose_name="Доступные модели")
    models_refreshed_at = models.DateTimeField(null=True, blank=True, verbose_name="Список обновлён")
    last_test_status = models.CharField(max_length=32, blank=True, verbose_name="Статус последней проверки")
    last_test_details = models.JSONField(default=dict, blank=True, verbose_name="Диагностика")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        verbose_name = "Настройка Gemini"
        verbose_name_plural = "Настройки Gemini"

    @classmethod
    def load(cls):
        configuration, _created = cls.objects.get_or_create(pk=1)
        return configuration

    def __str__(self):
        return self.model_name or "Gemini"
