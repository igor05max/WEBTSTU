import logging
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from apps.submissions.paper_formatter_ai import QwenSemanticProvider
from paper_formatter.config import SemanticSettings
from paper_formatter.pipeline import ConversionPipeline

from .models import TemplateJob

logger = logging.getLogger(__name__)


def output_directory(job):
    return Path(settings.MEDIA_ROOT) / "template_workspace" / str(job.pk) / "output"


def expire_jobs(owner):
    # A killed worker must not leave the page polling indefinitely.
    TemplateJob.objects.filter(owner=owner, status__in=["queued", "running"],
        updated_at__lt=timezone.now() - timedelta(minutes=20)).update(
        status="failed", message="Обработка прервалась или превысила 20 минут. Создайте новую сборку.")


def launch_job(job):
    kwargs = dict(cwd=str(settings.BASE_DIR), stdin=subprocess.DEVNULL,
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        log_path = output_directory(job).parent / "worker.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab") as log:
            kwargs.update(stdout=log, stderr=log)
            subprocess.Popen([sys.executable, str(settings.BASE_DIR / "manage.py"),
                              "run_template_job", str(job.pk)], **kwargs)
    except OSError:
        logger.exception("Cannot start template job %s", job.pk)
        TemplateJob.objects.filter(pk=job.pk).update(status="failed", message="Не удалось запустить сборку. Повторите загрузку.")


def run_job(job_id):
    if not TemplateJob.objects.filter(pk=job_id, status="queued").update(status="running", updated_at=timezone.now()):
        return
    job = TemplateJob.objects.get(pk=job_id)
    try:
        semantic = SemanticSettings(enabled=bool(settings.AI_BASE_URL), provider="qwen")
        result = ConversionPipeline(semantic_settings=semantic,
            semantic_provider=QwenSemanticProvider(semantic)).run(
            Path(job.article.path), output_directory(job), example=Path(job.template.path),
            compile_pdf=True, render_docx=False)
        plan = []
        metadata = result.article_ir.metadata
        for title in metadata.titles:
            plan.append({"kind": "Название", "text": title.text})
        if metadata.authors:
            plan.append({"kind": "Авторы", "text": ", ".join(a.name for a in metadata.authors)})
        if metadata.affiliations:
            plan.append({"kind": "Организации", "text": "; ".join(a.name for a in metadata.affiliations)})
        for abstract in metadata.abstracts:
            plan.append({"kind": "Аннотация", "text": abstract.text[:300]})
        if metadata.keywords:
            plan.append({"kind": "Ключевые слова", "text": ", ".join(metadata.keywords)})
        for block in result.article_ir.body:
            if block.type == "section":
                plan.append({"kind": "Раздел", "text": block.title})
            elif block.type in {"table", "figure", "equation"}:
                kind = {"table": "Таблица", "figure": "Рисунок", "equation": "Формула"}[block.type]
                plan.append({"kind": kind, "text": getattr(block, "caption", None) or block.id})
        if result.article_ir.references:
            plan.append({"kind": "Литература", "text": f"Источников: {len(result.article_ir.references)}"})
        warnings = list(result.run.warnings)
        errors = list(result.run.errors)
        # Do not expose filesystem paths from parser/compiler diagnostics in the UI.
        diagnostics = [str(w).replace(str(output_directory(job)), "проект") for w in warnings + errors]
        severe_layout_warning = any(
            "существенное переполнение" in warning.lower()
            or "аварийно" in warning.lower()
            for warning in diagnostics
        )
        status = "completed" if result.pdf and not errors and not severe_layout_warning else "partial"
        message = ("Статья собрана из LaTeX. Проверьте предпросмотр перед использованием."
                   if status == "completed" else
                   "LaTeX подготовлен, но сборка требует проверки. Скачайте проект и отчёт.")
        TemplateJob.objects.filter(pk=job_id, status="running").update(
            status=status, message=message, plan=plan, warnings=diagnostics, updated_at=timezone.now())
    except Exception:
        logger.exception("Template conversion failed: %s", job_id)
        TemplateJob.objects.filter(pk=job_id, status="running").update(
            status="failed", message="Не удалось обработать файлы. Проверьте, что статья и шаблон открываются и содержат текст. Для сканов нужен текстовый слой.", updated_at=timezone.now())
