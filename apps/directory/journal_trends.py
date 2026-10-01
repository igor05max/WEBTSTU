"""Rank journals by recent choices in newly created materials."""

from datetime import timedelta

from django.db.models import Count, Max, Q
from django.utils import timezone

from apps.directory.models import Journal
from apps.submissions.models import Submission


def trending_journals(*, limit=10, now=None):
    """Return active journals, giving rising weekly demand the most weight.

    Each submission contributes once, when it is created. Activity older than
    90 days does not influence the ranking; catalog entries fill empty slots.
    """
    if limit <= 0:
        return []
    now = now or timezone.now()
    week_start = now - timedelta(days=7)
    previous_week_start = now - timedelta(days=14)
    month_start = now - timedelta(days=30)
    window_start = now - timedelta(days=90)

    activity = Submission.objects.filter(
        journal__is_active=True,
        created_at__gte=window_start,
        created_at__lte=now,
    ).values("journal_id").annotate(
        week_count=Count("pk", filter=Q(created_at__gte=week_start)),
        previous_week_count=Count(
            "pk",
            filter=Q(created_at__gte=previous_week_start, created_at__lt=week_start),
        ),
        month_count=Count("pk", filter=Q(created_at__gte=month_start)),
        window_count=Count("pk"),
        latest_choice=Max("created_at"),
    )
    ranked = []
    for row in activity:
        week = row["week_count"]
        previous_week = row["previous_week_count"]
        month = row["month_count"]
        older_month = month - week - previous_week
        older_window = row["window_count"] - month
        score = (
            100 * week
            + 25 * max(week - previous_week, 0)
            + 20 * previous_week
            + 4 * older_month
            + older_window
        )
        ranked.append({**row, "score": score})

    journals = Journal.objects.in_bulk(row["journal_id"] for row in ranked)
    ranked.sort(
        key=lambda row: (
            -row["score"],
            -row["week_count"],
            -row["latest_choice"].timestamp(),
            journals[row["journal_id"]].name.casefold(),
        )
    )
    result = [
        {
            "journal": journals[row["journal_id"]],
            "week_count": row["week_count"],
            "month_count": row["month_count"],
        }
        for row in ranked[:limit]
    ]
    if len(result) < limit:
        selected_ids = [item["journal"].pk for item in result]
        for journal in Journal.objects.filter(is_active=True).exclude(pk__in=selected_ids).order_by("name")[:limit - len(result)]:
            result.append({"journal": journal, "week_count": 0, "month_count": 0})
    return result
