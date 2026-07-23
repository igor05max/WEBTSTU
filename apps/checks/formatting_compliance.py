from collections import Counter
from pathlib import Path

from apps.submissions.document_analysis import read_file_bytes
from document_template_engine import (
    DocumentTemplateEngineError,
    check_docx_against_template,
    normalize_template_rules,
)


def _issue(code, title, message, *, severity="info", suggestion="", fixable=False):
    return {
        "code": code,
        "title": title,
        "severity": severity,
        "message": message,
        "location": "Оформление по шаблону",
        "context": "",
        "context_before": "",
        "context_highlight": "",
        "context_after": "",
        "suggestion": suggestion,
        "fixable": fixable,
    }


def _summary(issues):
    counts = Counter(item["severity"] for item in issues)
    return {
        "info": counts.get("info", 0),
        "warning": counts.get("warning", 0),
        "error": counts.get("error", 0),
        "critical": counts.get("critical", 0),
        "total": len(issues),
    }


def _submission_metadata(submission):
    return {
        "title": submission.title,
        "authors": submission.document_authors or submission.get_authors_display(),
        "organizations": submission.organizations,
        "abstract": submission.abstract,
        "keywords": submission.keywords,
    }


def _payload(submission, message, issues, *, report=None, execution_status=""):
    report = report or {}
    payload = {
        "schema_version": "2.0",
        "check_code": "formatting_compliance",
        "message": message,
        "summary": _summary(issues),
        "issues": issues,
        "metrics": report.get("metrics") or {},
        "extracted_metadata": {},
        "details": {
            "template_id": submission.formatting_template_id,
            "template_version": (
                submission.formatting_template.version_number
                if submission.formatting_template_id
                else None
            ),
            "rule_sources": (submission.formatting_rules_snapshot or {}).get("sources") or [],
            "rule_conflicts": (submission.formatting_rules_snapshot or {}).get("conflicts") or [],
            "document_blocks": report.get("blocks") or [],
            "role_assignments": report.get("role_assignments") or [],
            "content_policy": report.get("content_policy") or {},
            "can_generate_corrected_document": bool(report.get("can_build")),
            "engine": "document_template_engine",
        },
    }
    if execution_status:
        payload["execution_status"] = execution_status
    return payload


def build_formatting_compliance_report(submission, version):
    if not submission.formatting_check_requested:
        return True, _payload(
            submission,
            "Автор отключил необязательную проверку оформления.",
            [],
            execution_status="not_performed",
        )
    if submission.formatting_template_id is None:
        return True, _payload(
            submission,
            "Шаблон оформления не приложен. Остальные автоматические проверки выполнены.",
            [],
            execution_status="not_performed",
        )

    rules = normalize_template_rules(
        (submission.formatting_rules_snapshot or {}).get("effective") or {}
    )
    if not rules:
        return True, _payload(
            submission,
            "Из шаблона пока не удалось извлечь проверяемые правила.",
            [],
            execution_status="partial",
        )

    suffix = Path(version.file.name).suffix.casefold()
    if suffix != ".docx":
        issues = [
            _issue(
                "template_limited_format_analysis",
                "Для точной сборки нужен DOCX",
                "Поля, шрифты, интервалы и роли абзацев можно надёжно проверить и исправить только в DOCX.",
                suggestion="При необходимости загрузите работу в формате DOCX.",
            )
        ]
        return True, _payload(
            submission,
            "Проверка по шаблону выполнена частично: исходный файл не DOCX.",
            issues,
            execution_status="partial",
        )

    with version.file.open("rb") as source:
        data = read_file_bytes(source)
    try:
        report = check_docx_against_template(
            data,
            rules,
            metadata=_submission_metadata(submission),
        )
    except DocumentTemplateEngineError as exc:
        issues = [
            _issue(
                "template_docx_open_failed",
                "Не удалось разобрать DOCX",
                str(exc),
                suggestion="Проверьте, что файл открывается в текстовом редакторе, и загрузите новую версию.",
            )
        ]
        return True, _payload(
            submission,
            "Проверка по шаблону выполнена частично.",
            issues,
            execution_status="partial",
        )

    issues = report["issues"]
    message = (
        "Структура блоков и оформление соответствуют извлечённым правилам шаблона."
        if not issues
        else f"По шаблону найдено замечаний: {len(issues)}."
    )
    return not any(item["severity"] in {"error", "critical"} for item in issues), _payload(
        submission,
        message,
        issues,
        report=report,
    )
