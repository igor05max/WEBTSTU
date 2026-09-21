"""Keep worker and stale-job deadlines outside configured AI queue budgets."""
from django.conf import settings


def job_timeout_seconds():
    planning = max(1, int(getattr(settings, 'TEMPLATE_V2_QWEN_TIMEOUT', 300))) if getattr(
        settings, 'TEMPLATE_V2_QWEN_ENABLED', False) else 0
    vision = max(1, int(getattr(settings, 'TEMPLATE_V2_VISUAL_REVIEW_BUDGET', 900))) if getattr(
        settings, 'TEMPLATE_V2_VISUAL_REVIEW_ENABLED', False) else 0
    if vision and getattr(settings, 'TEMPLATE_V2_EDIT_CYCLE_ENABLED', True):
        vision = max(vision, getattr(settings, 'TEMPLATE_V2_EDIT_CYCLE_BUDGET', 2100))
    # Separate margin for DOC/DOTX preparation, inspection and two PDF renders.
    latex = 16 * 60 if getattr(settings, 'JAMT_LATEX_EXPORT_ENABLED', False) else 0
    editorial = max(0, int(getattr(settings, 'JAMT_EDITORIAL_REVIEW_SECONDS', 120))) if planning else 0
    return max(18 * 60, planning + vision + 10 * 60 + editorial) + latex


def stale_job_seconds():
    return job_timeout_seconds() + 2 * 60
