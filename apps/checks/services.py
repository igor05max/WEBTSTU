import os
import logging
import subprocess
import sys
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.checks.models import CheckDefinition, CheckRun, CheckRunStatus
from apps.checks.content_review import build_content_review_report
from apps.checks.document_checks import (
    build_document_quality_report,
    build_file_safety_report,
    build_snapshot,
)
from apps.checks.formatting_compliance import build_formatting_compliance_report
from apps.submissions.route_suggestions import ensure_submission_route_suggestion, get_selectable_directions_queryset
from apps.submissions.models import Submission, SubmissionStatus
from apps.submissions.subject_area import detect_direction_for_submission

RETIRED_CHECK_CODES = frozenset({"article_recommendations"})

DEFAULT_CHECK_DEFINITIONS = (
    {
        "code": "mock_content_screening",
        "name": "Содержание и риски",
        "description": "AI-модель оценивает связность, адекватность и признаки опасного содержания без проверки плагиата.",
        "order": 10,
        "is_blocking": False,
        "backend_code": "gemini_content_review",
    },
    {
        "code": "metadata_complete",
        "name": "Целостность документа",
        "description": "Проверяет внутренние ссылки, подписи, нумерацию и идентификаторы без навязывания универсальной структуры.",
        "order": 20,
        "is_blocking": False,
        "backend_code": "document_quality",
    },
    {
        "code": "file_uploaded",
        "name": "Формат и безопасность файла",
        "description": "Проверяет формат, размер, контейнер DOCX, макросы, вложения и опасные внешние связи.",
        "order": 30,
        "is_blocking": False,
        "backend_code": "file_safety",
    },
    {
        "code": "formatting_compliance",
        "name": "Оформление по шаблону",
        "description": "Сравнивает документ с выбранным шаблоном журнала, темы или события.",
        "order": 25,
        "is_blocking": False,
        "backend_code": "formatting_compliance",
    },
    {
        "code": "subject_area_detection",
        "name": "Определение области",
        "description": "Автоматически определяет предметную область материала для подбора маршрута согласования.",
        "order": 35,
        "is_blocking": False,
        "backend_code": "subject_area_detection",
    },
)

logger = logging.getLogger(__name__)


def ensure_default_check_definitions():
    definitions = []
    for item in DEFAULT_CHECK_DEFINITIONS:
        definition, _ = CheckDefinition.objects.update_or_create(
            code=item["code"],
            defaults={
                "name": item["name"],
                "description": item["description"],
                "order": item["order"],
                "is_blocking": item["is_blocking"],
                "backend_code": item["backend_code"],
                "is_active": True,
            },
        )
        definitions.append(definition)
    CheckDefinition.objects.filter(code__in=RETIRED_CHECK_CODES).update(is_active=False)
    return definitions


def get_active_check_definitions():
    ensure_default_check_definitions()
    return list(
        CheckDefinition.objects.filter(is_active=True)
        .exclude(code__in=RETIRED_CHECK_CODES)
        .order_by("order", "id")
    )


def _evaluate_check(check_definition, submission, version, *, snapshot=None):
    if check_definition.code == "subject_area_detection":
        payload = detect_direction_for_submission(
            submission,
            directions=get_selectable_directions_queryset(article_type=submission.article_type),
        )
        return bool(payload.get("matched")), payload

    if check_definition.code == "file_uploaded":
        return build_file_safety_report(submission, version, snapshot=snapshot)

    if check_definition.code == "formatting_compliance":
        return build_formatting_compliance_report(submission, version)

    if check_definition.code == "metadata_complete":
        return build_document_quality_report(submission, version, snapshot=snapshot)

    if check_definition.code == "mock_content_screening":
        return build_content_review_report(submission, snapshot or build_snapshot(version))

    return True, {
        "schema_version": "1.0",
        "check_code": check_definition.code,
        "message": "Для проверки не настроен обработчик; отправка не блокируется.",
        "summary": {"info": 1, "warning": 0, "error": 0, "critical": 0, "total": 1},
        "issues": [
            {
                "code": "handler_not_configured",
                "title": "Обработчик не настроен",
                "severity": "info",
                "message": "Проверка сохранена как информационная.",
                "location": "Система проверок",
                "context": "",
                "context_before": "",
                "context_highlight": "",
                "context_after": "",
                "suggestion": "",
            }
        ],
        "metrics": {},
        "extracted_metadata": {},
        "details": {},
    }


def prepare_submission_checks(submission, *, version=None):
    submission.refresh_from_db()
    version = version or submission.current_version
    if version is None:
        raise ValueError("Submission must have a current version before checks.")

    submission.status = SubmissionStatus.AUTO_CHECKING
    submission.save(update_fields=["status", "updated_at"])

    definitions = get_active_check_definitions()
    CheckRun.objects.filter(
        submission=submission,
        version=version,
    ).delete()
    check_runs = []
    now = timezone.now()
    for definition in definitions:
        check_runs.append(
            CheckRun.objects.create(
                submission=submission,
                version=version,
                check_definition=definition,
                status=CheckRunStatus.PENDING,
                created_at=now,
            )
        )
    return check_runs


def launch_submission_checks_process(submission_id, version_id, resume_workflow_after_success):
    command = [
        sys.executable,
        str(settings.BASE_DIR / "manage.py"),
        "run_submission_checks",
        str(submission_id),
        "--version-id",
        str(version_id),
    ]
    if resume_workflow_after_success:
        command.append("--resume-workflow-after-success")

    popen_kwargs = {
        "cwd": str(settings.BASE_DIR),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        "env": os.environ.copy(),
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    else:
        popen_kwargs["start_new_session"] = True

    subprocess.Popen(command, **popen_kwargs)


def queue_submission_checks(submission, *, resume_workflow_after_success=False):
    submission.refresh_from_db()
    version = submission.current_version
    if version is None:
        raise ValueError("Submission must have a current version before checks.")

    prepare_submission_checks(submission, version=version)

    if not settings.SUBMISSION_CHECKS_ASYNC:
        run_mock_checks(
            submission,
            expected_version_id=version.id,
            resume_workflow_after_success=resume_workflow_after_success,
        )
        return

    from django.db import transaction

    transaction.on_commit(
        lambda: launch_submission_checks_process(
            submission.id,
            version.id,
            resume_workflow_after_success,
        )
    )


def recover_stalled_submission_checks(
    *,
    no_run_grace_seconds=90,
    pending_grace_seconds=600,
    submission_ids=None,
):
    """Synchronously recover auto-checking submissions whose worker never started.

    The normal path remains asynchronous.  This recovery path is intended for a
    periodic watchdog and deliberately handles only two unambiguous states:
    there are no runs at all, or every run is still pending long after dispatch.
    A currently running check is never interrupted.
    """
    now = timezone.now()
    queryset = Submission.objects.filter(
        status=SubmissionStatus.AUTO_CHECKING,
        current_version__isnull=False,
    ).select_related(
        "current_version",
        "formatting_template",
    )
    if submission_ids is not None:
        queryset = queryset.filter(pk__in=submission_ids)

    recovered = []
    skipped = []
    for submission in queryset.order_by("updated_at", "pk"):
        version = submission.current_version
        runs = list(
            CheckRun.objects.filter(
                submission=submission,
                version=version,
            ).select_related("check_definition")
        )
        active_runs = [
            run
            for run in runs
            if run.check_definition.code not in RETIRED_CHECK_CODES
        ]
        if any(
            run.status in {
                CheckRunStatus.RUNNING,
                CheckRunStatus.PASSED,
                CheckRunStatus.FAILED,
                CheckRunStatus.PARTIAL,
                CheckRunStatus.NOT_PERFORMED,
            }
            for run in active_runs
        ):
            skipped.append((submission.id, "active_or_finished"))
            continue

        if not active_runs:
            stalled_before = now - timedelta(seconds=no_run_grace_seconds)
            if submission.updated_at > stalled_before:
                skipped.append((submission.id, "grace_period"))
                continue
        else:
            oldest_pending_at = min(run.created_at for run in active_runs)
            stalled_before = now - timedelta(seconds=pending_grace_seconds)
            if oldest_pending_at > stalled_before:
                skipped.append((submission.id, "pending_worker"))
                continue

        # Create pending rows first.  They serve as a durable claim, so a
        # concurrent retry sees that this submission is already being recovered.
        prepare_submission_checks(submission, version=version)

        if submission.formatting_template_id:
            from apps.submissions.template_processing import (
                prepare_submission_template_by_id,
            )

            prepare_submission_template_by_id(
                submission.id,
                template_id=submission.formatting_template_id,
                expected_version_id=version.id,
                start_checks=False,
            )

        completed = run_mock_checks(
            submission,
            expected_version_id=version.id,
            resume_workflow_after_success=False,
        )
        recovered.append((submission.id, bool(completed)))

    return {
        "recovered": recovered,
        "skipped": skipped,
    }


def run_mock_checks(submission, *, expected_version_id=None, resume_workflow_after_success=False):
    submission.refresh_from_db()
    version = submission.current_version
    if version is None:
        raise ValueError("Submission must have a current version before checks.")

    if expected_version_id is not None and version.id != expected_version_id:
        return False

    definitions = get_active_check_definitions()
    runs_by_definition_id = {
        run.check_definition_id: run
        for run in CheckRun.objects.filter(
            submission=submission,
            version=version,
        ).select_related("check_definition")
    }

    if len(runs_by_definition_id) != len(definitions):
        prepare_submission_checks(submission, version=version)
        runs_by_definition_id = {
            run.check_definition_id: run
            for run in CheckRun.objects.filter(
                submission=submission,
                version=version,
            ).select_related("check_definition")
        }

    snapshot = build_snapshot(version)
    for definition in definitions:
        submission.refresh_from_db(fields=["current_version"])
        if submission.current_version_id != version.id:
            return False

        started_at = timezone.now()
        run = runs_by_definition_id[definition.id]
        run.status = CheckRunStatus.RUNNING
        run.started_at = started_at
        run.finished_at = None
        run.save(update_fields=["status", "started_at", "finished_at"])
        try:
            passed, payload = _evaluate_check(
                definition,
                submission,
                version,
                snapshot=snapshot,
            )
        except Exception as exc:
            logger.exception("Submission check %s failed", definition.code)
            passed = False
            payload = {
                "schema_version": "1.0",
                "check_code": definition.code,
                "message": "Проверка завершилась технической ошибкой. Это не блокирует отправку.",
                "summary": {"info": 0, "warning": 1, "error": 0, "critical": 0, "total": 1},
                "issues": [
                    {
                        "code": "technical_error",
                        "title": "Техническая ошибка проверки",
                        "severity": "warning",
                        "message": "Автоматическая проверка не завершилась; эксперт может проверить материал вручную.",
                        "location": "Система проверок",
                        "context": "",
                        "context_before": "",
                        "context_highlight": "",
                        "context_after": "",
                        "suggestion": "Повторите проверку после устранения технической причины.",
                    }
                ],
                "metrics": {},
                "extracted_metadata": {},
                "details": {"error_type": type(exc).__name__},
                "execution_status": "not_performed",
            }
        execution_status = str(payload.get("execution_status") or "").strip()
        if execution_status == "not_performed":
            run.status = CheckRunStatus.NOT_PERFORMED
        elif execution_status == "partial":
            run.status = CheckRunStatus.PARTIAL
        else:
            run.status = CheckRunStatus.PASSED if passed else CheckRunStatus.FAILED
        run.result_payload = payload
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "result_payload", "finished_at"])

    submission.refresh_from_db(fields=["current_version"])
    if submission.current_version_id != version.id:
        return False

    submission.status = SubmissionStatus.SUBMITTED
    submission.save(update_fields=["status", "updated_at"])
    ensure_submission_route_suggestion(submission)
    if resume_workflow_after_success:
        from apps.workflow.services import resume_or_start_workflow

        submission.refresh_from_db()
        if submission.current_version_id == version.id:
            resume_or_start_workflow(submission)
    return True


def run_submission_checks_by_id(submission_id, *, version_id, resume_workflow_after_success=False):
    submission = Submission.objects.get(pk=submission_id)
    return run_mock_checks(
        submission,
        expected_version_id=version_id,
        resume_workflow_after_success=resume_workflow_after_success,
    )
