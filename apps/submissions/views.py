import logging
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.db import OperationalError
from django.db.models import BooleanField, Case, Prefetch, Q, Value, When
from django.http import FileResponse, Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_cache_control
from django.views.decorators.http import require_POST
from django.views.decorators.clickjacking import xframe_options_sameorigin
from document_template_engine import build_latex_template

from apps.accounts.roles import has_chair_head_role
from apps.accounts.models import User
from apps.checks.models import CheckDefinition, CheckRunStatus
from apps.checks.services import queue_submission_checks
from apps.conclusions.models import ConclusionDocument
from apps.directory.formatting_templates import (
    build_rules_snapshot,
    create_formatting_template,
    process_formatting_template,
)
from apps.directory.publication_topics import resolve_or_create_publication_topic
from apps.submissions.forms import (
    FormattingRulesForm,
    SubmissionAppealDecisionForm,
    SubmissionAppealForm,
    SubmissionCreateForm,
    SubmissionSubmitForm,
    SubmissionVersionUploadForm,
)
from apps.submissions.formatting_correction import (
    FormattingCorrectionError,
    build_corrected_docx,
    build_document_template_plan,
)
from apps.submissions.document_preview import (
    DocumentPreviewError,
    build_docx_bytes_pdf,
    build_legacy_doc_pdf,
    build_word_document_pdf,
    get_display_filename,
    get_preview_kind,
    read_docx_preview,
    read_text_preview,
)
from apps.submissions.document_analysis import (
    analyze_document_bytes,
    match_authors_to_users,
    read_file_bytes,
)
from apps.submissions.models import Submission, SubmissionAppeal, SubmissionStatus, SubmissionVersion
from apps.submissions.route_suggestions import (
    ensure_submission_route_suggestion,
    get_selectable_directions_queryset,
    get_selectable_route_templates_queryset,
)
from apps.submissions.services import (
    add_submission_version,
    confirm_submission_route_before_launch,
    create_submission_with_initial_version,
    submit_submission,
)
from apps.workflow.models import (
    ApprovalTask,
    ApprovalTaskStatus,
    RouteStepDirectionAssignment,
    RouteStepTemplate,
    TaskDecision,
    WorkflowRun,
    WorkflowRunStatus,
)
from apps.workflow.selectors import build_task_visibility_q
from apps.workflow.services import (
    approve_submission_appeal,
    get_appeal_action_state,
    reject_submission_appeal,
    submit_submission_appeal,
)
from document_template_engine import normalize_template_rules


logger = logging.getLogger(__name__)

_DELETABLE_DRAFT_STATUSES = frozenset(
    {
        SubmissionStatus.DRAFT,
        SubmissionStatus.AUTO_CHECKING,
        SubmissionStatus.SUBMITTED,
    }
)

_CORRECTED_VERSION_CREATION_STATUSES = frozenset(
    {
        SubmissionStatus.DRAFT,
        SubmissionStatus.SUBMITTED,
        SubmissionStatus.REVISION_REQUESTED,
    }
)


def _task_visibility_filter(user):
    return build_task_visibility_q(user)


def _can_access_chair_submissions(user):
    return has_chair_head_role(user)


def _get_personal_submissions_queryset(user):
    return (
        Submission.objects.filter(authors=user)
        .select_related(
            "author",
            "journal",
            "publication_topic",
            "article_type",
            "formatting_template",
            "direction",
            "route_template",
            "current_version",
        )
        .prefetch_related("authors")
        .distinct()
    )


def _get_chair_submissions_queryset(user):
    if not _can_access_chair_submissions(user):
        return Submission.objects.none()
    return (
        Submission.objects.filter(
            author__chair_org_unit_id=user.chair_org_unit_id,
            submitted_at__isnull=False,
        )
        .select_related(
            "author",
            "journal",
            "publication_topic",
            "article_type",
            "formatting_template",
            "direction",
            "route_template",
            "current_version",
        )
        .prefetch_related("authors")
        .distinct()
    )


def _get_active_route_review_task_for_submission(submission, user=None):
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
        return None

    queryset = ApprovalTask.objects.filter(
        workflow_step=workflow_run.current_step,
        status=ApprovalTaskStatus.ACTIVE,
    )
    if user is not None and not user.is_superuser:
        queryset = queryset.filter(_task_visibility_filter(user))
    return queryset.select_related("workflow_step", "assigned_user", "assigned_group").first()


def _can_review_submission_route(user, submission):
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return _get_active_route_review_task_for_submission(submission) is not None
    return _get_active_route_review_task_for_submission(submission, user) is not None


def _resolve_actual_unit(step, submission):
    if step.assigned_unit_id:
        return step.assigned_unit
    return None


def _build_assignment_parts(step, submission):
    actual_unit = _resolve_actual_unit(step, submission)

    if step.assigned_user_id and step.assigned_group_id and actual_unit is not None:
        return {
            "short": str(step.assigned_user),
            "full": f"Пользователь: {step.assigned_user} | Роль: {step.assigned_group.name} | Группа: {actual_unit.name}",
            "meta": f"{step.assigned_group.name} | {actual_unit.name}",
        }

    if step.assigned_user_id and step.assigned_group_id:
        return {
            "short": str(step.assigned_user),
            "full": f"Пользователь: {step.assigned_user} | Роль: {step.assigned_group.name}",
            "meta": step.assigned_group.name,
        }

    if step.assigned_user_id:
        return {
            "short": str(step.assigned_user),
            "full": f"Пользователь: {step.assigned_user}",
            "meta": "",
        }

    if step.assigned_group_id and actual_unit is not None:
        return {
            "short": step.assigned_group.name,
            "full": f"Роль: {step.assigned_group.name} | Группа: {actual_unit.name}",
            "meta": actual_unit.name,
        }

    if step.assigned_group_id:
        return {
            "short": step.assigned_group.name,
            "full": f"Роль: {step.assigned_group.name}",
            "meta": "",
        }

    if actual_unit is not None:
        return {
            "short": actual_unit.name,
            "full": f"Группа: {actual_unit.name}",
            "meta": "",
        }

    return {
        "short": "Исполнитель не задан",
        "full": "Исполнитель не задан",
        "meta": "",
    }


def _get_route_step_template_assignment(step_template, *, direction=None):
    if direction is not None:
        direction_id = getattr(direction, "pk", direction)
        if direction_id:
            for assignment in step_template.direction_assignments.all():
                if assignment.direction_id == direction_id:
                    return assignment.target_unit, assignment.target_group, assignment.target_user
    return step_template.target_unit, step_template.target_group, step_template.target_user


def _build_route_template_assignment_parts(step_template, *, direction=None):
    actual_unit, actual_group, actual_user = _get_route_step_template_assignment(
        step_template,
        direction=direction,
    )

    if actual_user is not None and actual_group is not None and actual_unit is not None:
        return {
            "short": str(actual_user),
            "full": (
                f"Пользователь: {actual_user} | "
                f"Роль: {actual_group.name} | "
                f"Группа: {actual_unit.name}"
            ),
            "meta": f"{actual_group.name} | {actual_unit.name}",
        }

    if actual_user is not None:
        return {
            "short": str(actual_user),
            "full": f"Пользователь: {actual_user}",
            "meta": "",
        }

    if actual_group is not None and actual_unit is not None:
        return {
            "short": actual_group.name,
            "full": f"Роль: {actual_group.name} | Группа: {actual_unit.name}",
            "meta": actual_unit.name,
        }

    if actual_group is not None:
        return {
            "short": actual_group.name,
            "full": f"Роль: {actual_group.name}",
            "meta": "",
        }

    if actual_unit is not None:
        return {
            "short": actual_unit.name,
            "full": f"Группа: {actual_unit.name}",
            "meta": "",
        }

    return {
        "short": "Определяется по области",
        "full": "Исполнитель будет определен по выбранной области экспертизы.",
        "meta": "",
    }


def _build_route_preview_templates(*, article_type=None, direction=None, route_template=None):
    templates = (
        get_selectable_route_templates_queryset(article_type=article_type, direction=direction)
        .prefetch_related(
            Prefetch(
                "step_templates",
                queryset=RouteStepTemplate.objects.select_related(
                    "target_user",
                    "target_group",
                    "target_unit",
                )
                .prefetch_related(
                    Prefetch(
                        "direction_assignments",
                        queryset=RouteStepDirectionAssignment.objects.select_related(
                            "direction",
                            "target_user",
                            "target_group",
                            "target_unit",
                        ).order_by("direction__name", "id"),
                    )
                )
                .order_by("order", "id"),
            )
        )
        .order_by("direction__name", "-priority", "name")
    )
    if route_template is not None:
        route_template_id = getattr(route_template, "pk", route_template)
        templates = templates.filter(pk=route_template_id)

    preview_templates = []
    for route_template in templates:
        resolved_direction_id = route_template.direction_id
        resolved_direction_name = route_template.direction.name if route_template.direction_id else ""
        if resolved_direction_id is None and direction is not None:
            resolved_direction_id = getattr(direction, "pk", direction)
            resolved_direction_name = getattr(direction, "name", resolved_direction_name)

        steps = []
        for step_template in route_template.step_templates.all():
            assignment_parts = _build_route_template_assignment_parts(
                step_template,
                direction=direction,
            )
            steps.append(
                {
                    "order": step_template.order,
                    "name": step_template.name,
                    "assignment_short_text": assignment_parts["short"],
                    "assignment_meta_text": assignment_parts["meta"],
                    "assignment_full_text": assignment_parts["full"],
                }
            )

        preview_templates.append(
            {
                "id": route_template.id,
                "direction_id": resolved_direction_id,
                "direction_name": resolved_direction_name,
                "article_type_id": route_template.article_type_id,
                "article_type_name": route_template.article_type.name if route_template.article_type_id else "",
                "name": route_template.name,
                "steps": steps,
            }
        )

    return preview_templates


def _build_route_preview_templates_by_direction(*, article_type=None):
    selectable_directions = list(get_selectable_directions_queryset(article_type=article_type))
    previews_by_direction = {}
    for direction in selectable_directions:
        previews_by_direction[str(direction.id)] = _build_route_preview_templates(
            article_type=article_type,
            direction=direction,
        )
    return previews_by_direction


def _pick_route_preview_template(previews_by_direction, *, direction=None, route_template=None):
    direction_id = getattr(direction, "pk", direction)
    route_template_id = getattr(route_template, "pk", route_template)
    previews = previews_by_direction.get(str(direction_id or ""), [])
    if route_template_id is not None:
        for preview in previews:
            if preview["id"] == route_template_id:
                return preview
    if previews:
        return previews[0]
    return None


def _get_submission_appeal_or_none(submission):
    try:
        return submission.appeal
    except SubmissionAppeal.DoesNotExist:
        return None


def _get_latest_check_run_for_current_version(submission, check_runs, check_code):
    if submission.current_version_id is None:
        return None

    for run in check_runs:
        if run.version_id == submission.current_version_id and run.check_definition.code == check_code:
            return run
    return None


def _get_submission_status_tone(status):
    if status == SubmissionStatus.APPROVED:
        return "success"
    if status == SubmissionStatus.REJECTED:
        return "danger"
    if status == SubmissionStatus.DRAFT:
        return "warning"
    return "primary"


def _build_check_entries(submission, check_runs):
    definitions = list(CheckDefinition.objects.filter(is_active=True).order_by("order", "id"))
    current_version_id = submission.current_version_id
    latest_runs_by_code = {}
    for run in check_runs:
        if current_version_id is None or run.version_id != current_version_id:
            continue
        check_code = run.check_definition.code
        if check_code not in latest_runs_by_code:
            latest_runs_by_code[check_code] = run

    default_messages = {
        CheckRunStatus.PENDING: "Ожидает запуска.",
        CheckRunStatus.RUNNING: "Проверка выполняется.",
    }
    tone_map = {
        CheckRunStatus.PENDING: "muted",
        CheckRunStatus.RUNNING: "primary",
        CheckRunStatus.PASSED: "success",
        CheckRunStatus.FAILED: "danger",
        CheckRunStatus.PARTIAL: "warning",
        CheckRunStatus.NOT_PERFORMED: "warning",
    }
    entries = []
    for definition in definitions:
        run = latest_runs_by_code.get(definition.code)
        if run is None:
            status = CheckRunStatus.PENDING
            status_display = "Ожидает"
            payload = {}
        else:
            status = run.status
            status_display = run.get_status_display()
            payload = run.result_payload or {}

        issues = payload.get("issues") if isinstance(payload.get("issues"), list) else []
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
        details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
        if status == CheckRunStatus.NOT_PERFORMED:
            status_display = "Не выполнена"
            tone = "warning"
        elif status == CheckRunStatus.PARTIAL:
            status_display = "Выполнена частично"
            tone = "warning"
        elif status in {CheckRunStatus.PASSED, CheckRunStatus.FAILED}:
            if summary.get("critical") or summary.get("error"):
                status_display = "Есть замечания"
                tone = "danger"
            elif summary.get("warning"):
                status_display = "Есть рекомендации"
                tone = "warning"
            else:
                status_display = "Проверено"
                tone = "success"
        else:
            tone = tone_map.get(status, "muted")

        entries.append(
            {
                "code": definition.code,
                "name": definition.name,
                "status": status,
                "status_display": status_display,
                "tone": tone,
                "message": str(payload.get("message") or default_messages.get(status, "")).strip(),
                "issues": issues,
                "summary": summary,
                "metrics": metrics,
                "details": details,
                "has_details": bool(issues or metrics or details),
            }
        )
    return entries


def _build_formatting_rule_rows(snapshot):
    rules = normalize_template_rules((snapshot or {}).get("effective") or {})
    page = rules.get("page") or {}
    margins = page.get("margins_cm") or {}
    body = rules.get("body") or {}
    structure = rules.get("structure") or {}
    limits = rules.get("limits") or {}
    rows = []

    def add(label, value):
        if value not in (None, "", [], {}):
            rows.append({"label": label, "value": value})

    add("Размер страницы", page.get("size"))
    orientation = {"portrait": "книжная", "landscape": "альбомная"}.get(
        page.get("orientation"),
        page.get("orientation"),
    )
    add("Ориентация", orientation)
    margin_values = [
        margins.get("top"),
        margins.get("right"),
        margins.get("bottom"),
        margins.get("left"),
    ]
    if any(value is not None for value in margin_values):
        add(
            "Поля (верх / право / низ / лево)",
            " / ".join("—" if value is None else f"{value} см" for value in margin_values),
        )
    add("Основной шрифт", body.get("font_family"))
    if body.get("font_size_pt") is not None:
        add("Размер шрифта", f"{body['font_size_pt']} пт")
    add("Межстрочный интервал", body.get("line_spacing"))
    if body.get("first_line_indent_cm") is not None:
        add("Абзацный отступ", f"{body['first_line_indent_cm']} см")
    add("Выравнивание", body.get("alignment"))
    required_sections = structure.get("required_sections") or []
    add("Обязательные разделы", ", ".join(str(value) for value in required_sections))
    if limits.get("min_words") is not None:
        add("Минимальный объём", f"{limits['min_words']} слов")
    if limits.get("max_words") is not None:
        add("Максимальный объём", f"{limits['max_words']} слов")
    return rows


def _can_view_submission(user, submission):
    if user.is_superuser or submission.authors.filter(pk=user.id).exists():
        return True
    if (
        _can_access_chair_submissions(user)
        and submission.submitted_at is not None
        and submission.author.chair_org_unit_id is not None
        and submission.author.chair_org_unit_id == user.chair_org_unit_id
    ):
        return True
    if not user.is_authenticated:
        return False
    return ApprovalTask.objects.filter(
        Q(workflow_step__workflow_run__submission=submission)
        & (_task_visibility_filter(user) | Q(decisions__actor=user))
    ).exists()


def _get_viewable_submission_or_404(user, pk):
    submission = get_object_or_404(
        Submission.objects.select_related("author").prefetch_related("authors"),
        pk=pk,
    )
    if not _can_view_submission(user, submission):
        raise Http404
    return submission


def _get_viewable_conclusion_or_404(user, submission_pk, conclusion_pk):
    submission = _get_viewable_submission_or_404(user, submission_pk)
    return get_object_or_404(
        ConclusionDocument,
        pk=conclusion_pk,
        submission=submission,
    )


def _get_route_state_text(step):
    state_map = {
        "approved": "Согласовано",
        "active": "На рассмотрении",
        "pending": "Ожидает",
        "rejected": "Отклонено",
        "revision_requested": "На доработке",
        "skipped": "Пропущен",
    }
    return state_map.get(step.status, step.get_status_display())


def _task_sort_key(task):
    return (task.activated_at or task.created_at, task.id)


def _build_step_history_entries(step, submission):
    assignment_parts = _build_assignment_parts(step, submission)
    tasks = sorted(step.tasks.all(), key=_task_sort_key)
    history_entries = []

    for attempt_number, task in enumerate(tasks, start=1):
        task_decisions = list(task.decisions.all())
        attempt_label = f"Попытка {attempt_number}" if len(tasks) > 1 else ""

        if task_decisions:
            for decision in task_decisions:
                history_entries.append(
                    {
                        "step_title": step.name,
                        "step_order": step.order,
                        "attempt_label": attempt_label,
                        "attempt_number": attempt_number,
                        "actor": str(decision.actor),
                        "assignment_text": assignment_parts["full"],
                        "date": decision.created_at,
                        "result": decision.get_decision_display(),
                        "comment": decision.comment.strip(),
                        "tone": decision.decision,
                    }
                )
            continue

        if task.status == "active":
            history_entries.append(
                {
                    "step_title": step.name,
                    "step_order": step.order,
                    "attempt_label": attempt_label,
                    "attempt_number": attempt_number,
                    "actor": assignment_parts["short"],
                    "assignment_text": assignment_parts["full"],
                    "date": task.activated_at or task.created_at,
                    "result": "Ожидает решения",
                    "comment": "",
                    "tone": "active",
                }
            )

    return history_entries


@login_required
def submission_list(request):
    owner = request.GET.get("owner", "me")
    scope = request.GET.get("scope", "all")
    search_query = (request.GET.get("q") or "").strip()
    article_type_filter = (request.GET.get("article_type") or "").strip()
    journal_filter = (request.GET.get("journal") or "").strip()
    can_view_chair_submissions = _can_access_chair_submissions(request.user)
    if owner == "chair" and can_view_chair_submissions:
        base_queryset = _get_chair_submissions_queryset(request.user)
        list_title = "Материалы моей кафедры"
        list_description = "Все материалы, отправленные сотрудниками вашей кафедры."
    else:
        owner = "me"
        base_queryset = _get_personal_submissions_queryset(request.user)
        list_title = "Мои материалы"
        list_description = "Все ваши материалы и их текущий статус в маршруте согласования."

    draft_statuses = [
        SubmissionStatus.DRAFT,
        SubmissionStatus.AUTO_CHECKING,
        SubmissionStatus.SUBMITTED,
        SubmissionStatus.REVISION_REQUESTED,
    ]
    work_statuses = [
        SubmissionStatus.IN_REVIEW,
        SubmissionStatus.APPEAL_PENDING,
    ]

    scope_filters = {
        "drafts": Q(status__in=draft_statuses),
        "work": Q(status__in=work_statuses),
        # Старые адреса разделов остаются рабочими для сохранённых ссылок.
        "submitted": Q(
            status__in=[
                SubmissionStatus.REVISION_REQUESTED,
                SubmissionStatus.APPEAL_PENDING,
            ]
        ),
        "in_review": Q(status=SubmissionStatus.IN_REVIEW),
        "history": Q(status__in=[SubmissionStatus.APPROVED, SubmissionStatus.REJECTED]),
    }
    queryset = base_queryset.order_by("-updated_at", "-pk")
    if scope in scope_filters:
        queryset = queryset.filter(scope_filters[scope])
    else:
        scope = "all"

    if search_query:
        search_filter = Q()
        # SQLite не выполняет регистронезависимый LIKE для кириллицы, поэтому
        # добавляем типичные варианты регистра; на PostgreSQL они безвредны.
        search_values = {
            search_query,
            search_query.capitalize(),
            search_query.title(),
            search_query.upper(),
        }
        for search_value in search_values:
            search_filter |= (
                Q(title__icontains=search_value)
                | Q(journal__name__icontains=search_value)
                | Q(publication_topic__name__icontains=search_value)
                | Q(article_type__name__icontains=search_value)
                | Q(authors__first_name__icontains=search_value)
                | Q(authors__last_name__icontains=search_value)
                | Q(authors__username__icontains=search_value)
            )
        queryset = queryset.filter(search_filter).distinct()
    if article_type_filter.isdigit():
        queryset = queryset.filter(article_type_id=int(article_type_filter))
    else:
        article_type_filter = ""
    if journal_filter.isdigit():
        queryset = queryset.filter(journal_id=int(journal_filter))
    else:
        journal_filter = ""

    if owner == "me":
        queryset = queryset.annotate(
            can_delete_draft=Case(
                When(
                    author=request.user,
                    status__in=_DELETABLE_DRAFT_STATUSES,
                    then=Value(True),
                ),
                default=Value(False),
                output_field=BooleanField(),
            )
        )
    else:
        queryset = queryset.annotate(
            can_delete_draft=Value(False, output_field=BooleanField())
        )

    counts = {
        "all": base_queryset.count(),
        "drafts": base_queryset.filter(status__in=draft_statuses).count(),
        "work": base_queryset.filter(status__in=work_statuses).count(),
        "submitted": base_queryset.filter(
            status__in=[
                SubmissionStatus.REVISION_REQUESTED,
                SubmissionStatus.APPEAL_PENDING,
            ]
        ).count(),
        "in_review": base_queryset.filter(status=SubmissionStatus.IN_REVIEW).count(),
        "history": base_queryset.filter(
            status__in=[SubmissionStatus.APPROVED, SubmissionStatus.REJECTED]
        ).count(),
    }

    article_type_options = list(
        base_queryset.values("article_type_id", "article_type__name")
        .order_by("article_type__name", "article_type_id")
        .distinct()
    )
    journal_options = list(
        base_queryset.filter(journal__isnull=False)
        .values("journal_id", "journal__name")
        .order_by("journal__name", "journal_id")
        .distinct()
    )
    return render(
        request,
        "submissions/list.html",
        {
        "submissions": queryset,
        "current_scope": scope,
        "current_owner": owner,
        "can_view_chair_submissions": can_view_chair_submissions,
        "scope_counts": counts,
        "list_title": list_title,
        "list_description": list_description,
        "search_query": search_query,
        "article_type_filter": article_type_filter,
        "journal_filter": journal_filter,
        "article_type_options": article_type_options,
        "journal_options": journal_options,
        "has_active_filters": bool(search_query or article_type_filter or journal_filter),
        "result_count": queryset.count(),
    },
    )


@login_required
@require_POST
def delete_submission_draft_view(request, pk):
    submission = get_object_or_404(Submission, pk=pk, author=request.user)
    if submission.status not in _DELETABLE_DRAFT_STATUSES:
        messages.error(
            request,
            "Удалить можно только материал, который ещё не отправлен в маршрут согласования.",
        )
        return redirect("submissions:detail", pk=submission.pk)

    stored_files = [
        (version.file.storage, version.file.name)
        for version in submission.versions.all()
        if version.file and version.file.name
    ]
    submission_title = submission.title
    submission_id = submission.pk
    submission.delete()

    for storage, file_name in stored_files:
        try:
            storage.delete(file_name)
        except Exception:
            logger.warning(
                "Не удалось удалить файл версии удалённого черновика #%s.",
                submission_id,
                exc_info=True,
            )

    messages.success(request, f'Черновик «{submission_title}» удалён.')
    return_scope = request.POST.get("return_scope", "all")
    if return_scope not in {"all", "drafts"}:
        return_scope = "all"
    return redirect(f"{reverse('submissions:list')}?owner=me&scope={return_scope}")


@login_required
def submission_create(request):
    if request.method == "POST":
        form = SubmissionCreateForm(request.POST, request.FILES, current_user=request.user)
        if form.is_valid():
            article_type = form.cleaned_data["article_type"]
            journal = form.cleaned_data["journal"]
            publication_topic = form.cleaned_data["publication_topic"]
            if journal is None and publication_topic is None:
                publication_topic, _created = resolve_or_create_publication_topic(
                    form.cleaned_data["publication_topic_query"],
                    created_by=request.user,
                )

            formatting_template = form.cleaned_data["formatting_template"]
            uploaded_template = form.cleaned_data["formatting_template_file"]
            if uploaded_template is not None:
                formatting_template = create_formatting_template(
                    article_type=article_type,
                    uploaded_by=request.user,
                    file=uploaded_template,
                    journal=journal,
                    publication_topic=publication_topic,
                )
                process_formatting_template(formatting_template)

            rules_snapshot = build_rules_snapshot(
                article_type=article_type,
                template=formatting_template,
                journal=journal,
            )
            uploaded_material = form.cleaned_data["file"]
            document_snapshot = analyze_document_bytes(
                read_file_bytes(uploaded_material),
                uploaded_material.name,
            )
            metadata = document_snapshot.get("metadata") or {}
            title = (
                form.cleaned_data["title"].strip()
                or str(metadata.get("title") or "").strip()
                or Path(uploaded_material.name).stem
            )
            submission = create_submission_with_initial_version(
                author=request.user,
                title=title,
                abstract=form.cleaned_data["abstract"] or metadata.get("abstract", ""),
                journal=journal,
                publication_topic=publication_topic,
                article_type=article_type,
                formatting_template=formatting_template,
                formatting_rules_snapshot=rules_snapshot,
                formatting_check_requested=form.cleaned_data["formatting_check_requested"],
                file=uploaded_material,
                comment=form.cleaned_data["version_comment"],
                co_authors=form.cleaned_data["co_authors"],
                document_authors=form.cleaned_data["document_authors"] or metadata.get("document_authors", ""),
                organizations=form.cleaned_data["organizations"] or metadata.get("organizations", ""),
                contact_emails=form.cleaned_data["contact_emails"] or metadata.get("contact_emails", ""),
                keywords=form.cleaned_data["keywords"] or metadata.get("keywords", ""),
            )
            submission.refresh_from_db()
            if submission.status == SubmissionStatus.AUTO_CHECKING:
                messages.success(
                    request,
                    "Заявка загружена. Автопроверки уже запущены в фоне, результаты появятся на странице автоматически.",
                )
            elif submission.status == SubmissionStatus.SUBMITTED:
                messages.success(
                    request,
                    "Заявка загружена и прошла автопроверки. Теперь её можно отправить в согласование.",
                )
            else:
                messages.success(
                    request,
                    "Заявка загружена. Автопроверки нашли рекомендации, но они не блокируют отправку.",
                )
            return redirect("submissions:detail", pk=submission.pk)
    else:
        form = SubmissionCreateForm(current_user=request.user)

    return render(request, "submissions/create.html", {"form": form})


@login_required
@require_POST
def extract_submission_metadata_view(request):
    uploaded_file = request.FILES.get("file")
    if uploaded_file is None:
        return JsonResponse({"error": "Файл не передан."}, status=400)
    maximum_size = int(getattr(settings, "SUBMISSION_FILE_MAX_SIZE", 50 * 1024 * 1024))
    if uploaded_file.size > maximum_size:
        return JsonResponse(
            {"error": f"Файл превышает лимит {round(maximum_size / 1024 / 1024)} МБ."},
            status=400,
        )
    snapshot = analyze_document_bytes(read_file_bytes(uploaded_file), uploaded_file.name)
    metadata = snapshot.get("metadata") or {}
    users = list(User.objects.select_related("org_unit").filter(is_active=True))
    matches = match_authors_to_users(metadata.get("authors") or [], users)
    for match in matches:
        match["is_current_user"] = match["user_id"] == request.user.id
    return JsonResponse(
        {
            "metadata": metadata,
            "matched_users": matches,
            "analysis": {
                "file_name": snapshot.get("file_name"),
                "parse_error": snapshot.get("parse_error"),
                "images": snapshot.get("image_count", 0),
                "tables": len(snapshot.get("tables") or []),
            },
        }
    )


@login_required
def submission_detail(request, pk):
    submission = get_object_or_404(
        Submission.objects.select_related(
            "author",
            "journal",
            "publication_topic",
            "article_type",
            "formatting_template",
            "direction",
            "route_template",
            "current_version",
        ).prefetch_related(
            "authors",
            "versions__uploaded_by",
            "check_runs__check_definition",
            Prefetch(
                "workflow_runs",
                queryset=WorkflowRun.objects.select_related("route_template", "current_step").prefetch_related(
                    "steps__tasks__assigned_user",
                    "steps__tasks__assigned_group",
                    "steps__tasks__assigned_unit",
                    Prefetch(
                        "steps__tasks__decisions",
                        queryset=TaskDecision.objects.select_related("actor"),
                    ),
                ),
            ),
        ),
        pk=pk,
    )
    if not _can_view_submission(request.user, submission):
        raise Http404

    workflow_runs = list(submission.workflow_runs.all())
    versions = list(submission.versions.all())
    for version in versions:
        version.preview_kind = get_preview_kind(version.file.name)
        version.preview_available = version.preview_kind is not None
        version.display_filename = get_display_filename(version.file.name)
    check_runs = list(submission.check_runs.all())
    check_entries = _build_check_entries(submission, check_runs)
    recommendation_run = _get_latest_check_run_for_current_version(
        submission,
        check_runs,
        "article_recommendations",
    )
    recommended_articles = []
    citation_claims = []
    recommendation_message = ""
    if recommendation_run is not None:
        recommendation_payload = recommendation_run.result_payload or {}
        recommended_articles = [
            item
            for item in recommendation_payload.get("recommendations", [])
            if int(item.get("score_percent") or 0) > 0
            and item.get("verdict") != "not_supports"
        ]
        citation_claims = recommendation_payload.get("citation_claims", [])
        if not citation_claims:
            citation_claims = (
                recommendation_payload.get("details", {}).get("citation_claims", [])
            )
        recommendation_message = recommendation_payload.get("message", "")
    appeal = _get_submission_appeal_or_none(submission)
    for run in workflow_runs:
        ordered_steps = list(run.steps.all())
        run_commentary_entries = []
        for index, step in enumerate(ordered_steps):
            step_tasks = sorted(step.tasks.all(), key=_task_sort_key)
            latest_task = step_tasks[-1] if step_tasks else None
            assignment_parts = _build_assignment_parts(step, submission)
            step.history_entries = _build_step_history_entries(step, submission)
            latest_history_entry = step.history_entries[-1] if step.history_entries else None
            run_commentary_entries.extend(step.history_entries)

            step.route_stage_title = step.name
            step.route_state_text = _get_route_state_text(step)
            step.has_decision = latest_history_entry is not None and latest_history_entry["tone"] != "active"
            step.is_currently_active = step.status == "active"
            step.is_waiting = step.status == "pending"
            step.assignment_short_text = assignment_parts["short"]
            step.assignment_full_text = assignment_parts["full"]
            step.assignment_meta_text = assignment_parts["meta"]

            if latest_history_entry is not None:
                step.route_user_text = latest_history_entry["actor"]
                if step.assignment_meta_text:
                    step.route_meta_text = step.assignment_meta_text
                elif step.assigned_user_id:
                    step.route_meta_text = ""
                else:
                    step.route_meta_text = step.assignment_short_text
            else:
                step.route_user_text = step.assignment_short_text
                step.route_meta_text = step.assignment_meta_text

        run.commentary_entries = sorted(
            run_commentary_entries,
            key=lambda entry: (entry["date"], entry["step_order"], entry["attempt_number"]),
            reverse=True,
        )
        run.ordered_steps = ordered_steps
    primary_workflow_run = workflow_runs[0] if workflow_runs else None
    conclusion_document = (
        ConclusionDocument.objects.filter(submission=submission)
        .prefetch_related("signatures__signer", "signatures__submission_version")
        .first()
    )
    upload_form = None
    submit_form = None
    appeal_form = None
    appeal_approve_form = None
    appeal_reject_form = None
    appeal_action_state = None
    route_review_form = None
    route_review_task = _get_active_route_review_task_for_submission(submission, request.user)
    active_review_tasks = (
        ApprovalTask.objects.filter(
            workflow_step__workflow_run__submission=submission,
            status=ApprovalTaskStatus.ACTIVE,
        )
        .filter(_task_visibility_filter(request.user))
        .select_related("workflow_step")
    )
    if route_review_task is not None:
        active_review_tasks = active_review_tasks.exclude(pk=route_review_task.pk)
    active_review_task = active_review_tasks.order_by("workflow_step__order", "pk").first()
    route_suggestion = None
    route_preview_templates = []
    route_preview_templates_by_direction = {}
    route_review_preview_payload = None
    selected_route_preview_template = None
    can_edit = submission.author_id == request.user.id or request.user.is_superuser
    formatting_check_entry = next(
        (entry for entry in check_entries if entry["code"] == "formatting_compliance"),
        None,
    )
    can_generate_corrected_document = bool(
        can_edit
        and submission.status in _CORRECTED_VERSION_CREATION_STATUSES
        and submission.current_version_id
        and Path(submission.current_version.file.name).suffix.casefold() == ".docx"
        and submission.formatting_template_id
        and (submission.formatting_rules_snapshot or {}).get("effective")
    )
    document_template_plan = None
    document_template_plan_error = ""
    if (
        submission.current_version_id
        and Path(submission.current_version.file.name).suffix.casefold() == ".docx"
        and submission.formatting_template_id
        and (submission.formatting_rules_snapshot or {}).get("effective")
    ):
        try:
            document_template_plan = build_document_template_plan(submission)
        except FormattingCorrectionError as exc:
            document_template_plan_error = str(exc)
    formatting_rules_form = None
    can_edit_formatting_rules = can_edit and submission.status in _DELETABLE_DRAFT_STATUSES
    if can_edit_formatting_rules:
        formatting_rules_form = FormattingRulesForm.from_snapshot(
            submission.formatting_rules_snapshot
        )
    can_review_route = route_review_task is not None
    if can_edit and submission.status == SubmissionStatus.REVISION_REQUESTED:
        upload_form = SubmissionVersionUploadForm()
    if can_edit and submission.status == SubmissionStatus.SUBMITTED:
        route_suggestion = ensure_submission_route_suggestion(submission)
    elif can_review_route and submission.route_template_id is not None:
        route_review_form = SubmissionSubmitForm(
            current_direction=submission.direction,
            current_route_template=submission.route_template,
            current_article_type=submission.article_type,
        )
        route_preview_templates_by_direction = _build_route_preview_templates_by_direction(
            article_type=submission.article_type,
        )
        route_preview_templates = route_preview_templates_by_direction.get(str(submission.direction_id or ""), [])
        selected_route_preview_template = _pick_route_preview_template(
            route_preview_templates_by_direction,
            direction=submission.direction,
            route_template=submission.route_template,
        )
        route_review_preview_payload = {
            "previewsByDirection": route_preview_templates_by_direction,
            "initialDirectionId": str(submission.direction_id or ""),
            "initialRouteTemplateId": str(submission.route_template_id or ""),
        }
    elif submission.route_template_id is not None:
        route_preview_templates = _build_route_preview_templates(
            article_type=submission.article_type,
            direction=submission.direction,
            route_template=submission.route_template,
        )
        if route_preview_templates:
            selected_route_preview_template = route_preview_templates[0]
    is_route_approval_pending = bool(primary_workflow_run and primary_workflow_run.awaiting_route_approval)
    can_view_route_details = request.user.is_superuser or can_review_route or (
        submission.status not in {SubmissionStatus.AUTO_CHECKING, SubmissionStatus.SUBMITTED}
        and not is_route_approval_pending
        and submission.route_template_id is not None
    )
    if can_edit and submission.status == SubmissionStatus.REJECTED and appeal is None:
        appeal_form = SubmissionAppealForm()
    if appeal is not None:
        appeal_action_state = get_appeal_action_state(appeal, request.user)
        if appeal_action_state["can_act"]:
            appeal_approve_form = SubmissionAppealDecisionForm(prefix="appeal_approve")
            appeal_reject_form = SubmissionAppealDecisionForm(
                prefix="appeal_reject",
                require_comment=True,
            )

    return render(
        request,
        "submissions/detail.html",
        {
            "submission": submission,
            "workflow_runs": workflow_runs,
            "primary_workflow_run": primary_workflow_run,
            "conclusion_document": conclusion_document,
            "versions": versions,
            "check_runs": check_runs,
            "check_entries": check_entries,
            "recommendation_run": recommendation_run,
            "recommended_articles": recommended_articles,
            "citation_claims": citation_claims,
            "recommendation_message": recommendation_message,
            "appeal": appeal,
            "upload_form": upload_form,
            "submit_form": submit_form,
            "appeal_form": appeal_form,
            "appeal_approve_form": appeal_approve_form,
            "appeal_reject_form": appeal_reject_form,
            "appeal_action_state": appeal_action_state,
            "route_suggestion": route_suggestion,
            "route_review_form": route_review_form,
            "route_review_task": route_review_task,
            "active_review_task": active_review_task,
            "route_preview_templates": route_preview_templates,
            "route_preview_templates_by_direction": route_preview_templates_by_direction,
            "route_review_preview_payload": route_review_preview_payload,
            "selected_route_preview_template": selected_route_preview_template,
            "is_auto_checking": submission.status == SubmissionStatus.AUTO_CHECKING,
            "is_route_approval_pending": is_route_approval_pending,
            "status_tone": _get_submission_status_tone(submission.status),
            "progress_poll_interval_ms": settings.SUBMISSION_PROGRESS_POLL_INTERVAL_MS,
            "can_edit": can_edit,
            "formatting_rules_form": formatting_rules_form,
            "formatting_rules": (submission.formatting_rules_snapshot or {}).get("effective") or {},
            "formatting_rule_rows": _build_formatting_rule_rows(
                submission.formatting_rules_snapshot
            ),
            "formatting_rule_conflicts": (submission.formatting_rules_snapshot or {}).get("conflicts") or [],
            "can_edit_formatting_rules": can_edit_formatting_rules,
            "formatting_check_entry": formatting_check_entry,
            "can_generate_corrected_document": can_generate_corrected_document,
            "document_template_plan": document_template_plan,
            "document_template_plan_error": document_template_plan_error,
            "can_review_route": can_review_route,
            "can_view_route_details": can_view_route_details,
            "can_submit": can_edit
            and submission.status == SubmissionStatus.SUBMITTED
            and submission.current_version_id is not None,
            "route_selection_ready": route_suggestion is not None,
        },
    )


@login_required
def submission_conclusion_document_download(request, pk, conclusion_pk):
    document = _get_viewable_conclusion_or_404(request.user, pk, conclusion_pk)
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


@login_required
def submission_conclusion_package_file_download(request, pk, conclusion_pk, file_kind):
    document = _get_viewable_conclusion_or_404(request.user, pk, conclusion_pk)
    package_files = {
        "source": (document.source_pdf_file, "application/pdf"),
        "printed": (document.printed_pdf_file, "application/pdf"),
        "signatures": (document.signature_data_file, "application/xml"),
    }
    file_entry = package_files.get(file_kind)
    if not document.package_finalized_at or file_entry is None:
        raise Http404
    file_field, content_type = file_entry
    if not file_field:
        raise Http404
    try:
        source = file_field.open("rb")
    except OSError as exc:
        raise Http404 from exc
    return FileResponse(
        source,
        as_attachment=True,
        filename=file_field.name.rsplit("/", maxsplit=1)[-1],
        content_type=content_type,
    )


def _get_viewable_submission_version(request, pk, version_pk):
    version = get_object_or_404(
        SubmissionVersion.objects.select_related("submission", "uploaded_by"),
        pk=version_pk,
        submission_id=pk,
    )
    if not _can_view_submission(request.user, version.submission):
        raise Http404
    return version


@login_required
def submission_version_preview(request, pk, version_pk):
    version = _get_viewable_submission_version(request, pk, version_pk)
    preview_kind = get_preview_kind(version.file.name)
    if preview_kind is None:
        raise Http404

    preview_error = ""
    rendered_preview_kind = preview_kind
    text_content = ""
    document_blocks = []
    is_truncated = False
    try:
        if preview_kind == "text":
            text_content, is_truncated = read_text_preview(version.file)
        elif preview_kind == "docx":
            if getattr(settings, "DOCUMENT_PREVIEW_CONVERT_DOCX_TO_PDF", True):
                try:
                    build_word_document_pdf(version)
                    rendered_preview_kind = "word_pdf"
                except DocumentPreviewError:
                    document_blocks, is_truncated = read_docx_preview(version.file)
            else:
                document_blocks, is_truncated = read_docx_preview(version.file)
        elif preview_kind == "legacy_doc":
            build_legacy_doc_pdf(version)
    except (DocumentPreviewError, OSError) as exc:
        preview_error = str(exc) or "Не удалось открыть файл для просмотра."

    return render(
        request,
        "submissions/version_preview.html",
        {
            "submission": version.submission,
            "version": version,
            "preview_kind": rendered_preview_kind,
            "preview_error": preview_error,
            "text_content": text_content,
            "document_blocks": document_blocks,
            "is_truncated": is_truncated,
            "display_filename": get_display_filename(version.file.name),
            "preview_label": "DOC" if preview_kind == "legacy_doc" else preview_kind.upper(),
        },
    )


@login_required
@xframe_options_sameorigin
def submission_version_content(request, pk, version_pk):
    version = _get_viewable_submission_version(request, pk, version_pk)
    preview_kind = get_preview_kind(version.file.name)
    if preview_kind not in {"pdf", "legacy_doc", "docx"}:
        raise Http404
    try:
        if preview_kind == "legacy_doc":
            source = build_legacy_doc_pdf(version).open("rb")
            filename = f"{get_display_filename(version.file.name)}.pdf"
        elif preview_kind == "docx" and getattr(settings, "DOCUMENT_PREVIEW_CONVERT_DOCX_TO_PDF", True):
            source = build_word_document_pdf(version).open("rb")
            filename = f"{get_display_filename(version.file.name)}.pdf"
        elif preview_kind == "docx":
            raise Http404
        else:
            source = version.file.open("rb")
            filename = get_display_filename(version.file.name)
    except (DocumentPreviewError, OSError) as exc:
        raise Http404 from exc
    response = FileResponse(
        source,
        as_attachment=False,
        filename=filename,
        content_type="application/pdf",
    )
    patch_cache_control(response, private=True, no_store=True)
    return response


@login_required
def submission_version_download(request, pk, version_pk):
    version = _get_viewable_submission_version(request, pk, version_pk)
    try:
        source = version.file.open("rb")
    except OSError as exc:
        raise Http404 from exc
    return FileResponse(
        source,
        as_attachment=True,
        filename=get_display_filename(version.file.name),
        content_type="application/octet-stream",
    )


@login_required
@require_POST
def update_formatting_rules_view(request, pk):
    submission = get_object_or_404(
        Submission.objects.select_related("author", "formatting_template"),
        pk=pk,
    )
    if submission.author_id != request.user.id and not request.user.is_superuser:
        return HttpResponseForbidden("Недостаточно прав.")
    if submission.status not in _DELETABLE_DRAFT_STATUSES:
        messages.error(request, "Правила можно уточнить только до запуска маршрута согласования.")
        return redirect("submissions:detail", pk=submission.pk)

    form = FormattingRulesForm.from_snapshot(
        submission.formatting_rules_snapshot,
        request.POST,
    )
    if not form.is_valid():
        messages.error(request, "Не удалось сохранить правила. Проверьте введённые значения.")
        return redirect("submissions:detail", pk=submission.pk)

    submission.formatting_rules_snapshot = form.apply_to_snapshot(
        submission.formatting_rules_snapshot
    )
    submission.formatting_check_requested = True
    submission.save(
        update_fields=[
            "formatting_rules_snapshot",
            "formatting_check_requested",
            "updated_at",
        ]
    )
    queue_submission_checks(submission)
    messages.success(request, "Правила уточнены. Автопроверки запущены повторно.")
    return redirect("submissions:detail", pk=submission.pk)


@login_required
def submission_latex_template_download_view(request, pk):
    submission = _get_viewable_submission_or_404(request.user, pk)
    rules = (submission.formatting_rules_snapshot or {}).get("effective") or {}
    if not rules:
        messages.error(
            request,
            "LaTeX-шаблон пока нельзя сформировать: для работы не сохранены правила оформления.",
        )
        return redirect("submissions:detail", pk=submission.pk)
    source = build_latex_template(
        rules,
        metadata={
            "title": submission.title,
            "authors": submission.document_authors or submission.get_authors_display(),
            "institution": submission.organizations,
            "abstract": submission.abstract,
            "keywords": submission.keywords,
        },
    )
    response = HttpResponse(source, content_type="application/x-tex; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="submission-{submission.pk}-template.tex"'
    )
    return response


@login_required
def corrected_document_download_view(request, pk):
    submission = get_object_or_404(
        Submission.objects.select_related(
            "author",
            "current_version",
            "formatting_template",
        ),
        pk=pk,
    )
    if submission.author_id != request.user.id and not request.user.is_superuser:
        return HttpResponseForbidden("Исправленный файл доступен только автору.")
    try:
        corrected_bytes, changes = build_corrected_docx(submission)
    except FormattingCorrectionError as exc:
        messages.error(request, str(exc))
        return redirect("submissions:detail", pk=submission.pk)

    response = HttpResponse(
        corrected_bytes,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="submission-{submission.id}-template-edited.docx"'
    )
    response["X-Template-Engine-Changes"] = str(len(changes))
    return response


def _get_correctable_submission(request, pk):
    submission = get_object_or_404(
        Submission.objects.select_related(
            "author",
            "current_version",
            "formatting_template",
        ),
        pk=pk,
    )
    if submission.author_id != request.user.id and not request.user.is_superuser:
        raise PermissionDenied("Исправленный файл доступен только автору.")
    return submission


def _corrected_source_is_current(submission, version_pk):
    return bool(
        submission.current_version_id
        and submission.current_version_id == version_pk
    )


@login_required
def corrected_document_preview_view(request, pk, version_pk):
    submission = _get_correctable_submission(request, pk)
    if not _corrected_source_is_current(submission, version_pk):
        messages.warning(
            request,
            "Текущая версия материала изменилась. Откройте исправленный документ заново.",
        )
        return redirect("submissions:detail", pk=submission.pk)
    if submission.status not in _CORRECTED_VERSION_CREATION_STATUSES:
        messages.warning(
            request,
            "Сейчас нельзя создать новую версию. Дождитесь завершения текущей проверки.",
        )
        return redirect("submissions:detail", pk=submission.pk)

    try:
        _corrected_bytes, _changes = build_corrected_docx(submission)
    except FormattingCorrectionError as exc:
        messages.error(request, str(exc))
        return redirect("submissions:detail", pk=submission.pk)

    return render(
        request,
        "submissions/corrected_document_preview.html",
        {
            "submission": submission,
            "source_version": submission.current_version,
            "display_filename": f"submission-{submission.id}-template-edited.docx",
        },
    )


@login_required
@xframe_options_sameorigin
def corrected_document_preview_content_view(request, pk, version_pk):
    submission = _get_correctable_submission(request, pk)
    if not _corrected_source_is_current(submission, version_pk):
        raise Http404
    try:
        corrected_bytes, _changes = build_corrected_docx(submission)
        preview_bytes = build_docx_bytes_pdf(corrected_bytes)
    except (FormattingCorrectionError, DocumentPreviewError, OSError) as exc:
        raise Http404 from exc

    response = HttpResponse(preview_bytes, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'inline; filename="submission-{submission.id}-template-edited.pdf"'
    )
    patch_cache_control(response, private=True, no_store=True)
    return response


@login_required
@require_POST
def submit_corrected_document_for_check_view(request, pk, version_pk):
    submission = _get_correctable_submission(request, pk)
    if not _corrected_source_is_current(submission, version_pk):
        messages.warning(
            request,
            "Исправленный документ не отправлен: текущая версия материала уже изменилась.",
        )
        return redirect("submissions:detail", pk=submission.pk)
    if submission.status not in _CORRECTED_VERSION_CREATION_STATUSES:
        messages.error(
            request,
            "Исправленный документ нельзя отправить, пока выполняется другая проверка.",
        )
        return redirect("submissions:detail", pk=submission.pk)

    try:
        corrected_bytes, _changes = build_corrected_docx(submission)
        version = add_submission_version(
            submission=submission,
            uploaded_by=request.user,
            file=ContentFile(
                corrected_bytes,
                name=f"submission-{submission.id}-template-edited.docx",
            ),
            comment="Версия создана системой по выбранному шаблону оформления.",
            expected_current_version_id=version_pk,
        )
    except (FormattingCorrectionError, PermissionError, ValueError) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            f"Исправленная версия v{version.version_number} создана и отправлена на проверку.",
        )
    return redirect("submissions:detail", pk=submission.pk)


@login_required
def upload_submission_version(request, pk):
    submission = get_object_or_404(Submission, pk=pk)
    if submission.author_id != request.user.id and not request.user.is_superuser:
        return HttpResponseForbidden("Недостаточно прав.")
    if request.method != "POST":
        return redirect("submissions:detail", pk=submission.pk)

    form = SubmissionVersionUploadForm(request.POST, request.FILES)
    if form.is_valid():
        try:
            version = add_submission_version(
                submission=submission,
                uploaded_by=request.user,
                file=form.cleaned_data["file"],
                comment=form.cleaned_data["comment"],
            )
        except (PermissionError, ValueError) as exc:
            messages.error(request, str(exc))
        else:
            submission.refresh_from_db()
            if submission.status == SubmissionStatus.AUTO_CHECKING:
                messages.success(
                    request,
                    f"Новая версия v{version.version_number} загружена. Автопроверки уже запущены в фоне, результаты появятся на странице автоматически.",
                )
            elif submission.status == SubmissionStatus.IN_REVIEW:
                messages.success(
                    request,
                    f"Новая версия v{version.version_number} загружена, прошла автопроверки и автоматически возвращена в согласование.",
                )
            elif submission.status == SubmissionStatus.SUBMITTED:
                messages.success(
                    request,
                    f"Новая версия v{version.version_number} загружена и прошла автопроверки. Можно отправлять в согласование.",
                )
            else:
                messages.warning(
                    request,
                    f"Новая версия v{version.version_number} загружена, но автопроверки нашли замечания.",
                )
    else:
        messages.error(request, "Не удалось загрузить новую версию. Проверьте форму.")
    return redirect("submissions:detail", pk=submission.pk)


@login_required
def submit_submission_view(request, pk):
    submission = get_object_or_404(Submission, pk=pk)
    if submission.author_id != request.user.id and not request.user.is_superuser:
        return HttpResponseForbidden("Недостаточно прав.")
    if request.method != "POST":
        return redirect("submissions:detail", pk=submission.pk)

    try:
        submit_submission(
            submission,
            submitted_by=request.user,
        )
    except (PermissionError, ValueError) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            "Заявка отправлена заведующему кафедрой для проверки маршрута и дальнейшего запуска согласования.",
        )
    return redirect("submissions:detail", pk=submission.pk)


@login_required
def update_submission_route_view(request, pk):
    submission = get_object_or_404(Submission, pk=pk)
    if not _can_review_submission_route(request.user, submission):
        return HttpResponseForbidden("Недостаточно прав.")
    if request.method != "POST":
        return redirect("submissions:detail", pk=submission.pk)

    form = SubmissionSubmitForm(
        request.POST,
        current_direction=submission.direction,
        current_route_template=submission.route_template,
        current_article_type=submission.article_type,
    )
    if not form.is_valid():
        messages.error(request, "Не удалось подтвердить маршрут. Проверьте выбранную область и шаблон.")
        return redirect("submissions:detail", pk=submission.pk)

    try:
        confirm_submission_route_before_launch(
            submission,
            actor=request.user,
            direction=form.cleaned_data["direction"],
            route_template=form.cleaned_data["route_template"],
        )
    except (PermissionError, ValueError) as exc:
        messages.error(request, str(exc))
    except OperationalError as exc:
        if "locked" not in str(exc).lower():
            raise
        messages.error(
            request,
            "База данных занята фоновой проверкой. Подождите несколько секунд и подтвердите маршрут ещё раз.",
        )
    else:
        messages.success(request, "Маршрут подтвержден. Материал отправлен на следующий этап согласования.")
    return redirect("submissions:detail", pk=submission.pk)


@login_required
def submission_progress_view(request, pk):
    submission = get_object_or_404(
        Submission.objects.select_related(
            "author",
            "direction",
            "route_template",
            "current_version",
        ).prefetch_related("check_runs__check_definition"),
        pk=pk,
    )
    if not _can_view_submission(request.user, submission):
        raise Http404

    check_entries = _build_check_entries(submission, list(submission.check_runs.all()))
    return JsonResponse(
        {
            "submission_id": submission.id,
            "status": submission.status,
            "status_display": submission.get_status_display(),
            "status_tone": _get_submission_status_tone(submission.status),
            "direction_name": submission.direction.name if submission.direction_id else "Не выбрано",
            "route_template_name": submission.route_template.name if submission.route_template_id else "Не выбрано",
            "is_auto_checking": submission.status == SubmissionStatus.AUTO_CHECKING,
            "check_entries": check_entries,
        }
    )


@login_required
def submission_checks_report_view(request, pk):
    submission = get_object_or_404(
        Submission.objects.select_related("author", "current_version").prefetch_related(
            "authors",
            "check_runs__check_definition",
        ),
        pk=pk,
    )
    if not _can_view_submission(request.user, submission):
        raise Http404
    runs = [
        run
        for run in submission.check_runs.all()
        if run.version_id == submission.current_version_id
    ]
    runs.sort(key=lambda run: (run.check_definition.order, run.check_definition_id))
    report = {
        "schema_version": "1.0",
        "submission_id": submission.id,
        "version_id": submission.current_version_id,
        "generated_at": timezone.now().isoformat(),
        "advisory_only": True,
        "checks": [
            {
                "code": run.check_definition.code,
                "name": run.check_definition.name,
                "status": run.status,
                "payload": run.result_payload or {},
            }
            for run in runs
        ],
    }
    response = JsonResponse(report, json_dumps_params={"ensure_ascii": False, "indent": 2})
    version_number = getattr(submission.current_version, "version_number", 0)
    response["Content-Disposition"] = (
        f'attachment; filename="submission-{submission.id}-checks-v{version_number}.json"'
    )
    return response


@login_required
def submit_submission_appeal_view(request, pk):
    submission = get_object_or_404(Submission, pk=pk)
    if submission.author_id != request.user.id and not request.user.is_superuser:
        return HttpResponseForbidden("Недостаточно прав.")
    if request.method != "POST":
        return redirect("submissions:detail", pk=submission.pk)

    form = SubmissionAppealForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, "Не удалось отправить апелляцию. Проверьте форму.")
        return redirect("submissions:detail", pk=submission.pk)

    try:
        submit_submission_appeal(
            submission,
            request.user,
            comment=form.cleaned_data["comment"],
            attachment=form.cleaned_data.get("attachment"),
        )
    except (PermissionError, ValueError) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Апелляция отправлена на повторное рассмотрение.")
    return redirect("submissions:detail", pk=submission.pk)


def _handle_submission_appeal_action(request, pk, action, *, form_prefix, require_comment, success_message):
    if request.method != "POST":
        raise Http404

    submission = get_object_or_404(Submission, pk=pk)
    appeal = _get_submission_appeal_or_none(submission)
    if appeal is None:
        raise Http404

    form = SubmissionAppealDecisionForm(
        request.POST,
        prefix=form_prefix,
        require_comment=require_comment,
    )
    if not form.is_valid():
        messages.error(request, "Не удалось сохранить решение по апелляции. Исправьте ошибки формы.")
        return redirect("submissions:detail", pk=submission.pk)

    try:
        action(appeal, request.user, comment=form.cleaned_data["comment"])
    except (PermissionError, ValueError) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, success_message)
    return redirect("submissions:detail", pk=submission.pk)


@login_required
def approve_submission_appeal_view(request, pk):
    return _handle_submission_appeal_action(
        request,
        pk,
        approve_submission_appeal,
        form_prefix="appeal_approve",
        require_comment=False,
        success_message="Апелляция принята. Заявка продолжила движение по маршруту.",
    )


@login_required
def reject_submission_appeal_view(request, pk):
    return _handle_submission_appeal_action(
        request,
        pk,
        reject_submission_appeal,
        form_prefix="appeal_reject",
        require_comment=True,
        success_message="Апелляция отклонена. Заявка окончательно остановлена.",
    )
