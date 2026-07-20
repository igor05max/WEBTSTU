from django.db import transaction
from django.utils import timezone

from apps.accounts.roles import get_chair_head_candidates, get_or_create_chair_head_role
from apps.conclusions.services import (
    append_prorector_approval_step,
    create_authenticated_signature,
    ensure_conclusion_document,
    finalize_conclusion_package,
)
from apps.submissions.models import SubmissionAppeal, SubmissionAppealStatus, SubmissionStatus
from apps.workflow.models import (
    ApprovalTask,
    ApprovalTaskStatus,
    AssigneeKind,
    RevisionStrategy,
    RouteTemplate,
    TaskDecision,
    TaskDecisionType,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowStep,
    WorkflowStepStatus,
)

ROUTE_REVIEW_STEP_NAME = "Проверка маршрута кафедрой"


def select_route_template(submission):
    if submission.route_template_id is not None:
        route_template = submission.route_template
        if (
            route_template.direction_id is not None
            and route_template.direction_id != submission.direction_id
        ):
            raise ValueError("У выбранного маршрута не совпадает область экспертизы.")
        if route_template.article_type_id not in (None, submission.article_type_id):
            raise ValueError("У выбранного маршрута не совпадает тип материала.")
        if not route_template.is_active:
            raise ValueError("Выбранный шаблон маршрута больше не активен.")
        return route_template

    candidates = []
    candidate_querysets = []
    if submission.article_type_id is not None:
        candidate_querysets.append(
            RouteTemplate.objects.filter(
                is_active=True,
                article_type_id=submission.article_type_id,
            ).prefetch_related("step_templates")
        )
    candidate_querysets.append(
        RouteTemplate.objects.filter(
            is_active=True,
            article_type__isnull=True,
        ).prefetch_related("step_templates")
    )

    for candidate_queryset in candidate_querysets:
        for route_template in candidate_queryset:
            if route_template.direction_id is None:
                candidates.append(route_template)
                continue
            if (
                submission.direction_id is not None
                and route_template.direction_id == submission.direction_id
            ):
                candidates.append(route_template)

    unique_candidates = {}
    for route_template in candidates:
        unique_candidates[route_template.id] = route_template
    candidates = list(unique_candidates.values())

    if not candidates:
        raise ValueError("Для выбранных области и типа материала нет активного шаблона маршрута.")

    candidates.sort(
        key=lambda template: (
            0 if template.direction_id is None else 1,
            0 if template.article_type_id == submission.article_type_id else 1,
            0 if submission.direction_id is not None and template.direction_id == submission.direction_id else 1,
            -template.priority,
            template.pk,
        )
    )
    return candidates[0]


def _validate_assignment_membership(assigned_unit, assigned_group, assigned_user):
    if assigned_unit is None or assigned_group is None:
        raise ValueError("Для этапа нужно выбрать группу и роль.")

    if not assigned_unit.available_roles.filter(id=assigned_group.id).exists():
        raise ValueError("Выбранная роль не входит в список ролей выбранной группы.")

    if assigned_user is None:
        return assigned_unit, assigned_group, assigned_user

    if assigned_user.org_unit_id != assigned_unit.id:
        raise ValueError("Пользователь этапа должен относиться к выбранной группе.")

    if not assigned_user.groups.filter(id=assigned_group.id).exists():
        raise ValueError("У выбранного пользователя нет указанной роли в этой группе.")

    return assigned_unit, assigned_group, assigned_user


def _resolve_author_chair_head_assignment(submission):
    author = submission.author
    chair_org_unit = getattr(author, "chair_org_unit", None)
    if chair_org_unit is None:
        raise ValueError("У отправителя не указана кафедра, поэтому нельзя выбрать заведующего кафедрой.")

    chair_head_role = get_or_create_chair_head_role()
    candidates = list(get_chair_head_candidates(chair_org_unit))
    if not candidates:
        raise ValueError(
            f'Для кафедры "{chair_org_unit.name}" не найден пользователь с ролью заведующего кафедрой.'
        )
    if len(candidates) > 1:
        raise ValueError(
            f'Для кафедры "{chair_org_unit.name}" найдено несколько пользователей с ролью заведующего кафедрой.'
        )
    return None, chair_head_role, candidates[0]


def _resolve_assignment(step_template, submission):
    if step_template.assignee_kind == AssigneeKind.AUTHOR_CHAIR_HEAD:
        return _resolve_author_chair_head_assignment(submission)

    direction_assignment = None
    if submission.direction_id is not None:
        direction_assignment = (
            step_template.direction_assignments.filter(direction_id=submission.direction_id)
            .select_related("target_unit", "target_group", "target_user")
            .first()
        )

    if direction_assignment is not None:
        return _validate_assignment_membership(
            direction_assignment.target_unit,
            direction_assignment.target_group,
            direction_assignment.target_user,
        )

    return _validate_assignment_membership(
        step_template.target_unit,
        step_template.target_group,
        step_template.target_user,
    )


def _create_step_task(step):
    return ApprovalTask.objects.create(
        workflow_step=step,
        status=ApprovalTaskStatus.ACTIVE,
        assigned_group=step.assigned_group,
        assigned_unit=step.assigned_unit,
        assigned_user=step.assigned_user,
        activated_at=timezone.now(),
    )


def _activate_step(workflow_run, step):
    if step.status == WorkflowStepStatus.PENDING:
        step.status = WorkflowStepStatus.ACTIVE
        step.started_at = step.started_at or timezone.now()
        step.save()

    workflow_run.status = WorkflowRunStatus.ACTIVE
    workflow_run.current_step = step
    workflow_run.save()
    return _create_step_task(step)


def _latest_paused_run(submission):
    return submission.workflow_runs.filter(status=WorkflowRunStatus.PAUSED_FOR_REVISION).order_by(
        "-created_at", "-pk"
    ).first()


def _shift_workflow_steps_for_insert(workflow_run, start_order):
    steps_to_shift = workflow_run.steps.filter(order__gte=start_order).order_by("-order", "-id")
    for step in steps_to_shift:
        step.order += 1
        step.save(update_fields=["order"])


def _get_next_pending_step(workflow_run):
    return workflow_run.steps.filter(status=WorkflowStepStatus.PENDING).order_by("order", "id").first()


def _latest_rejected_task_for_submission(submission):
    return (
        ApprovalTask.objects.filter(
            workflow_step__workflow_run__submission=submission,
            status=ApprovalTaskStatus.REJECTED,
        )
        .select_related(
            "assigned_user",
            "workflow_step__workflow_run",
            "workflow_step__workflow_run__submission",
        )
        .order_by("-decided_at", "-pk")
        .first()
    )


def _build_route_steps(
    workflow_run,
    submission,
    *,
    start_order=1,
    route_template=None,
    include_prorector_approval=False,
):
    route_template = route_template or select_route_template(submission)
    if workflow_run.route_template_id != route_template.id:
        workflow_run.route_template = route_template
        workflow_run.save(update_fields=["route_template"])

    step_templates = list(route_template.step_templates.order_by("order", "id"))
    if not step_templates:
        raise ValueError("Selected route template has no steps.")

    created_steps = []
    for step_template in step_templates:
        assigned_unit, assigned_group, assigned_user = _resolve_assignment(step_template, submission)
        created_steps.append(
            WorkflowStep.objects.create(
                workflow_run=workflow_run,
                step_template=step_template,
                order=start_order + step_template.order - 1,
                name=step_template.name,
                assignee_kind=step_template.assignee_kind,
                assigned_group=assigned_group,
                assigned_unit=assigned_unit,
                assigned_user=assigned_user,
                can_reject=step_template.can_reject,
                can_request_revision=step_template.can_request_revision,
            )
        )
    if include_prorector_approval:
        created_steps.append(
            append_prorector_approval_step(
                workflow_run,
                after_order=created_steps[-1].order,
            )
        )
    return created_steps


@transaction.atomic
def start_route_review_workflow(submission):
    active_run_exists = submission.workflow_runs.filter(status=WorkflowRunStatus.ACTIVE).exists()
    if active_run_exists:
        raise ValueError("Submission already has an active workflow.")

    route_template = select_route_template(submission)
    if not route_template.step_templates.exists():
        raise ValueError("Selected route template has no steps.")
    assigned_unit, assigned_group, assigned_user = _resolve_author_chair_head_assignment(submission)

    workflow_run = WorkflowRun.objects.create(
        submission=submission,
        route_template=route_template,
        status=WorkflowRunStatus.ACTIVE,
        current_step=None,
        awaiting_route_approval=True,
        started_at=timezone.now(),
    )
    route_review_step = WorkflowStep.objects.create(
        workflow_run=workflow_run,
        step_template=None,
        order=1,
        name=ROUTE_REVIEW_STEP_NAME,
        assignee_kind=AssigneeKind.AUTHOR_CHAIR_HEAD,
        assigned_group=assigned_group,
        assigned_unit=assigned_unit,
        assigned_user=assigned_user,
        can_reject=True,
        can_request_revision=True,
        status=WorkflowStepStatus.PENDING,
    )
    _activate_step(workflow_run, route_review_step)

    submission.status = SubmissionStatus.IN_REVIEW
    submission.save(update_fields=["status", "updated_at"])
    return workflow_run


@transaction.atomic
def start_workflow(submission, *, route_template=None):
    active_run_exists = submission.workflow_runs.filter(status=WorkflowRunStatus.ACTIVE).exists()
    if active_run_exists:
        raise ValueError("Submission already has an active workflow.")

    route_template = route_template or select_route_template(submission)

    workflow_run = WorkflowRun.objects.create(
        submission=submission,
        route_template=route_template,
        status=WorkflowRunStatus.ACTIVE,
        awaiting_route_approval=False,
        started_at=timezone.now(),
    )
    created_steps = _build_route_steps(
        workflow_run,
        submission,
        start_order=1,
        route_template=route_template,
    )

    first_step = created_steps[0]
    _activate_step(workflow_run, first_step)

    submission.status = SubmissionStatus.IN_REVIEW
    submission.save()
    return workflow_run


@transaction.atomic
def resume_or_start_workflow(submission):
    paused_run = _latest_paused_run(submission)
    if paused_run is None:
        selected_route_template = select_route_template(submission)
        return start_workflow(submission, route_template=selected_route_template)

    selected_route_template = select_route_template(submission)

    if paused_run.awaiting_route_approval:
        if paused_run.route_template_id != selected_route_template.id:
            paused_run.route_template = selected_route_template
            paused_run.save(update_fields=["route_template"])
        step = paused_run.current_step
        if step is None:
            raise ValueError("Paused workflow has no current step to resume.")

        paused_run.status = WorkflowRunStatus.ACTIVE
        paused_run.finished_at = None
        paused_run.save(update_fields=["status", "finished_at"])

        if step.status != WorkflowStepStatus.ACTIVE:
            step.status = WorkflowStepStatus.ACTIVE
            step.finished_at = None
            if step.started_at is None:
                step.started_at = timezone.now()
            step.save(update_fields=["status", "finished_at", "started_at"])

        _create_step_task(step)
        submission.status = SubmissionStatus.IN_REVIEW
        submission.save(update_fields=["status", "updated_at"])
        return paused_run

    if paused_run.route_template_id != selected_route_template.id:
        return start_workflow(submission, route_template=selected_route_template)

    step = paused_run.current_step
    if step is None:
        raise ValueError("Paused workflow has no current step to resume.")

    paused_run.status = WorkflowRunStatus.ACTIVE
    paused_run.finished_at = None
    paused_run.save()

    if step.status != WorkflowStepStatus.ACTIVE:
        step.status = WorkflowStepStatus.ACTIVE
        step.finished_at = None
        if step.started_at is None:
            step.started_at = timezone.now()
        step.save()

    _create_step_task(step)
    submission.status = SubmissionStatus.IN_REVIEW
    submission.save()
    return paused_run


@transaction.atomic
def insert_manual_step(
    workflow_run,
    *,
    name,
    assigned_unit,
    assigned_group,
    assigned_user,
    insert_after_step=None,
    can_reject=True,
    can_request_revision=True,
):
    if workflow_run.status in (WorkflowRunStatus.COMPLETED, WorkflowRunStatus.REJECTED):
        raise ValueError("Нельзя добавлять этап в завершенный или отклоненный маршрут.")

    step_name = (name or "").strip()
    if not step_name:
        raise ValueError("Нужно указать название этапа.")

    _validate_assignment_membership(assigned_unit, assigned_group, assigned_user)

    if insert_after_step is not None and insert_after_step.workflow_run_id != workflow_run.id:
        raise ValueError("Этап для вставки выбран из другого маршрута.")

    insert_order = 1 if insert_after_step is None else insert_after_step.order + 1
    _shift_workflow_steps_for_insert(workflow_run, insert_order)

    return WorkflowStep.objects.create(
        workflow_run=workflow_run,
        step_template=None,
        order=insert_order,
        name=step_name,
        assignee_kind=AssigneeKind.FIXED_UNIT_GROUP,
        assigned_group=assigned_group,
        assigned_unit=assigned_unit,
        assigned_user=assigned_user,
        can_reject=can_reject,
        can_request_revision=can_request_revision,
        status=WorkflowStepStatus.PENDING,
    )


def _validate_active_task(task):
    if task.status != ApprovalTaskStatus.ACTIVE:
        raise ValueError("Only active tasks can be decided.")
    if task.workflow_step.status != WorkflowStepStatus.ACTIVE:
        raise ValueError("Task belongs to a non-active workflow step.")
    if task.workflow_step.workflow_run.status != WorkflowRunStatus.ACTIVE:
        raise ValueError("Task belongs to a non-active workflow run.")


def _validate_actor(task, actor):
    if actor is None:
        raise ValueError("Нужно указать пользователя, который принимает решение.")
    if actor.is_superuser:
        return
    if task.assigned_user_id and task.assigned_user_id != actor.id:
        raise PermissionError("Задача назначена другому пользователю.")
    if task.assigned_group_id and not actor.groups.filter(id=task.assigned_group_id).exists():
        raise PermissionError("Пользователь не состоит в назначенной роли.")
    if task.assigned_unit_id and actor.org_unit_id != task.assigned_unit_id:
        raise PermissionError("Пользователь не относится к назначенной группе.")


def get_task_action_state(task, actor):
    state = {
        "can_act": False,
        "can_approve": False,
        "can_reject": False,
        "can_request_revision": False,
        "reason": "",
    }

    if actor is None or not getattr(actor, "is_authenticated", False):
        state["reason"] = "Нужно войти в систему, чтобы зафиксировать результат этапа."
        return state

    if task.status != ApprovalTaskStatus.ACTIVE:
        state["reason"] = "Результат по этому этапу уже зафиксирован."
        return state

    if task.workflow_step.status != WorkflowStepStatus.ACTIVE:
        state["reason"] = "Этот этап уже не активен."
        return state

    if task.workflow_step.workflow_run.status != WorkflowRunStatus.ACTIVE:
        state["reason"] = "Маршрут согласования уже не активен."
        return state

    try:
        _validate_actor(task, actor)
    except (PermissionError, ValueError) as exc:
        state["reason"] = str(exc)
        return state

    state["can_act"] = True
    state["can_approve"] = True
    state["can_reject"] = task.workflow_step.can_reject
    state["can_request_revision"] = task.workflow_step.can_request_revision
    return state


def get_appeal_action_state(appeal, actor):
    state = {
        "can_act": False,
        "can_approve": False,
        "can_reject": False,
        "reason": "",
    }

    if actor is None or not getattr(actor, "is_authenticated", False):
        state["reason"] = "Нужно войти в систему, чтобы рассмотреть апелляцию."
        return state

    if appeal.status != SubmissionAppealStatus.PENDING:
        state["reason"] = "Апелляция уже рассмотрена."
        return state

    if actor.is_superuser or appeal.reviewer_id == actor.id:
        state["can_act"] = True
        state["can_approve"] = True
        state["can_reject"] = True
        return state

    state["reason"] = "Апелляцию может рассмотреть только отклонивший проверяющий."
    return state


def _record_decision(task, actor, decision, comment):
    return TaskDecision.objects.create(
        task=task,
        actor=actor,
        decision=decision,
        comment=comment or "",
    )


def _require_comment(comment):
    if not (comment or "").strip():
        raise ValueError("Комментарий обязателен для отрицательного результата проверки.")


def _require_appeal_comment(comment):
    if not (comment or "").strip():
        raise ValueError("Комментарий к апелляции обязателен.")


def _close_task_and_step(task, task_status, step_status):
    now = timezone.now()
    task.status = task_status
    task.decided_at = now
    task.save()

    step = task.workflow_step
    step.refresh_from_db()
    step.status = step_status
    step.finished_at = now
    step.save(update_fields=["status", "finished_at"])
    return step


@transaction.atomic
def approve_task(task, actor, comment="", *, request_meta=None):
    _validate_active_task(task)
    _validate_actor(task, actor)
    decision = _record_decision(task, actor, TaskDecisionType.APPROVE, comment)
    create_authenticated_signature(
        task,
        actor,
        decision,
        request_meta=request_meta,
    )

    step = _close_task_and_step(task, ApprovalTaskStatus.APPROVED, WorkflowStepStatus.APPROVED)
    workflow_run = step.workflow_run
    submission = workflow_run.submission

    if workflow_run.awaiting_route_approval and step.assignee_kind == AssigneeKind.AUTHOR_CHAIR_HEAD:
        created_steps = _build_route_steps(
            workflow_run,
            submission,
            start_order=step.order + 1,
            include_prorector_approval=True,
        )
        ensure_conclusion_document(workflow_run)
        workflow_run.awaiting_route_approval = False
        workflow_run.save(update_fields=["awaiting_route_approval"])
        next_step = created_steps[0]
    else:
        next_step = _get_next_pending_step(workflow_run)

    if next_step is None:
        workflow_run.status = WorkflowRunStatus.COMPLETED
        workflow_run.current_step = None
        workflow_run.finished_at = timezone.now()
        workflow_run.save()

        submission.status = SubmissionStatus.APPROVED
        submission.save()
        try:
            conclusion_document = workflow_run.conclusion_document
        except AttributeError:
            conclusion_document = None
        if conclusion_document is not None:
            finalize_conclusion_package(conclusion_document)
        return task

    _activate_step(workflow_run, next_step)
    submission.status = SubmissionStatus.IN_REVIEW
    submission.save()
    return task


@transaction.atomic
def reject_task(task, actor, comment="", *, request_meta=None):
    _validate_active_task(task)
    _validate_actor(task, actor)
    if not task.workflow_step.can_reject:
        raise ValueError("This workflow step cannot reject the submission.")
    _require_comment(comment)

    _record_decision(task, actor, TaskDecisionType.REJECT, comment)
    step = _close_task_and_step(task, ApprovalTaskStatus.REJECTED, WorkflowStepStatus.REJECTED)
    workflow_run = step.workflow_run
    workflow_run.status = WorkflowRunStatus.REJECTED
    workflow_run.current_step = None
    workflow_run.finished_at = timezone.now()
    workflow_run.save()

    submission = workflow_run.submission
    submission.status = SubmissionStatus.REJECTED
    submission.save()
    return task


@transaction.atomic
def request_revision(task, actor, comment="", *, request_meta=None):
    _validate_active_task(task)
    _validate_actor(task, actor)
    if not task.workflow_step.can_request_revision:
        raise ValueError("Этот этап нельзя вернуть на доработку.")
    _require_comment(comment)

    now = timezone.now()
    _record_decision(task, actor, TaskDecisionType.REQUEST_REVISION, comment)
    step = _close_task_and_step(
        task,
        ApprovalTaskStatus.REVISION_REQUESTED,
        WorkflowStepStatus.REVISION_REQUESTED,
    )
    workflow_run = step.workflow_run
    workflow_run.status = WorkflowRunStatus.PAUSED_FOR_REVISION
    workflow_run.current_step = step
    workflow_run.finished_at = now
    workflow_run.save(update_fields=["status", "current_step", "finished_at"])

    submission = workflow_run.submission
    submission.status = SubmissionStatus.REVISION_REQUESTED
    submission.save(update_fields=["status", "updated_at"])
    return task


@transaction.atomic
def submit_submission_appeal(submission, author, *, comment, attachment=None):
    if author != submission.author and not author.is_superuser:
        raise PermissionError("Подать апелляцию может только автор заявки.")

    if SubmissionAppeal.objects.filter(submission=submission).exists():
        raise ValueError("Апелляцию по этой заявке уже подавали.")

    if submission.status != SubmissionStatus.REJECTED:
        raise ValueError("Апелляцию можно подать только по отклоненной заявке.")

    _require_appeal_comment(comment)
    rejected_task = _latest_rejected_task_for_submission(submission)
    if rejected_task is None:
        raise ValueError("Не удалось определить отклоняющий этап для этой заявки.")

    reviewer = rejected_task.assigned_user
    if reviewer is None:
        latest_reject_decision = (
            rejected_task.decisions.filter(decision=TaskDecisionType.REJECT)
            .select_related("actor")
            .order_by("-created_at", "-pk")
            .first()
        )
        reviewer = latest_reject_decision.actor if latest_reject_decision is not None else None

    if reviewer is None:
        raise ValueError("Для отклоненной заявки не найден проверяющий, который должен рассмотреть апелляцию.")

    appeal = SubmissionAppeal.objects.create(
        submission=submission,
        rejected_task=rejected_task,
        author=author,
        reviewer=reviewer,
        comment=comment.strip(),
        attachment=attachment,
    )
    submission.status = SubmissionStatus.APPEAL_PENDING
    submission.save(update_fields=["status", "updated_at"])
    return appeal


def _validate_pending_appeal(appeal):
    if appeal.status != SubmissionAppealStatus.PENDING:
        raise ValueError("Апелляция уже рассмотрена.")


def _validate_appeal_actor(appeal, actor):
    if actor is None:
        raise ValueError("Нужно указать пользователя, который рассматривает апелляцию.")
    if actor.is_superuser:
        return
    if appeal.reviewer_id != actor.id:
        raise PermissionError("Апелляцию может рассмотреть только отклонивший проверяющий.")


@transaction.atomic
def approve_submission_appeal(appeal, actor, comment=""):
    _validate_pending_appeal(appeal)
    _validate_appeal_actor(appeal, actor)

    now = timezone.now()
    appeal.status = SubmissionAppealStatus.APPROVED
    appeal.decision_comment = comment or ""
    appeal.decided_by = actor
    appeal.decided_at = now
    appeal.save(update_fields=["status", "decision_comment", "decided_by", "decided_at"])

    workflow_run = appeal.rejected_task.workflow_step.workflow_run
    next_step = _get_next_pending_step(workflow_run)

    if next_step is None and workflow_run.awaiting_route_approval:
        step = appeal.rejected_task.workflow_step
        workflow_run.status = WorkflowRunStatus.ACTIVE
        workflow_run.current_step = step
        workflow_run.finished_at = None
        workflow_run.save(update_fields=["status", "current_step", "finished_at"])
        if step.status != WorkflowStepStatus.ACTIVE:
            step.status = WorkflowStepStatus.ACTIVE
            step.finished_at = None
            if step.started_at is None:
                step.started_at = now
            step.save(update_fields=["status", "finished_at", "started_at"])
        _create_step_task(step)

        submission = workflow_run.submission
        submission.status = SubmissionStatus.IN_REVIEW
        submission.save(update_fields=["status", "updated_at"])
        return appeal

    if next_step is None:
        workflow_run.status = WorkflowRunStatus.COMPLETED
        workflow_run.current_step = None
        workflow_run.finished_at = now
        workflow_run.save(update_fields=["status", "current_step", "finished_at"])

        submission = workflow_run.submission
        submission.status = SubmissionStatus.APPROVED
        submission.save(update_fields=["status", "updated_at"])
        return appeal

    workflow_run.status = WorkflowRunStatus.ACTIVE
    workflow_run.finished_at = None
    workflow_run.save(update_fields=["status", "finished_at"])
    _activate_step(workflow_run, next_step)

    submission = workflow_run.submission
    submission.status = SubmissionStatus.IN_REVIEW
    submission.save(update_fields=["status", "updated_at"])
    return appeal


@transaction.atomic
def reject_submission_appeal(appeal, actor, comment=""):
    _validate_pending_appeal(appeal)
    _validate_appeal_actor(appeal, actor)
    _require_appeal_comment(comment)

    now = timezone.now()
    appeal.status = SubmissionAppealStatus.REJECTED
    appeal.decision_comment = comment.strip()
    appeal.decided_by = actor
    appeal.decided_at = now
    appeal.save(update_fields=["status", "decision_comment", "decided_by", "decided_at"])

    submission = appeal.submission
    submission.status = SubmissionStatus.REJECTED
    submission.save(update_fields=["status", "updated_at"])
    return appeal
