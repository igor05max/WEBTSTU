from dataclasses import dataclass

from django.conf import settings
from django.db.models import Q

from apps.checks.models import CheckRun
from apps.directory.models import Direction
from apps.submissions.models import SubmissionStatus
from apps.submissions.subject_area import detect_direction_for_submission
from apps.workflow.models import RouteTemplate

SUBJECT_AREA_CHECK_CODE = "subject_area_detection"


@dataclass(frozen=True)
class SubmissionRouteSuggestion:
    direction: Direction
    route_template: RouteTemplate
    source: str
    message: str

    @property
    def source_label(self):
        if self.source == "saved":
            return "Сохраненный выбор"
        if self.source in {"gemini", "ai"}:
            return "AI-модель"
        if self.source == "single_route":
            return "Автовыбор"
        if self.source == "single_direction":
            return "Автовыбор"
        return "Подсказка"


def _filter_route_templates_by_article_type(queryset, article_type):
    if article_type is None:
        return queryset
    article_type_id = getattr(article_type, "pk", article_type)
    return queryset.filter(Q(article_type_id=article_type_id) | Q(article_type__isnull=True))


def get_selectable_route_templates_queryset(*, article_type=None, direction=None):
    queryset = (
        RouteTemplate.objects.filter(
            is_active=True,
        )
        .select_related("direction", "article_type")
        .order_by("direction__name", "article_type__name", "-priority", "name")
    )

    queryset = _filter_route_templates_by_article_type(queryset, article_type)
    allowed_ids = settings.SUBMISSION_SELECTABLE_ROUTE_TEMPLATE_IDS
    if allowed_ids:
        queryset = queryset.filter(id__in=allowed_ids)

    material_templates = queryset.filter(direction__isnull=True)
    if material_templates.exists():
        return material_templates

    if direction is not None:
        direction_id = getattr(direction, "pk", direction)
        queryset = queryset.filter(direction_id=direction_id)
    return queryset


def get_selectable_directions_queryset(*, article_type=None):
    route_queryset = get_selectable_route_templates_queryset(article_type=article_type)
    if route_queryset.filter(direction__isnull=True).exists():
        return Direction.objects.filter(is_active=True).order_by("name")

    return (
        Direction.objects.filter(is_active=True, route_templates__in=route_queryset)
        .distinct()
        .order_by("name")
    )


def is_route_template_selectable(route_template, *, article_type=None):
    if route_template is None:
        return False
    if not route_template.is_active:
        return False

    direction = route_template.direction
    if direction is not None and not direction.is_active:
        return False

    allowed_ids = settings.SUBMISSION_SELECTABLE_ROUTE_TEMPLATE_IDS
    if allowed_ids and route_template.id not in allowed_ids:
        return False

    article_type_id = getattr(article_type, "pk", article_type)
    if article_type_id is not None and route_template.article_type_id not in (None, article_type_id):
        return False
    return True


def _route_template_sort_key(route_template, *, article_type):
    article_type_id = getattr(article_type, "pk", article_type)
    return (
        0 if route_template.direction_id is None else 1,
        0 if route_template.article_type_id == article_type_id else 1,
        -route_template.priority,
        route_template.name,
        route_template.id,
    )


def _pick_route_template_for_direction(*, article_type, direction):
    if direction is None:
        route_templates = list(
            get_selectable_route_templates_queryset(
                article_type=article_type,
            ).filter(direction__isnull=True)
        )
    else:
        route_templates = list(
            get_selectable_route_templates_queryset(
                article_type=article_type,
                direction=direction,
            )
        )
    if not route_templates:
        return None
    route_templates.sort(key=lambda item: _route_template_sort_key(item, article_type=article_type))
    return route_templates[0]


def _build_suggestion(direction, route_template, *, source, message):
    if direction is None or route_template is None:
        return None
    return SubmissionRouteSuggestion(
        direction=direction,
        route_template=route_template,
        source=source,
        message=message,
    )


def _get_latest_subject_area_check_run(submission):
    if submission.current_version_id is None:
        return None

    return (
        CheckRun.objects.filter(
            submission=submission,
            version_id=submission.current_version_id,
            check_definition__code=SUBJECT_AREA_CHECK_CODE,
        )
        .order_by("-created_at", "-id")
        .first()
    )


def _build_suggestion_from_subject_area_payload(submission, payload):
    if not payload or not payload.get("matched"):
        return None

    direction_code = str(payload.get("direction_code") or "").strip()
    if not direction_code:
        return None

    try:
        direction = Direction.objects.get(code=direction_code, is_active=True)
    except Direction.DoesNotExist:
        return None

    route_template = _pick_route_template_for_direction(
        article_type=submission.article_type,
        direction=direction,
    )
    if route_template is None:
        return None

    reasoning = str(payload.get("reasoning") or "").strip()
    message = str(payload.get("message") or "").strip()
    if reasoning:
        message = f"{message} {reasoning}".strip()

    return _build_suggestion(
        direction,
        route_template,
        source=str(payload.get("source") or "hint"),
        message=message,
    )


def _build_suggestion_from_subject_area_check(submission):
    check_run = _get_latest_subject_area_check_run(submission)
    if check_run is None:
        return None
    return _build_suggestion_from_subject_area_payload(submission, check_run.result_payload or {})


def suggest_submission_route(submission):
    route_template = _pick_route_template_for_direction(
        article_type=submission.article_type,
        direction=submission.direction,
    )
    if route_template is None:
        return None

    directions = list(get_selectable_directions_queryset(article_type=submission.article_type))
    if len(directions) == 1:
        direction = directions[0]
        return _build_suggestion(
            direction,
            route_template,
            source="single_direction",
            message="Для выбранного типа материала доступна только одна область экспертизы.",
        )

    subject_area_suggestion = _build_suggestion_from_subject_area_check(submission)
    if subject_area_suggestion is not None:
        return subject_area_suggestion

    subject_area_payload = detect_direction_for_submission(
        submission,
        directions=directions,
    )
    return _build_suggestion_from_subject_area_payload(submission, subject_area_payload)

    return None


def ensure_submission_route_suggestion(submission):
    if submission.status != SubmissionStatus.SUBMITTED:
        return None

    preferred_route_template = _pick_route_template_for_direction(
        article_type=submission.article_type,
        direction=submission.direction,
    )
    if (
        preferred_route_template is not None
        and submission.route_template_id != preferred_route_template.id
    ):
        submission.route_template = preferred_route_template
        submission.save(update_fields=["route_template", "updated_at"])

    subject_area_suggestion = _build_suggestion_from_subject_area_check(submission)

    if (
        submission.route_template_id is not None
        and submission.direction_id is not None
        and submission.route_template.direction_id in (None, submission.direction_id)
        and submission.route_template.article_type_id in (None, submission.article_type_id)
        and is_route_template_selectable(submission.route_template, article_type=submission.article_type)
    ):
        if (
            subject_area_suggestion is not None
            and subject_area_suggestion.direction.id == submission.direction_id
            and subject_area_suggestion.route_template.id == submission.route_template_id
        ):
            return subject_area_suggestion

        return _build_suggestion(
            submission.direction,
            submission.route_template,
            source="saved",
            message="Сохраненный выбор области и маршрута можно изменить перед отправкой.",
        )

    suggestion = subject_area_suggestion or suggest_submission_route(submission)
    if suggestion is None:
        return None

    update_fields = []
    if submission.direction_id != suggestion.direction.id:
        submission.direction = suggestion.direction
        update_fields.append("direction")
    if submission.route_template_id != suggestion.route_template.id:
        submission.route_template = suggestion.route_template
        update_fields.append("route_template")

    if update_fields:
        submission.save(update_fields=[*update_fields, "updated_at"])

    return suggestion
