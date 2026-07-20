import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


def conclusion_docx_upload_to(instance, filename):
    run_id = instance.workflow_run_id or "run"
    return f"conclusions/{run_id}/document/{filename}"


def conclusion_pdf_upload_to(instance, filename):
    """Kept for the initial migration; new conclusions are DOCX-only."""
    run_id = instance.workflow_run_id or "run"
    return f"conclusions/{run_id}/pdf/{filename}"


def conclusion_package_upload_to(instance, filename):
    run_id = instance.workflow_run_id or "run"
    return f"conclusions/{run_id}/package/{filename}"


class ConclusionDocument(models.Model):
    workflow_run = models.OneToOneField(
        "workflow.WorkflowRun",
        on_delete=models.PROTECT,
        related_name="conclusion_document",
        verbose_name="Запуск маршрута",
    )
    submission = models.ForeignKey(
        "submissions.Submission",
        on_delete=models.PROTECT,
        related_name="conclusion_documents",
        verbose_name="Заявка",
    )
    source_version = models.ForeignKey(
        "submissions.SubmissionVersion",
        on_delete=models.PROTECT,
        related_name="conclusion_documents",
        verbose_name="Версия рукописи при формировании",
    )
    registration_number = models.CharField(
        max_length=64,
        unique=True,
        verbose_name="Регистрационный номер",
    )
    document_file = models.FileField(
        upload_to=conclusion_docx_upload_to,
        verbose_name="Подписываемое заключение DOCX",
    )
    document_sha256 = models.CharField(
        max_length=64,
        db_index=True,
        verbose_name="SHA-256 подписываемого заключения",
    )
    package_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    source_pdf_file = models.FileField(
        upload_to=conclusion_package_upload_to,
        blank=True,
        verbose_name="Исходное заключение PDF",
    )
    source_pdf_sha256 = models.CharField(max_length=64, blank=True, editable=False)
    printed_pdf_file = models.FileField(
        upload_to=conclusion_package_upload_to,
        blank=True,
        verbose_name="Печатная форма с подписями PDF",
    )
    printed_pdf_sha256 = models.CharField(max_length=64, blank=True, editable=False)
    signature_data_file = models.FileField(
        upload_to=conclusion_package_upload_to,
        blank=True,
        verbose_name="Данные электронных подписей XML",
    )
    signature_data_sha256 = models.CharField(max_length=64, blank=True, editable=False)
    package_finalized_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
        verbose_name="Комплект файлов сформирован",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Сформировано")
    sealed_at = models.DateTimeField(null=True, blank=True, verbose_name="Зафиксировано")
    is_sealed = models.BooleanField(default=False, editable=False)

    class Meta:
        ordering = ("-created_at", "-id")
        verbose_name = "Заключение"
        verbose_name_plural = "Заключения"

    def __str__(self):
        return self.registration_number

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).first()
            protected_fields = (
                "workflow_run_id",
                "submission_id",
                "source_version_id",
                "registration_number",
                "document_file",
                "document_sha256",
                "package_id",
            )
            if previous and previous.is_sealed and any(
                getattr(previous, field) != getattr(self, field) for field in protected_fields
            ):
                raise ValidationError("Зафиксированное заключение нельзя изменять.")
            package_fields = (
                "package_id",
                "source_pdf_file",
                "source_pdf_sha256",
                "printed_pdf_file",
                "printed_pdf_sha256",
                "signature_data_file",
                "signature_data_sha256",
                "package_finalized_at",
            )
            if previous and previous.package_finalized_at and any(
                getattr(previous, field) != getattr(self, field) for field in package_fields
            ):
                raise ValidationError("Сформированный комплект заключения нельзя изменять.")
        super().save(*args, **kwargs)


class ConclusionSignature(models.Model):
    document = models.ForeignKey(
        ConclusionDocument,
        on_delete=models.PROTECT,
        related_name="signatures",
        verbose_name="Заключение",
    )
    task = models.OneToOneField(
        "workflow.ApprovalTask",
        on_delete=models.PROTECT,
        related_name="conclusion_signature",
        verbose_name="Задача согласования",
    )
    decision = models.OneToOneField(
        "workflow.TaskDecision",
        on_delete=models.PROTECT,
        related_name="conclusion_signature",
        verbose_name="Решение",
    )
    signer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="conclusion_signatures",
        verbose_name="Подписант",
    )
    signer_name = models.CharField(max_length=255, verbose_name="ФИО подписанта")
    signer_role = models.CharField(max_length=255, verbose_name="Роль подписанта")
    submission_version = models.ForeignKey(
        "submissions.SubmissionVersion",
        on_delete=models.PROTECT,
        related_name="conclusion_signatures",
        verbose_name="Одобренная версия рукописи",
    )
    submission_version_number = models.PositiveIntegerField(
        verbose_name="Номер одобренной версии"
    )
    submission_version_sha256 = models.CharField(
        max_length=64,
        verbose_name="SHA-256 одобренной версии",
    )
    document_sha256 = models.CharField(
        max_length=64,
        verbose_name="SHA-256 подписанного заключения",
    )
    operation_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    confirmation_method = models.CharField(
        max_length=64,
        default="authenticated_session",
        editable=False,
        verbose_name="Способ подтверждения",
    )
    client_ip = models.GenericIPAddressField(null=True, blank=True, editable=False)
    user_agent = models.TextField(blank=True, editable=False)
    signed_payload = models.JSONField(editable=False, verbose_name="Зафиксированные данные операции")
    previous_event_hash = models.CharField(max_length=64, blank=True, editable=False)
    event_hash = models.CharField(max_length=64, unique=True, editable=False)
    signed_at = models.DateTimeField(verbose_name="Подписано")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Запись создана")

    class Meta:
        ordering = ("signed_at", "id")
        verbose_name = "Подпись ПЭП заключения"
        verbose_name_plural = "Подписи ПЭП заключений"

    def __str__(self):
        return f"{self.document} / {self.signer_name}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Записи ПЭП неизменяемы.")
        super().save(*args, **kwargs)
