from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Max, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET

from apps.accounts.access import get_user_display_name
from apps.accounts.forms import UserRegistrationForm
from apps.accounts.roles import has_chair_head_role
from apps.activities.models import Activity, ActivityStatus, get_current_academic_year
from apps.submissions.models import Submission, SubmissionAppeal, SubmissionAppealStatus, SubmissionStatus
from apps.workflow.selectors import (
    get_active_visible_tasks_queryset,
    get_decision_history_queryset,
)
from apps.accounts.workspace import get_workspace_navigation

User = get_user_model()


@login_required
def dashboard(request):
    submissions = (
        Submission.objects.filter(authors=request.user)
        .select_related(
            "journal",
            "article_type",
            "direction",
            "route_template",
        )
        .prefetch_related("authors")
        .distinct()
    )
    active_tasks = get_active_visible_tasks_queryset(request.user).select_related(
        "assigned_group",
        "assigned_unit",
        "workflow_step__workflow_run__submission__author",
        "workflow_step__workflow_run__submission__direction",
        "workflow_step__workflow_run__route_template",
    )
    decision_history = get_decision_history_queryset(request.user).select_related(
        "workflow_step__workflow_run__submission",
        "workflow_step__workflow_run__route_template",
    )
    pending_appeals = SubmissionAppeal.objects.filter(
        status=SubmissionAppealStatus.PENDING,
        reviewer=request.user,
    ).select_related(
        "submission__author",
        "submission__journal",
        "submission__article_type",
    )
    personal_activities = (
        Activity.objects.filter(Q(owner=request.user) | Q(collaborators=request.user))
        .select_related("activity_type", "grant_type", "owner")
        .distinct()
    )
    personal_activity_year = (
        personal_activities.order_by("-academic_year").values_list("academic_year", flat=True).first()
        or get_current_academic_year()
    )
    current_personal_activities = personal_activities.filter(academic_year=personal_activity_year)

    def activity_quantity(status):
        return (
            current_personal_activities.filter(status=status).aggregate(total=Sum("quantity"))["total"]
            or 0
        )

    my_activity_total = current_personal_activities.aggregate(total=Sum("quantity"))["total"] or 0
    my_activity_planned = activity_quantity(ActivityStatus.PLANNED)
    my_activity_in_progress = activity_quantity(ActivityStatus.IN_PROGRESS)
    my_activity_completed = activity_quantity(ActivityStatus.COMPLETED)
    activity_progress_percent = (
        min(100, round((my_activity_completed / my_activity_total) * 100))
        if my_activity_total
        else 0
    )

    workspace_navigation = get_workspace_navigation(
        request.user,
        active_tasks=active_tasks,
        decision_history=decision_history,
        plan_year=personal_activity_year,
    )
    pending_task_count = workspace_navigation["workspace_pending_review_count"]
    review_history_count = workspace_navigation["workspace_review_history_count"]
    show_review_navigation = workspace_navigation["show_review_navigation"]

    attention_submissions = submissions.filter(
        status__in=[
            SubmissionStatus.REVISION_REQUESTED,
            SubmissionStatus.APPEAL_PENDING,
        ]
    )

    history_statuses = [
        SubmissionStatus.APPROVED,
        SubmissionStatus.REJECTED,
    ]
    chair_submissions = Submission.objects.none()
    can_view_chair_submissions = has_chair_head_role(request.user)
    if can_view_chair_submissions:
        chair_submissions = (
            Submission.objects.filter(
                author__chair_org_unit_id=request.user.chair_org_unit_id,
                submitted_at__isnull=False,
            )
            .select_related("author", "journal", "article_type", "direction", "route_template")
            .prefetch_related("authors")
            .distinct()
        )

    context = {
        "dashboard_name": get_user_display_name(request.user),
        "ready_count": submissions.filter(status=SubmissionStatus.SUBMITTED).count(),
        "attention_count": submissions.filter(
            status__in=[
                SubmissionStatus.REVISION_REQUESTED,
                SubmissionStatus.APPEAL_PENDING,
            ]
        ).count(),
        "in_review_count": submissions.filter(status=SubmissionStatus.IN_REVIEW).count(),
        "history_count": submissions.filter(status__in=history_statuses).count(),
        "pending_task_count": pending_task_count,
        "review_history_count": review_history_count,
        "show_review_navigation": show_review_navigation,
        "attention_submissions": attention_submissions.order_by("-updated_at", "-pk")[:4],
        "attention_count": attention_submissions.count() + pending_task_count + pending_appeals.count(),
        "pending_appeal_count": pending_appeals.count(),
        "submission_total": submissions.count(),
        "recent_submissions": submissions.order_by("-updated_at", "-pk")[:5],
        "can_view_chair_submissions": can_view_chair_submissions,
        "chair_submission_count": chair_submissions.count(),
        "chair_recent_submissions": chair_submissions.order_by("-updated_at", "-pk")[:5],
        "pending_tasks": active_tasks.order_by("activated_at", "created_at", "pk")[:5],
        "pending_appeals": pending_appeals.order_by("-created_at", "-pk")[:5],
        "personal_activity_year": personal_activity_year,
        "my_activity_total": my_activity_total,
        "my_activity_planned": my_activity_planned,
        "my_activity_in_progress": my_activity_in_progress,
        "my_activity_completed": my_activity_completed,
        "activity_progress_percent": activity_progress_percent,
        "activity_remaining": max(my_activity_total - my_activity_completed, 0),
        "recent_personal_activities": current_personal_activities.order_by(
            "activity_type__name", "title", "pk"
        )[:6],
    }
    context.update(workspace_navigation)
    return render(request, "accounts/dashboard.html", context)


@login_required
def author_directory(request):
    authors = (
        User.objects.filter(authored_submissions__isnull=False)
        .select_related("org_unit", "position")
        .annotate(
            submission_count=Count("authored_submissions", distinct=True),
            sent_submission_count=Count(
                "authored_submissions",
                filter=Q(authored_submissions__submitted_at__isnull=False),
                distinct=True,
            ),
            approved_submission_count=Count(
                "authored_submissions",
                filter=Q(authored_submissions__status=SubmissionStatus.APPROVED),
                distinct=True,
            ),
            latest_submission_at=Max("authored_submissions__submitted_at"),
        )
        .order_by("-approved_submission_count", "-sent_submission_count", "last_name", "first_name", "username")
        .distinct()
    )
    return render(request, "accounts/authors.html", {"authors": authors})


@login_required
@require_GET
def author_profile(request, pk):
    if pk is None:
        pk = request.user.id
    author = get_object_or_404(
        User.objects.select_related("org_unit", "position", "chair_org_unit"),
        pk=pk,
    )
    is_own_profile = author.id == request.user.id

    submissions = (
        Submission.objects.filter(authors=author)
        .select_related("journal", "article_type", "direction", "route_template", "current_version")
        .prefetch_related("authors")
        .order_by("-updated_at", "-pk")
        .distinct()
    )
    sent_submissions = submissions.filter(submitted_at__isnull=False).order_by(
        "-submitted_at",
        "-updated_at",
        "-pk",
    )
    approved_submissions = sent_submissions.filter(status=SubmissionStatus.APPROVED)
    publication_plan = getattr(author, "publication_plan", None)
    plan_items = publication_plan.items.all().order_by("order") if publication_plan else []

    context = {
        "author": author,
        "is_own_profile": is_own_profile,
        "submission_count": submissions.count(),
        "sent_submission_count": sent_submissions.count(),
        "approved_submission_count": approved_submissions.count(),
        "latest_submission_at": sent_submissions.aggregate(latest=Max("submitted_at"))["latest"],
        "sent_submissions": sent_submissions,
        "approved_submissions": approved_submissions,
        "plan_items": plan_items,
    }
    return render(request, "accounts/profile.html", context)


def register(request):
    if request.user.is_authenticated:
        return redirect("home")

    if request.method == "POST":
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(
                request,
                "Учетная запись создана. Получите единый временный пароль у администратора.",
            )
            return redirect("login")
    else:
        form = UserRegistrationForm()

    return render(request, "registration/register.html", {"form": form})
