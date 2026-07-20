from apps.workflow.selectors import get_active_visible_tasks_queryset


def workflow_notifications(request):
    if not request.user.is_authenticated:
        return {"workflow_pending_count": 0}

    return {
        "workflow_pending_count": get_active_visible_tasks_queryset(request.user).count(),
    }
