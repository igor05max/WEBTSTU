import os
import logging
import subprocess
import sys
from contextlib import contextmanager
from datetime import timedelta
from threading import Event, Thread
from uuid import uuid4

from django.conf import settings
from django.db import close_old_connections, transaction
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
TERMINAL_CHECK_RUN_STATUSES = frozenset(
    {
        CheckRunStatus.PASSED,
        CheckRunStatus.FAILED,
        CheckRunStatus.PARTIAL,
        CheckRunStatus.NOT_PERFORMED,
    }
)
CHECK_RUN_HEARTBEAT_SECONDS = 30

DEFAULT_CHECK_DEFINITIONS = (
    {
        "code": "mock_content_screening",
        "name": "Содержание и риски",
        "description": "AI-модель оценивает связность, адекватность и признаки опасного содержания без проверки плагиата.",
        "order": 10,
        "is_blocking": False,
        "backend_code": "local_ai_content_review",
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


def ensure_submission_checks(
    submission,
    *,
    version=None,
    definitions=None,
):
    """Create only missing active check rows and preserve existing results."""

    submission.refresh_from_db()
    version = version or submission.current_version
    if version is None:
        raise ValueError("Submission must have a current version before checks.")
    if (
        version.submission_id != submission.pk
        or submission.current_version_id != version.id
    ):
        raise ValueError("Check version must be the submission's current version.")

    if definitions is None:
        definitions = get_active_check_definitions()
    else:
        definitions = list(definitions)

    with transaction.atomic():
        for definition in definitions:
            CheckRun.objects.get_or_create(
                submission=submission,
                version=version,
                check_definition=definition,
                defaults={
                    "status": CheckRunStatus.PENDING,
                    "result_payload": {},
                },
            )

    definition_ids = [definition.id for definition in definitions]
    runs_by_definition_id = {
        run.check_definition_id: run
        for run in CheckRun.objects.filter(
            submission=submission,
            version=version,
            check_definition_id__in=definition_ids,
        ).select_related("check_definition")
    }
    return [runs_by_definition_id[definition.id] for definition in definitions]


def prepare_submission_checks(submission, *, version=None):
    """Reset active checks for an explicit new run without replacing rows."""

    submission.refresh_from_db()
    version = version or submission.current_version
    if version is None:
        raise ValueError("Submission must have a current version before checks.")
    if (
        version.submission_id != submission.pk
        or submission.current_version_id != version.id
    ):
        raise ValueError("Check version must be the submission's current version.")

    definitions = get_active_check_definitions()
    now = timezone.now()
    definition_ids = [definition.id for definition in definitions]
    with transaction.atomic():
        locked_submission = Submission.objects.select_for_update().get(
            pk=submission.pk,
        )
        if locked_submission.current_version_id != version.id:
            raise ValueError(
                "Check version must be the submission's current version."
            )
        locked_submission.status = SubmissionStatus.AUTO_CHECKING
        locked_submission.updated_at = now
        locked_submission.save(
            update_fields=["status", "updated_at"],
        )
        CheckRun.objects.filter(
            submission=submission,
            version=version,
            check_definition_id__in=definition_ids,
        ).update(
            status=CheckRunStatus.PENDING,
            result_payload={},
            started_at=None,
            finished_at=None,
            claim_token=None,
            heartbeat_at=None,
        )
        for definition in definitions:
            CheckRun.objects.get_or_create(
                submission=submission,
                version=version,
                check_definition=definition,
                defaults={
                    "status": CheckRunStatus.PENDING,
                    "result_payload": {},
                },
            )

    submission.status = SubmissionStatus.AUTO_CHECKING
    submission.updated_at = now
    runs_by_definition_id = {
        run.check_definition_id: run
        for run in CheckRun.objects.filter(
            submission=submission,
            version=version,
            check_definition_id__in=definition_ids,
        ).select_related("check_definition")
    }
    return [runs_by_definition_id[definition.id] for definition in definitions]


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
    pending_grace_seconds=300,
    processing_template_grace_seconds=600,
    submission_ids=None,
):
    """Resume checks only after their recorded activity has gone stale.

    Existing terminal results are preserved.  A stale running row is returned
    to pending with a claim-token compare-and-swap, so a superseded worker
    cannot later overwrite the recovered result.
    """
    now = timezone.now()
    definitions = get_active_check_definitions()
    definition_ids = [definition.id for definition in definitions]
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
                check_definition_id__in=definition_ids,
            ).select_related("check_definition")
        )
        active_runs = runs
        unfinished_runs = [
            run
            for run in active_runs
            if run.status in {
                CheckRunStatus.PENDING,
                CheckRunStatus.RUNNING,
            }
        ]
        checks_not_started = not active_runs or all(
            run.status == CheckRunStatus.PENDING
            and run.started_at is None
            and run.finished_at is None
            for run in active_runs
        )
        template_is_processing = bool(
            submission.formatting_template_id
            and submission.formatting_template.analysis_status == "processing"
        )
        if not active_runs:
            grace_seconds = (
                processing_template_grace_seconds
                if template_is_processing and checks_not_started
                else no_run_grace_seconds
            )
            stalled_before = now - timedelta(seconds=grace_seconds)
            if submission.updated_at > stalled_before:
                reason = (
                    "template_processing"
                    if template_is_processing
                    else "grace_period"
                )
                skipped.append((submission.id, reason))
                continue
        else:
            progress_times = [submission.updated_at]
            for run in active_runs:
                progress_times.append(run.created_at)
                if run.started_at is not None:
                    progress_times.append(run.started_at)
                if run.heartbeat_at is not None:
                    progress_times.append(run.heartbeat_at)
                if run.finished_at is not None:
                    progress_times.append(run.finished_at)
            last_progress_at = max(progress_times)
            grace_seconds = (
                processing_template_grace_seconds
                if template_is_processing and checks_not_started
                else pending_grace_seconds
            )
            stalled_before = now - timedelta(seconds=grace_seconds)
            if last_progress_at > stalled_before:
                if template_is_processing and checks_not_started:
                    reason = "template_processing"
                elif not unfinished_runs:
                    reason = "finishing_worker"
                else:
                    reason = (
                        "running_worker"
                        if any(
                            run.status == CheckRunStatus.RUNNING
                            for run in unfinished_runs
                        )
                        else "pending_worker"
                    )
                skipped.append((submission.id, reason))
                continue

            running_runs = [
                run
                for run in unfinished_runs
                if run.status == CheckRunStatus.RUNNING
            ]
            worker_progress_race = False
            with transaction.atomic():
                for run in running_runs:
                    filters = {
                        "pk": run.pk,
                        "status": CheckRunStatus.RUNNING,
                        "claim_token": run.claim_token,
                        "started_at": run.started_at,
                        "heartbeat_at": run.heartbeat_at,
                    }
                    if run.heartbeat_at is not None:
                        filters["heartbeat_at__lte"] = stalled_before
                    elif run.started_at is None:
                        filters["created_at"] = run.created_at
                        filters["created_at__lte"] = stalled_before
                    else:
                        filters["started_at__lte"] = stalled_before
                    updated = CheckRun.objects.filter(**filters).update(
                        status=CheckRunStatus.PENDING,
                        result_payload={},
                        started_at=None,
                        finished_at=None,
                        claim_token=None,
                        heartbeat_at=None,
                    )
                    if updated != 1:
                        worker_progress_race = True
                        transaction.set_rollback(True)
                        break
            if worker_progress_race:
                skipped.append((submission.id, "worker_progress_race"))
                continue

        try:
            current_runs = ensure_submission_checks(
                submission,
                version=version,
                definitions=definitions,
            )
        except ValueError:
            skipped.append((submission.id, "version_changed"))
            continue

        if submission.formatting_template_id and any(
            run.status not in TERMINAL_CHECK_RUN_STATUSES
            for run in current_runs
        ):
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


@contextmanager
def _maintain_check_run_heartbeat(
    run_id,
    claim_token,
    *,
    interval_seconds=None,
):
    if interval_seconds is None:
        interval_seconds = CHECK_RUN_HEARTBEAT_SECONDS
    stop = Event()

    def heartbeat():
        close_old_connections()
        try:
            while not stop.wait(interval_seconds):
                try:
                    updated = CheckRun.objects.filter(
                        pk=run_id,
                        status=CheckRunStatus.RUNNING,
                        claim_token=claim_token,
                    ).update(heartbeat_at=timezone.now())
                except Exception:
                    logger.exception(
                        "Failed to update heartbeat for check run %s.",
                        run_id,
                    )
                    close_old_connections()
                    continue
                if updated != 1:
                    break
        finally:
            close_old_connections()

    thread = Thread(
        target=heartbeat,
        name=f"check-run-heartbeat-{run_id}",
        daemon=True,
    )
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1)


def _technical_check_error_payload(definition, exc):
    return {
        "schema_version": "1.0",
        "check_code": definition.code,
        "message": "Проверка завершилась технической ошибкой. Это не блокирует отправку.",
        "summary": {
            "info": 0,
            "warning": 1,
            "error": 0,
            "critical": 0,
            "total": 1,
        },
        "issues": [
            {
                "code": "technical_error",
                "title": "Техническая ошибка проверки",
                "severity": "warning",
                "message": (
                    "Автоматическая проверка не завершилась; эксперт может "
                    "проверить материал вручную."
                ),
                "location": "Система проверок",
                "context": "",
                "context_before": "",
                "context_highlight": "",
                "context_after": "",
                "suggestion": (
                    "Повторите проверку после устранения технической причины."
                ),
            }
        ],
        "metrics": {},
        "extracted_metadata": {},
        "details": {"error_type": type(exc).__name__},
        "execution_status": "not_performed",
    }


def _finalize_submission_checks_if_complete(
    submission,
    version,
    definitions,
    *,
    resume_workflow_after_success=False,
):
    definition_ids = [definition.id for definition in definitions]
    with transaction.atomic():
        locked_submission = Submission.objects.select_for_update().get(
            pk=submission.pk,
        )
        if locked_submission.current_version_id != version.id:
            return False
        statuses = list(
            CheckRun.objects.filter(
                submission_id=locked_submission.pk,
                version=version,
                check_definition_id__in=definition_ids,
            ).values_list("status", flat=True)
        )
        if len(statuses) != len(definition_ids) or any(
            status not in TERMINAL_CHECK_RUN_STATUSES
            for status in statuses
        ):
            return False
        transitioned = (
            locked_submission.status == SubmissionStatus.AUTO_CHECKING
        )
        if transitioned:
            locked_submission.status = SubmissionStatus.SUBMITTED
            locked_submission.save(
                update_fields=["status", "updated_at"],
            )
        elif locked_submission.status != SubmissionStatus.SUBMITTED:
            return False

    submission.refresh_from_db()

    ensure_submission_route_suggestion(submission)
    if resume_workflow_after_success and transitioned:
        from apps.workflow.services import resume_or_start_workflow

        submission.refresh_from_db()
        if submission.current_version_id == version.id:
            resume_or_start_workflow(submission)
    return True


def run_mock_checks(
    submission,
    *,
    expected_version_id=None,
    resume_workflow_after_success=False,
):
    submission.refresh_from_db()
    version = submission.current_version
    if version is None:
        raise ValueError("Submission must have a current version before checks.")

    if expected_version_id is not None and version.id != expected_version_id:
        return False

    definitions = get_active_check_definitions()
    try:
        ensure_submission_checks(
            submission,
            version=version,
            definitions=definitions,
        )
    except ValueError:
        return False
    snapshot = None
    snapshot_loaded = False
    for definition in definitions:
        submission.refresh_from_db(fields=["current_version"])
        if submission.current_version_id != version.id:
            return False

        run = CheckRun.objects.filter(
            submission=submission,
            version=version,
            check_definition=definition,
        ).first()
        if run is None:
            try:
                ensure_submission_checks(
                    submission,
                    version=version,
                    definitions=[definition],
                )
            except ValueError:
                return False
            run = CheckRun.objects.get(
                submission=submission,
                version=version,
                check_definition=definition,
            )
        if run.status in TERMINAL_CHECK_RUN_STATUSES:
            continue
        if (
            run.status != CheckRunStatus.PENDING
            or run.claim_token is not None
            or run.heartbeat_at is not None
        ):
            return False

        claim_token = uuid4()
        started_at = timezone.now()
        claimed = CheckRun.objects.filter(
            pk=run.pk,
            status=CheckRunStatus.PENDING,
            claim_token__isnull=True,
            heartbeat_at__isnull=True,
        ).update(
            status=CheckRunStatus.RUNNING,
            result_payload={},
            started_at=started_at,
            finished_at=None,
            claim_token=claim_token,
            heartbeat_at=started_at,
        )
        if claimed != 1:
            latest_status = CheckRun.objects.filter(
                pk=run.pk,
            ).values_list("status", flat=True).first()
            if latest_status in TERMINAL_CHECK_RUN_STATUSES:
                continue
            return False

        with _maintain_check_run_heartbeat(run.pk, claim_token):
            try:
                if not snapshot_loaded:
                    snapshot = build_snapshot(version)
                    snapshot_loaded = True
                passed, payload = _evaluate_check(
                    definition,
                    submission,
                    version,
                    snapshot=snapshot,
                )
                if not isinstance(payload, dict):
                    raise TypeError("Check payload must be a dictionary.")
                execution_status = str(
                    payload.get("execution_status") or ""
                ).strip()
                if execution_status == "not_performed":
                    terminal_status = CheckRunStatus.NOT_PERFORMED
                elif execution_status == "partial":
                    terminal_status = CheckRunStatus.PARTIAL
                else:
                    terminal_status = (
                        CheckRunStatus.PASSED
                        if passed
                        else CheckRunStatus.FAILED
                    )
            except Exception as exc:
                logger.exception(
                    "Submission check %s failed",
                    definition.code,
                )
                payload = _technical_check_error_payload(definition, exc)
                terminal_status = CheckRunStatus.NOT_PERFORMED

        finished = CheckRun.objects.filter(
            pk=run.pk,
            status=CheckRunStatus.RUNNING,
            claim_token=claim_token,
        ).update(
            status=terminal_status,
            result_payload=payload,
            finished_at=timezone.now(),
            claim_token=None,
            heartbeat_at=None,
        )
        if finished != 1:
            return False

    return _finalize_submission_checks_if_complete(
        submission,
        version,
        definitions,
        resume_workflow_after_success=resume_workflow_after_success,
    )


def run_submission_checks_by_id(submission_id, *, version_id, resume_workflow_after_success=False):
    submission = Submission.objects.get(pk=submission_id)
    return run_mock_checks(
        submission,
        expected_version_id=version_id,
        resume_workflow_after_success=resume_workflow_after_success,
    )
