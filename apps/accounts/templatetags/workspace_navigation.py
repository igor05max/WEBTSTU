from django import template

from apps.accounts.workspace import get_workspace_navigation


register = template.Library()

NAVIGATION_KEYS = (
    "workspace_user_name",
    "workspace_user_initials",
    "workspace_user_unit",
    "workspace_submission_count",
    "workspace_plan_year",
    "workspace_pending_review_count",
    "workspace_review_history_count",
    "show_review_navigation",
    "show_root_admin_navigation",
)


@register.inclusion_tag("accounts/_workspace_sidebar.html", takes_context=True)
def workspace_sidebar(context, active_section):
    request = context["request"]
    if all(key in context for key in NAVIGATION_KEYS):
        navigation = {key: context[key] for key in NAVIGATION_KEYS}
    else:
        navigation = get_workspace_navigation(request.user)
    return {
        "request": request,
        "active_section": active_section,
        **navigation,
    }
