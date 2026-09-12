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

from apps.checks.ai_client import get_api_base_url
from apps.submissions.document_conversion import LegacyDocConversionError, convert_legacy_doc_to_docx
from apps.template_workspace.models import TemplateJob
from apps.template_workspace.services import output_directory
from apps.template_workspace.v2.classification.roles import RoleClassifierV2
from apps.template_workspace.v2.editor.safe_word_editor import SafeWordEditor
from apps.template_workspace.v2.inspector.document import DocumentInspector
from apps.template_workspace.v2.mapping.preview import RoleMatcher
from apps.template_workspace.v2.planning.qwen_provider import build_planning_engine
from apps.template_workspace.v2.profile.template import TemplateProfileBuilder
from apps.template_workspace.v2.word_inputs import prepare_word_file
from apps.template_workspace.v2.readability import run_readability_review, review_summary

logger = logging.getLogger(__name__)


def analysis_directory(job: TemplateJob) -> Path:
    return output_directory(job) / "v2_analysis"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def result_docx_path(job: TemplateJob) -> Path:
    return analysis_directory(job) / "result.docx"


def _working_docx_path(job: TemplateJob, field, label: str) -> tuple[Path, list[str]]:
    source = Path(field.path)
    suffix = source.suffix.casefold()
    if suffix == ".docx":
        return source, []
    if suffix not in {".doc", ".dotx"}:
        raise ValueError(f"{label} должен быть DOCX, DOC или DOTX.")
    converted_directory = analysis_directory(job) / "converted"
    converted_directory.mkdir(parents=True, exist_ok=True)
    converted_path = converted_directory / f"{label.lower()}.docx"
    prepare_word_file(source, converted_path)
    return converted_path, [f"{label}: исходный {suffix.upper()[1:]} подготовлен как рабочая DOCX-копия для V2."]


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
        conversion_warnings: list[str] = []
        source_path, warnings = _working_docx_path(job, job.article, "ARTICLE")
        conversion_warnings.extend(warnings)
        template_path, warnings = _working_docx_path(job, job.template, "TEMPLATE")
        conversion_warnings.extend(warnings)
        source_report = DocumentInspector(source_path).inspect()
        template_report = DocumentInspector(template_path).inspect()
        classifier = RoleClassifierV2(use_ai=False)
        article_structure = classifier.article_structure(source_report)
        template_profile = TemplateProfileBuilder(classifier=classifier).build(template_report)
        mapping_preview = RoleMatcher().build_preview(article_structure, template_profile)
        planner = build_planning_engine()
        editor_result = SafeWordEditor(classifier=classifier, planner=planner).render(
            article_path=source_path,
            template_path=template_path,
            output_path=result_docx_path(job),
            article_report=source_report,
            template_report=template_report,
            article_structure=article_structure,
            template_profile=template_profile,
            mapping_preview=mapping_preview,
        )

        write_json(output / "article_report.json", source_report.to_dict())
        write_json(output / "template_report.json", template_report.to_dict())
        write_json(output / "article_structure.json", article_structure.to_dict())
        write_json(output / "template_profile.json", template_profile.to_dict())
        write_json(output / "mapping_preview.json", mapping_preview.to_dict())
        write_json(
            output / "planning_report.json",
            planner.last_result.to_dict() if planner.last_result else {},
        )
        write_json(output / "editor_report.json", editor_result.to_dict())
        readability = run_readability_review(result_docx_path(job), output)
        plan = [
            {"kind": "DOCX flow", "text": f"ARTICLE: {len(source_report.flow)} блоков; TEMPLATE: {len(template_report.flow)} блоков"},
            {"kind": "V2 роли", "text": f"ARTICLE: {article_structure.provider}; TEMPLATE roles: {len(template_profile.roles)}"},
            {"kind": "Mapping preview", "text": f"{mapping_preview.summary['total_mappings']} действий; review: {mapping_preview.summary['needs_review']}"},
            {"kind": "Планировщик", "text": str(editor_result.metrics.get("planning_provider") or "local")},
            {"kind": "RESULT.docx", "text": "; ".join(editor_result.changes)},
            {"kind": "Секции", "text": f"ARTICLE: {len(source_report.sections)}; TEMPLATE: {len(template_report.sections)}"},
            {"kind": "Таблицы", "text": f"ARTICLE: {len(source_report.tables)}; TEMPLATE: {len(template_report.tables)}"},
            {"kind": "Рисунки", "text": f"ARTICLE: {len(source_report.drawings)}; TEMPLATE: {len(template_report.drawings)}"},
            {"kind": "Формулы", "text": f"ARTICLE: {len(source_report.formulas)}; TEMPLATE: {len(template_report.formulas)}"},
        ]
        warnings = []
        warnings.extend(conversion_warnings)
        warnings.extend(article_structure.warnings)
        warnings.extend(template_profile.warnings)
        warnings.extend(mapping_preview.warnings)
        warnings.extend(editor_result.warnings)
        warnings.extend(readability['warnings'])
        warnings.extend(
            f"Стр. {issue['page']}: {issue['description']} "
            f"({'Qwen, требует проверки' if issue['source'] == 'qwen-vision' else 'геометрический контроль'})"
            for issue in readability['issues']
        )
        plan.append({'kind':'Читаемость', 'text':review_summary(readability)})
        if not getattr(settings, "TEMPLATE_V2_QWEN_ENABLED", False):
            warnings.append("Qwen/VPN: планировщик Template V2 выключен; применён детерминированный локальный план.")
        elif not get_api_base_url(settings.TEMPLATE_V2_QWEN_BASE_URL or None):
            warnings.append("Qwen/VPN: AI_BASE_URL не задан; Template V2 применил детерминированный локальный fallback.")
        else:
            warnings.append(f"Qwen/VPN: планировщик Template V2 настроен через {get_api_base_url(settings.TEMPLATE_V2_QWEN_BASE_URL or None)}; ответ проходит whitelist-валидацию, при сбое используется локальный fallback.")
        TemplateJob.objects.filter(pk=job_id, kind="v2", status="running").update(
            status="completed",
            message="V2 RESULT.docx, отчёты, TemplateProfile и MappingPreview готовы.",
            plan=plan,
            warnings=warnings,
            updated_at=timezone.now(),
        )
    except LegacyDocConversionError as exc:
        logger.exception("Template V2 DOC conversion failed: %s", job_id)
        TemplateJob.objects.filter(pk=job_id, kind="v2", status="running").update(
            status="failed",
            message=f"Не удалось сконвертировать DOC в DOCX для V2. {exc}",
            updated_at=timezone.now(),
        )
    except Exception:
        logger.exception("Template V2 analysis failed: %s", job_id)
        TemplateJob.objects.filter(pk=job_id, kind="v2", status="running").update(
            status="failed",
            message="Не удалось выполнить V2-анализ DOCX. Проверьте, что оба файла являются корректными DOCX.",
            updated_at=timezone.now(),
        )
