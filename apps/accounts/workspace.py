from django.db.models import Q

from apps.accounts.access import get_user_display_name, get_user_initials, is_root_admin
from apps.accounts.roles import has_chair_head_role
from apps.activities.models import Activity, get_current_academic_year
from apps.submissions.models import Submission
from apps.workflow.models import RouteStepTemplate
from apps.workflow.selectors import (
    get_active_visible_tasks_queryset,
    get_decision_history_queryset,
)


def get_workspace_navigation(user, *, active_tasks=None, decision_history=None, plan_year=None):
    if not getattr(user, "is_authenticated", False):
        return {
            "workspace_user_name": "",
            "workspace_user_initials": "",
            "workspace_user_unit": "",
            "workspace_submission_count": 0,
            "workspace_plan_year": get_current_academic_year(),
            "workspace_pending_review_count": 0,
            "workspace_review_history_count": 0,
            "show_review_navigation": False,
            "show_root_admin_navigation": False,
        }

    if active_tasks is None:
        active_tasks = get_active_visible_tasks_queryset(user)
    if decision_history is None:
        decision_history = get_decision_history_queryset(user)

    pending_review_count = active_tasks.count()
    review_history_count = decision_history.count()
    user_group_ids = list(user.groups.values_list("id", flat=True))
    route_participation_query = Q(target_user=user)
    if user_group_ids:
        route_participation_query |= Q(target_group_id__in=user_group_ids)
        route_participation_query |= Q(
            direction_assignments__target_group_id__in=user_group_ids
        )
    participates_in_route = RouteStepTemplate.objects.filter(
        route_template__is_active=True,
    ).filter(route_participation_query).exists()

    if not plan_year:
        plan_year = (
            Activity.objects.filter(Q(owner=user) | Q(collaborators=user))
            .order_by("-academic_year")
            .values_list("academic_year", flat=True)
            .first()
            or get_current_academic_year()
        )

    return {
        "workspace_user_name": get_user_display_name(user),
        "workspace_user_initials": get_user_initials(user),
        "workspace_user_unit": getattr(
            getattr(user, "chair_org_unit", None) or getattr(user, "org_unit", None),
            "name",
            "",
        ),
        "workspace_submission_count": Submission.objects.filter(authors=user).distinct().count(),
        "workspace_plan_year": plan_year,
        "workspace_pending_review_count": pending_review_count,
        "workspace_review_history_count": review_history_count,
        "show_review_navigation": bool(
            user.is_superuser
            or has_chair_head_role(user)
            or participates_in_route
            or pending_review_count
            or review_history_count
        ),
        "show_root_admin_navigation": is_root_admin(user),
    }
