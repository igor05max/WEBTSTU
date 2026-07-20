from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.db.models import Prefetch
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, render

from apps.accounts.access import is_root_admin
from apps.accounts.models import User
from apps.checks.models import CheckRun
from apps.conclusions.models import ConclusionDocument
from apps.workflow.forms import TaskDecisionForm
from apps.workflow.models import ApprovalTask, ApprovalTaskStatus, TaskDecision, WorkflowStep
from apps.workflow.selectors import (
    build_inbox_tabs,
    filter_tasks_by_scope,
    get_active_visible_tasks_queryset,
    get_decision_history_queryset,
    get_visible_tasks_queryset,
)
from apps.workflow.services import (
    approve_task,
    get_task_action_state,
    reject_task,
    request_revision,
)


def _user_label(user):
    full_name = user.get_full_name().strip()
    return full_name or user.username


def _get_visible_task_or_404(user, pk):
    queryset = get_visible_tasks_queryset(user).select_related(
        "assigned_user",
        "assigned_group",
        "assigned_unit",
        "workflow_step__workflow_run__submission__author",
        "workflow_step__workflow_run__submission__direction",
        "workflow_step__workflow_run__submission__journal",
        "workflow_step__workflow_run__submission__article_type",
        "workflow_step__workflow_run__submission__current_version",
        "workflow_step__workflow_run__route_template",
    ).prefetch_related(
        "workflow_step__workflow_run__submission__authors",
        Prefetch("decisions", queryset=TaskDecision.objects.select_related("actor"))
    )
    return get_object_or_404(queryset, pk=pk)


def _build_task_forms(post_data=None, *, bound_form_key=None):
    form_specs = {
        "approve_form": {"prefix": "approve", "require_comment": False},
        "revision_form": {"prefix": "revision", "require_comment": True},
        "reject_form": {"prefix": "reject", "require_comment": True},
    }
    forms = {}
    for form_key, spec in form_specs.items():
        data = post_data if form_key == bound_form_key else None
        forms[form_key] = TaskDecisionForm(
            data=data,
            prefix=spec["prefix"],
            require_comment=spec["require_comment"],
        )
    return forms


def _get_request_meta(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    client_ip = forwarded_for.split(",", maxsplit=1)[0].strip() if forwarded_for else ""
    return {
        "client_ip": client_ip or request.META.get("REMOTE_ADDR") or None,
        "user_agent": request.META.get("HTTP_USER_AGENT", ""),
    }


def _render_task_detail(request, task, *, forms=None, status=200):
    if forms is None:
        forms = _build_task_forms()
    workflow_run = task.workflow_step.workflow_run
    submission = workflow_run.submission
    current_active_task = None
    if workflow_run.current_step_id:
        current_active_task = (
            ApprovalTask.objects.filter(
                workflow_step=workflow_run.current_step,
                status=ApprovalTaskStatus.ACTIVE,
            )
            .select_related("workflow_step")
            .first()
        )
    workflow_steps = (
        WorkflowStep.objects.filter(workflow_run=workflow_run)
        .select_related("assigned_user", "assigned_group", "assigned_unit")
        .prefetch_related(
            Prefetch(
                "tasks",
                queryset=ApprovalTask.objects.select_related(
                    "assigned_user",
                    "assigned_group",
                    "assigned_unit",
                ).prefetch_related(
                    Prefetch("decisions", queryset=TaskDecision.objects.select_related("actor"))
                ),
            )
        )
        .order_by("order", "id")
    )
    check_runs = (
        CheckRun.objects.filter(submission=submission)
        .select_related("check_definition", "version")
        .order_by("created_at", "id")
    )
    conclusion_document = (
        ConclusionDocument.objects.filter(workflow_run=workflow_run)
        .prefetch_related("signatures__signer", "signatures__submission_version")
        .first()
    )
    return render(
        request,
        "workflow/task_detail.html",
        {
            "task": task,
            "submission": submission,
            "workflow_run": workflow_run,
            "workflow_steps": workflow_steps,
            "check_runs": check_runs,
            "current_active_task": current_active_task,
            "is_route_approval_task": bool(
                workflow_run.awaiting_route_approval and workflow_run.current_step_id == task.workflow_step_id
            ),
            "conclusion_document": conclusion_document,
            "action_state": get_task_action_state(task, request.user),
            **forms,
        },
        status=status,
    )


@login_required
def inbox(request):
    current_scope = request.GET.get("scope", "all")
    active_tasks_queryset = get_active_visible_tasks_queryset(request.user)
    tabs = build_inbox_tabs(request.user, active_tasks_queryset, current_scope)
    if not any(tab["is_active"] for tab in tabs):
        current_scope = "all"
        tabs = build_inbox_tabs(request.user, active_tasks_queryset, current_scope)

    if current_scope == "history":
        tasks_queryset = get_decision_history_queryset(request.user)
    else:
        tasks_queryset = filter_tasks_by_scope(active_tasks_queryset, request.user, current_scope)

    tasks = list(
        tasks_queryset
        .select_related(
            "assigned_user",
            "assigned_group",
            "assigned_unit",
            "workflow_step__workflow_run__submission__author",
            "workflow_step__workflow_run__submission__direction",
            "workflow_step__workflow_run__submission__journal",
            "workflow_step__workflow_run__submission__article_type",
            "workflow_step__workflow_run__route_template",
        )
        .prefetch_related(
            "workflow_step__workflow_run__submission__authors",
            Prefetch("decisions", queryset=TaskDecision.objects.select_related("actor")),
        )
        .order_by("-decisions__created_at", "-created_at")
    )

    if current_scope == "history":
        for task in tasks:
            task.history_decision = next(
                (decision for decision in task.decisions.all() if decision.actor_id == request.user.id),
                None,
            )

    active_tab = next(tab for tab in tabs if tab["is_active"])
    return render(
        request,
        "workflow/inbox.html",
        {
            "tasks": tasks,
            "tabs": tabs,
            "active_tab": active_tab,
            "is_history": current_scope == "history",
        },
    )


@login_required
def assignment_options_view(request):
    if not is_root_admin(request.user):
        raise Http404

    group_id = request.GET.get("group_id")
    role_id = request.GET.get("role_id")

    roles_queryset = Group.objects.none()
    users_queryset = User.objects.none()

    if group_id:
        roles_queryset = Group.objects.filter(org_units__id=group_id).order_by("name").distinct()
        if role_id:
            users_queryset = (
                User.objects.filter(org_unit_id=group_id, groups__id=role_id)
                .order_by("last_name", "first_name", "username")
                .distinct()
            )

    return JsonResponse(
        {
            "roles": [{"id": role.id, "name": role.name} for role in roles_queryset],
            "users": [{"id": user.id, "name": _user_label(user)} for user in users_queryset],
        }
    )


@login_required
def task_detail(request, pk):
    task = _get_visible_task_or_404(request.user, pk)
    return _render_task_detail(request, task)


@login_required
def conclusion_document_download(request, pk):
    task = _get_visible_task_or_404(request.user, pk)
    document = get_object_or_404(
        ConclusionDocument,
        workflow_run=task.workflow_step.workflow_run,
    )
    try:
        source = document.document_file.open("rb")
    except OSError as exc:
        raise Http404 from exc
    return FileResponse(
        source,
        as_attachment=True,
        filename=document.document_file.name.rsplit("/", maxsplit=1)[-1],
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


def _handle_task_action(request, pk, action, *, form_key, success_message):
    if request.method != "POST":
        raise Http404

    task = _get_visible_task_or_404(request.user, pk)
    forms = _build_task_forms(request.POST, bound_form_key=form_key)
    form = forms[form_key]
    if not form.is_valid():
        messages.error(request, "Не удалось сохранить результат этапа. Исправьте ошибки в форме.")
        return _render_task_detail(request, task, forms=forms, status=400)

    try:
        action(
            task,
            request.user,
            comment=form.cleaned_data["comment"],
            request_meta=_get_request_meta(request),
        )
    except (PermissionError, ValueError) as exc:
        messages.error(request, str(exc))
        return _render_task_detail(request, task, forms=forms, status=400)
    else:
        messages.success(request, success_message)
        refreshed_task = _get_visible_task_or_404(request.user, pk)
        return _render_task_detail(request, refreshed_task)


@login_required
def approve_task_view(request, pk):
    return _handle_task_action(
        request,
        pk,
        approve_task,
        form_key="approve_form",
        success_message="Результат этапа сохранен: этап согласован.",
    )


@login_required
def reject_task_view(request, pk):
    return _handle_task_action(
        request,
        pk,
        reject_task,
        form_key="reject_form",
        success_message="Результат этапа сохранен: заявка отклонена.",
    )


@login_required
def request_revision_view(request, pk):
    return _handle_task_action(
        request,
        pk,
        request_revision,
        form_key="revision_form",
        success_message="Результат этапа сохранен: заявка возвращена на доработку.",
    )
