from django.db import models, transaction
from django.utils import timezone

from apps.directory.models import Direction
from apps.submissions.models import Submission, SubmissionStatus, SubmissionVersion
from apps.submissions.route_suggestions import ensure_submission_route_suggestion, is_route_template_selectable
from apps.workflow.models import RouteTemplate, WorkflowRunStatus


@transaction.atomic
def add_submission_version(
    submission,
    uploaded_by,
    file,
    comment="",
    *,
    expected_current_version_id=None,
):
    submission = Submission.objects.select_for_update().get(pk=submission.pk)

    if uploaded_by != submission.author and not uploaded_by.is_superuser:
        raise PermissionError("Only the author can upload a new version.")

    if (
        expected_current_version_id is not None
        and submission.current_version_id != expected_current_version_id
    ):
        raise ValueError(
            "Текущая версия материала уже изменилась. Откройте исправленный документ заново."
        )

    was_revision_requested = submission.status == SubmissionStatus.REVISION_REQUESTED
    allowed_statuses = {
        SubmissionStatus.DRAFT,
        SubmissionStatus.SUBMITTED,
        SubmissionStatus.REVISION_REQUESTED,
    }
    if submission.status not in allowed_statuses:
        raise ValueError("A new version can only be uploaded before workflow launch or after revision request.")

    next_version_number = (
        submission.versions.aggregate(max_number=models.Max("version_number"))["max_number"] or 0
    ) + 1
    version = SubmissionVersion.objects.create(
        submission=submission,
        version_number=next_version_number,
        file=file,
        uploaded_by=uploaded_by,
        comment=comment or "",
    )
    submission.current_version = version
    submission.save(update_fields=["current_version", "updated_at"])
    from apps.checks.services import queue_submission_checks

    queue_submission_checks(
        submission,
        resume_workflow_after_success=(
            was_revision_requested
            and submission.direction_id is not None
            and submission.route_template_id is not None
        ),
    )

    return version


@transaction.atomic
def create_submission_with_initial_version(
    *,
    author,
    title,
    abstract,
    journal,
    article_type,
    file,
    publication_topic=None,
    formatting_template=None,
    formatting_rules_snapshot=None,
    formatting_check_requested=True,
    comment="",
    co_authors=None,
    document_authors="",
    organizations="",
    contact_emails="",
    keywords="",
    defer_checks=False,
    mark_as_checking=True,
):
    submission = Submission.objects.create(
        title=title or "Без названия",
        abstract=abstract,
        document_authors=document_authors or "",
        organizations=organizations or "",
        contact_emails=contact_emails or "",
        keywords=keywords or "",
        author=author,
        journal=journal,
        publication_topic=publication_topic,
        article_type=article_type,
        formatting_template=formatting_template,
        formatting_rules_snapshot=formatting_rules_snapshot or {},
        formatting_check_requested=bool(formatting_check_requested),
        status=SubmissionStatus.DRAFT,
    )
    author_ids = {author.id}
    for co_author in co_authors or []:
        if co_author is None or co_author.id is None:
            continue
        author_ids.add(co_author.id)
    if author_ids:
        submission.authors.set(author_ids)

    version = SubmissionVersion.objects.create(
        submission=submission,
        version_number=1,
        file=file,
        uploaded_by=author,
        comment=comment or "",
    )
    submission.current_version = version
    submission.save(update_fields=["current_version", "updated_at"])
    if publication_topic is not None:
        publication_topic.last_used_at = timezone.now()
        publication_topic.save(update_fields=["last_used_at", "updated_at"])
    if defer_checks and mark_as_checking:
        submission.status = SubmissionStatus.AUTO_CHECKING
        submission.save(update_fields=["status", "updated_at"])
    elif not defer_checks:
        from apps.checks.services import queue_submission_checks

        queue_submission_checks(submission)
    return submission


def _validate_submission_route_selection(submission, *, direction: Direction, route_template: RouteTemplate):
    if route_template is None:
        raise ValueError("Перед отправкой нужно выбрать маршрут согласования.")

    if not route_template.is_active:
        raise ValueError("Выбранный маршрут больше не активен.")

    if direction is None:
        direction = route_template.direction

    if direction is None:
        raise ValueError("Перед отправкой нужно выбрать область экспертизы.")

    if not direction.is_active:
        raise ValueError("Выбранная область экспертизы больше не активна.")

    if route_template.direction_id not in (None, direction.id):
        raise ValueError("Выбранный маршрут не относится к указанной области экспертизы.")

    if route_template.article_type_id not in (None, submission.article_type_id):
        raise ValueError("Выбранный маршрут не относится к указанному типу материала.")

    if not is_route_template_selectable(route_template, article_type=submission.article_type):
        raise ValueError("Выбранный маршрут недоступен для отправки материала.")

    return direction, route_template


def has_valid_submission_route_selection(submission):
    if submission.direction_id is None or submission.route_template_id is None:
        return False

    try:
        _validate_submission_route_selection(
            submission,
            direction=submission.direction,
            route_template=submission.route_template,
        )
    except ValueError:
        return False

    return True


def _resolve_submission_route_selection_for_submit(
    submission,
    *,
    direction: Direction | None,
    route_template: RouteTemplate | None,
):
    if direction is not None or route_template is not None:
        return _validate_submission_route_selection(
            submission,
            direction=direction,
            route_template=route_template,
        )

    suggestion = ensure_submission_route_suggestion(submission)
    if suggestion is None:
        raise ValueError(
            "Не удалось автоматически определить область экспертизы для отправки материала."
        )

    return _validate_submission_route_selection(
        submission,
        direction=suggestion.direction,
        route_template=suggestion.route_template,
    )


@transaction.atomic
def submit_submission(
    submission,
    *,
    direction: Direction | None = None,
    route_template: RouteTemplate | None = None,
    submitted_by=None,
):
    if submission.current_version is None:
        raise ValueError("Submission must have a current version before submission.")

    if submission.status != SubmissionStatus.SUBMITTED:
        raise ValueError(f"Submission in status '{submission.status}' cannot be submitted.")

    if submitted_by is not None and submitted_by != submission.author and not submitted_by.is_superuser:
        raise PermissionError("Only the author can submit this submission.")

    direction, route_template = _resolve_submission_route_selection_for_submit(
        submission,
        direction=direction,
        route_template=route_template,
    )

    submission.direction = direction
    submission.route_template = route_template
    submission.submitted_at = timezone.now()
    submission.save(update_fields=["direction", "route_template", "submitted_at", "updated_at"])

    from apps.workflow.services import start_route_review_workflow

    start_route_review_workflow(submission)

    return submission


@transaction.atomic
def update_submission_route_before_launch(submission, *, direction: Direction, route_template: RouteTemplate):
    direction, route_template = _validate_submission_route_selection(
        submission,
        direction=direction,
        route_template=route_template,
    )

    workflow_run = (
        submission.workflow_runs.filter(
            awaiting_route_approval=True,
            status=WorkflowRunStatus.ACTIVE,
        )
        .order_by("-created_at", "-pk")
        .first()
    )
    if workflow_run is None:
        raise ValueError("Маршрут уже запущен. Менять его может только root через админку.")

    submission.direction = direction
    submission.route_template = route_template
    submission.save(update_fields=["direction", "route_template", "updated_at"])
    if workflow_run.route_template_id != route_template.id:
        workflow_run.route_template = route_template
        workflow_run.save(update_fields=["route_template"])

    return submission


@transaction.atomic
def confirm_submission_route_before_launch(submission, *, actor, direction: Direction, route_template: RouteTemplate):
    submission = update_submission_route_before_launch(
        submission,
        direction=direction,
        route_template=route_template,
    )

    workflow_run = (
        submission.workflow_runs.filter(
            awaiting_route_approval=True,
            status=WorkflowRunStatus.ACTIVE,
        )
        .select_related("current_step")
        .order_by("-created_at", "-pk")
        .first()
    )
    if workflow_run is None or workflow_run.current_step_id is None:
        raise ValueError("Этап проверки маршрута кафедрой уже завершен.")

    from apps.workflow.models import ApprovalTask, ApprovalTaskStatus
    from apps.workflow.services import approve_task

    route_review_task = (
        ApprovalTask.objects.filter(
            workflow_step=workflow_run.current_step,
            status=ApprovalTaskStatus.ACTIVE,
        )
        .select_related("workflow_step", "assigned_user", "assigned_group", "assigned_unit")
        .first()
    )
    if route_review_task is None:
        raise ValueError("Не найдена активная задача проверки маршрута кафедрой.")

    approve_task(
        route_review_task,
        actor,
        comment="Маршрут подтвержден заведующим кафедрой.",
    )
    return submission
