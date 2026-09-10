import uuid

from django.conf import settings
from django.db import models


def job_upload(instance, filename):
    return f"template_workspace/{instance.id}/uploads/{uuid.uuid4().hex}/{filename}"


class TemplateJob(models.Model):
    KIND_CHOICES = [("v1", "Legacy template workspace"), ("v2", "Word-first forensics")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default="v1")
    article = models.FileField(upload_to=job_upload, max_length=500)
    template = models.FileField(upload_to=job_upload, max_length=500)
    article_name = models.CharField(max_length=255)
    template_name = models.CharField(max_length=255)
    status = models.CharField(max_length=20, default="queued")
    message = models.TextField(blank=True)
    plan = models.JSONField(default=list)
    warnings = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def pending(self):
        return self.status in {"queued", "running"}

