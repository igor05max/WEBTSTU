from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

from django.conf import settings
from django.utils import timezone

from apps.checks.ai_client import get_api_base_url, is_ai_configured
from apps.template_workspace.models import TemplateJob
from apps.template_workspace.services import output_directory
from apps.template_workspace.v2.comparison.document_diff import DocumentDiffBuilder
from apps.template_workspace.v2.inspector.document import DocumentInspector

logger = logging.getLogger(__name__)


def analysis_directory(job: TemplateJob) -> Path:
    return output_directory(job) / "v2_analysis"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def expire_v2_jobs(owner) -> None:
    TemplateJob.objects.filter(
        owner=owner,
        kind="v2",
        status__in=["queued", "running"],
        updated_at__lt=timezone.now() - timedelta(minutes=20),
    ).update(status="failed", message="V2-анализ прервался или превысил 20 минут. Создайте новый отчёт.")


def launch_v2_job(job: TemplateJob) -> None:
    kwargs = dict(cwd=str(settings.BASE_DIR), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        log_path = analysis_directory(job).parent / "worker.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab") as log:
            kwargs.update(stdout=log, stderr=log)
            subprocess.Popen([sys.executable, str(settings.BASE_DIR / "manage.py"), "run_template_v2_job", str(job.pk)], **kwargs)
    except OSError:
        logger.exception("Cannot start template V2 job %s", job.pk)
        TemplateJob.objects.filter(pk=job.pk, kind="v2").update(status="failed", message="Не удалось запустить V2-анализ.")


def run_v2_job(job_id: str) -> None:
    if not TemplateJob.objects.filter(pk=job_id, kind="v2", status="queued").update(status="running", updated_at=timezone.now()):
        return
    job = TemplateJob.objects.get(pk=job_id, kind="v2")
    try:
        output = analysis_directory(job)
        source_report = DocumentInspector(job.article.path, semantic_cache_dir=output / "semantic" / "article").inspect()
        template_report = DocumentInspector(job.template.path, semantic_cache_dir=output / "semantic" / "template").inspect()
        diff = DocumentDiffBuilder().compare(source_report, template_report)

        write_json(output / "article_report.json", source_report.to_dict())
        write_json(output / "template_report.json", template_report.to_dict())
        write_json(output / "document_diff.json", diff.to_dict())
        plan = [
            {"kind": "DOCX flow", "text": f"ARTICLE: {len(source_report.flow)} блоков; TEMPLATE: {len(template_report.flow)} блоков"},
            {"kind": "AI роли", "text": f"ARTICLE: {source_report.semantic_roles.provider}; TEMPLATE: {template_report.semantic_roles.provider}"},
            {"kind": "Секции", "text": f"ARTICLE: {len(source_report.sections)}; TEMPLATE: {len(template_report.sections)}"},
            {"kind": "Таблицы", "text": f"ARTICLE: {len(source_report.tables)}; TEMPLATE: {len(template_report.tables)}"},
            {"kind": "Рисунки", "text": f"ARTICLE: {len(source_report.drawings)}; TEMPLATE: {len(template_report.drawings)}"},
            {"kind": "Формулы", "text": f"ARTICLE: {len(source_report.formulas)}; TEMPLATE: {len(template_report.formulas)}"},
        ]
        warnings = []
        if diff.summary.get("text_hash_changed"):
            warnings.append("CONTENT: нормализованный текст ARTICLE и TEMPLATE различается; V2 пока только анализирует это, не редактирует DOCX.")
        if diff.summary.get("table_structure_changed"):
            warnings.append("TABLES: структура таблиц отличается или имеет другие merges/widths.")
        warnings.extend(source_report.semantic_roles.warnings)
        warnings.extend(template_report.semantic_roles.warnings)
        if not is_ai_configured():
            warnings.append("Qwen/VPN: AI_BASE_URL не задан в окружении, V2 выполнил только локальную классификацию ролей.")
        else:
            warnings.append(f"Qwen/VPN: endpoint настроен ({get_api_base_url()}); роли классифицированы через доступный провайдер или fallback.")
        TemplateJob.objects.filter(pk=job_id, kind="v2", status="running").update(
            status="completed",
            message="V2 Word-forensics отчёты готовы. Документы не изменялись.",
            plan=plan,
            warnings=warnings,
            updated_at=timezone.now(),
        )
    except Exception:
        logger.exception("Template V2 analysis failed: %s", job_id)
        TemplateJob.objects.filter(pk=job_id, kind="v2", status="running").update(
            status="failed",
            message="Не удалось выполнить V2-анализ DOCX. Проверьте, что оба файла являются корректными DOCX.",
            updated_at=timezone.now(),
        )
