from django.conf import settings
from django.db import models


def submission_version_upload_to(instance, filename):
    submission_id = instance.submission_id or "draft"
    return f"submission_versions/{submission_id}/v{instance.version_number}/{filename}"


def submission_appeal_upload_to(instance, filename):
    submission_id = instance.submission_id or "submission"
    return f"submission_appeals/{submission_id}/{filename}"


class SubmissionStatus(models.TextChoices):
    DRAFT = "draft", "Создана"
    SUBMITTED = "submitted", "Готова к отправке"
    AUTO_CHECKING = "auto_checking", "Проверка идёт"
    IN_REVIEW = "in_review", "На согласовании"
    REVISION_REQUESTED = "revision_requested", "Требует доработки"
    APPEAL_PENDING = "appeal_pending", "Апелляция на рассмотрении"
    APPROVED = "approved", "Согласована"
    REJECTED = "rejected", "Отклонена"


class Submission(models.Model):
    title = models.CharField(max_length=500, verbose_name="Название")
    abstract = models.TextField(blank=True, verbose_name="Аннотация")
    document_authors = models.TextField(
        blank=True,
        verbose_name="Авторы из документа",
        help_text="Имена авторов в том виде, в котором они указаны в загруженном материале.",
    )
    organizations = models.TextField(blank=True, verbose_name="Организации авторов")
    contact_emails = models.TextField(blank=True, verbose_name="Контактные e-mail")
    keywords = models.TextField(blank=True, verbose_name="Ключевые слова")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="submissions",
        verbose_name="Отправитель",
        help_text="Пользователь, который создал и отправляет заявку в системе.",
    )
    authors = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="authored_submissions",
        verbose_name="Авторы",
        blank=True,
        help_text="Все авторы материала. Отправитель добавляется автоматически.",
    )
    journal = models.ForeignKey(
        "directory.Journal",
        on_delete=models.PROTECT,
        related_name="submissions",
        null=True,
        blank=True,
        verbose_name="Журнал",
    )
    publication_topic = models.ForeignKey(
        "directory.PublicationTopic",
        on_delete=models.PROTECT,
        related_name="submissions",
        null=True,
        blank=True,
        verbose_name="Тема или событие",
    )
    article_type = models.ForeignKey(
        "directory.ArticleType",
        on_delete=models.PROTECT,
        related_name="submissions",
        verbose_name="Тип материала",
    )
    direction = models.ForeignKey(
        "directory.Direction",
        on_delete=models.PROTECT,
        related_name="submissions",
        null=True,
        blank=True,
        verbose_name="Область экспертизы",
        help_text="Область экспертизы выбирается пользователем при отправке материала на согласование.",
    )
    route_template = models.ForeignKey(
        "workflow.RouteTemplate",
        on_delete=models.PROTECT,
        related_name="submissions",
        null=True,
        blank=True,
        verbose_name="Шаблон маршрута",
        help_text=(
            "Базовый маршрут обычно определяется типом материала автоматически. "
            "Область экспертизы влияет на состав согласующих внутри его шагов."
        ),
    )
    formatting_template = models.ForeignKey(
        "directory.FormattingTemplate",
        on_delete=models.PROTECT,
        related_name="submissions",
        null=True,
        blank=True,
        verbose_name="Шаблон оформления",
    )
    formatting_rules_snapshot = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Правила оформления на момент отправки",
    )
    formatting_check_requested = models.BooleanField(
        default=True,
        verbose_name="Проверять оформление по шаблону",
    )
    status = models.CharField(
        max_length=32,
        choices=SubmissionStatus.choices,
        default=SubmissionStatus.DRAFT,
        verbose_name="Статус",
    )
    current_version = models.ForeignKey(
        "submissions.SubmissionVersion",
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
        verbose_name="Текущая версия",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создана")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлена")
    submitted_at = models.DateTimeField(null=True, blank=True, verbose_name="Отправлена")

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Заявка"
        verbose_name_plural = "Заявки"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.author_id and not self.authors.filter(pk=self.author_id).exists():
            self.authors.add(self.author_id)

    def get_authors_display(self):
        return ", ".join(str(author) for author in self.authors.all())

    @property
    def destination_name(self):
        if self.journal_id:
            return self.journal.name
        if self.publication_topic_id:
            return self.publication_topic.name
        return ""

    @property
    def destination_label(self):
        return "Журнал" if self.journal_id else "Тема или событие"

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"


class SubmissionVersion(models.Model):
    submission = models.ForeignKey(
        Submission,
        on_delete=models.CASCADE,
        related_name="versions",
        verbose_name="Заявка",
    )
    version_number = models.PositiveIntegerField(verbose_name="Номер версии")
    file = models.FileField(upload_to=submission_version_upload_to, verbose_name="Файл")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="uploaded_submission_versions",
        verbose_name="Загрузил",
    )
    comment = models.TextField(blank=True, verbose_name="Комментарий")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создана")

    class Meta:
        ordering = ("submission_id", "version_number")
        constraints = [
            models.UniqueConstraint(
                fields=("submission", "version_number"),
                name="unique_submission_version_number",
            )
        ]
        verbose_name = "Версия заявки"
        verbose_name_plural = "Версии заявки"

    def __str__(self):
        return f"{self.submission_id} / v{self.version_number}"


class SubmissionAppealStatus(models.TextChoices):
    PENDING = "pending", "На рассмотрении"
    APPROVED = "approved", "Апелляция принята"
    REJECTED = "rejected", "Апелляция отклонена"


class SubmissionAppeal(models.Model):
    submission = models.OneToOneField(
        Submission,
        on_delete=models.CASCADE,
        related_name="appeal",
        verbose_name="Заявка",
    )
    rejected_task = models.ForeignKey(
        "workflow.ApprovalTask",
        on_delete=models.PROTECT,
        related_name="submission_appeals",
        verbose_name="Отклоняющая задача",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="submission_appeals",
        verbose_name="Автор апелляции",
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="review_submission_appeals",
        verbose_name="Кто рассматривает апелляцию",
    )
    comment = models.TextField(verbose_name="Комментарий автора")
    attachment = models.FileField(
        upload_to=submission_appeal_upload_to,
        blank=True,
        verbose_name="Файл апелляции",
    )
    status = models.CharField(
        max_length=16,
        choices=SubmissionAppealStatus.choices,
        default=SubmissionAppealStatus.PENDING,
        verbose_name="Статус апелляции",
    )
    decision_comment = models.TextField(blank=True, verbose_name="Комментарий по апелляции")
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="decided_submission_appeals",
        null=True,
        blank=True,
        verbose_name="Кто принял решение",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создана")
    decided_at = models.DateTimeField(null=True, blank=True, verbose_name="Решение принято")

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Апелляция по заявке"
        verbose_name_plural = "Апелляции по заявкам"

    def __str__(self):
        return f"Апелляция по заявке #{self.submission_id}"
