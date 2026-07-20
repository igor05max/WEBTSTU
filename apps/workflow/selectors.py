from django.db.models import Q

from apps.workflow.models import ApprovalTask, ApprovalTaskStatus


def build_task_visibility_q(user):
    if not user.is_authenticated:
        return Q(pk__in=[])

    if user.is_superuser:
        return Q()

    query = Q(assigned_user=user)
    group_ids = list(user.groups.values_list("id", flat=True))

    if group_ids:
        query |= Q(
            assigned_user__isnull=True,
            assigned_group_id__in=group_ids,
            assigned_unit__isnull=True,
        )

    if group_ids and user.org_unit_id:
        query |= Q(
            assigned_user__isnull=True,
            assigned_group_id__in=group_ids,
            assigned_unit_id=user.org_unit_id,
        )

    if user.org_unit_id:
        query |= Q(
            assigned_user__isnull=True,
            assigned_group__isnull=True,
            assigned_unit_id=user.org_unit_id,
        )

    return query


def get_visible_tasks_queryset(user):
    if not user.is_authenticated:
        return ApprovalTask.objects.none()

    if user.is_superuser:
        return ApprovalTask.objects.all()

    # A reviewer must retain access to a task they have already completed,
    # even if their group membership or appointment subsequently changes.
    return ApprovalTask.objects.filter(
        build_task_visibility_q(user) | Q(decisions__actor=user)
    ).distinct()


def get_active_visible_tasks_queryset(user):
    return get_visible_tasks_queryset(user).filter(status=ApprovalTaskStatus.ACTIVE)


def get_decision_history_queryset(user):
    """Tasks for which this user has personally recorded a decision."""
    if not user.is_authenticated:
        return ApprovalTask.objects.none()

    return ApprovalTask.objects.filter(decisions__actor=user).distinct()


def filter_tasks_by_scope(queryset, user, scope):
    if scope == "personal":
        return queryset.filter(assigned_user=user)

    if scope == "unit":
        if user.org_unit_id is None:
            return queryset.none()
        return queryset.filter(
            assigned_user__isnull=True,
            assigned_group__isnull=True,
            assigned_unit_id=user.org_unit_id,
        )

    if scope.startswith("role:"):
        try:
            group_id = int(scope.split(":", maxsplit=1)[1])
        except (IndexError, ValueError):
            return queryset.none()
        if not user.groups.filter(id=group_id).exists():
            return queryset.none()
        return queryset.filter(assigned_group_id=group_id)

    return queryset


def build_inbox_tabs(user, active_queryset, current_scope):
    tabs = [
        {
            "scope": "all",
            "label": "Все входящие",
            "description": "Все активные задачи, доступные вам как пользователю, роли или группе.",
            "count": active_queryset.count(),
        }
    ]

    tabs.append(
        {
            "scope": "history",
            "label": "История решений",
            "description": "Все решения, которые вы уже зафиксировали по материалам.",
            "count": get_decision_history_queryset(user).count(),
        }
    )

    for group in user.groups.order_by("name"):
        tabs.append(
            {
                "scope": f"role:{group.id}",
                "label": group.name,
                "description": f'Задачи, где вы выступаете в роли "{group.name}".',
                "count": active_queryset.filter(assigned_group_id=group.id).count(),
            }
        )

    tabs.append(
        {
            "scope": "personal",
            "label": "Лично мне",
            "description": "Задачи, назначенные непосредственно на вас как на конкретного пользователя.",
            "count": active_queryset.filter(assigned_user=user).count(),
        }
    )

    if user.org_unit_id:
        tabs.append(
            {
                "scope": "unit",
                "label": user.org_unit.name,
                "description": f'Задачи, назначенные напрямую на группу "{user.org_unit.name}" без отдельной роли.',
                "count": active_queryset.filter(
                    assigned_user__isnull=True,
                    assigned_group__isnull=True,
                    assigned_unit_id=user.org_unit_id,
                ).count(),
            }
        )

    for tab in tabs:
        tab["has_pending"] = tab["count"] > 0
        tab["is_active"] = tab["scope"] == current_scope

    return tabs
