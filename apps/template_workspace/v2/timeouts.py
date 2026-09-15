"""Keep worker and stale-job deadlines outside configured AI queue budgets."""
from django.conf import settings


def job_timeout_seconds():
    planning = max(1, int(getattr(settings, 'TEMPLATE_V2_QWEN_TIMEOUT', 300))) if getattr(
        settings, 'TEMPLATE_V2_QWEN_ENABLED', False) else 0
    vision = max(1, int(getattr(settings, 'TEMPLATE_V2_VISUAL_REVIEW_BUDGET', 900))) if getattr(
        settings, 'TEMPLATE_V2_VISUAL_REVIEW_ENABLED', False) else 0
    # Separate margin for DOC/DOTX preparation, inspection and two PDF renders.
    return max(18 * 60, planning + vision + 10 * 60)


def stale_job_seconds():
    return job_timeout_seconds() + 2 * 60
