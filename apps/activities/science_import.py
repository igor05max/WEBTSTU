"""Import confirmed factual results from the university science registry."""

from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from django.contrib.auth import get_user_model
from django.db import transaction

from apps.activities.models import Activity, ActivityStatus, ActivityType, ScientificResult


SPACE_RE = re.compile(r"\s+")
MATCH_WORD_RE = re.compile(r"[a-zа-я0-9]+", re.IGNORECASE)
NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)$")
INTEGER_RE = re.compile(r"^\d+$")
EXPECTED_FIELD_COUNT = 42
TRAILING_FIELD_COUNT = 9
MATCH_STOP_WORDS = {
    "the", "and", "for", "with", "from", "into", "using",
    "для", "или", "при", "как", "над", "под", "это", "этой", "этого",
    "научная", "научной", "научных", "статья", "статьи", "статей",
    "журнал", "журнале", "подготовка", "подготовки", "изданию", "публикация",
    "работа", "работы", "выполнение", "участие", "соавторстве",
}
RELATED_PLAN_TYPES = {
    frozenset(("article", "conference")),
    frozenset(("research_project", "contract_research")),
    frozenset(("teaching_aid", "methodical_material")),
}
GENERIC_PLAN_PATTERNS = {
    "article": re.compile(r"написан\w*.*стат|подготов\w*.*стат|статьи,?\s+опубликован"),
    "conference": re.compile(r"участи\w*.*конференц|организац\w*.*конференц|тезис\w*\s+доклад"),
    "research_project": re.compile(r"выполнен\w*.*(?:нир|научно[ -]?исследователь)"),
    "contract_research": re.compile(r"выполнен\w*.*(?:хоздоговор|договорн\w*\s+работ)"),
    "grant": re.compile(r"формирован\w*\s+заяв|подготов\w*\s+заяв|участи\w*\s+в\s+конкурс"),
    "patent": re.compile(r"составлен\w*\s+заяв|подготов\w*\s+заяв|патент\w*\s+на\s+изобрет"),
    "software_registration": re.compile(r"регистрац\w*.*(?:программ|баз\w*\s+данн)"),
}


@dataclass(frozen=True)
class ExtractedScientificResult:
    source_id: str
    external_author_id: str
    title: str
    result_year: int
    activity_type_code: str
    publication_name: str
    publication_details: str
    bibliographic_data: str
    source_file: str
    source_line: int
    source_payload: dict

    @property
    def source_key(self):
        return hashlib.sha256(f"science|{self.source_id}".encode("utf-8")).hexdigest()


def _clean(value):
    return SPACE_RE.sub(" ", str(value or "")).strip()


def _normalise(value):
    return _clean(value).casefold().replace("ё", "е")


def _match_text(value):
    return " ".join(MATCH_WORD_RE.findall(_normalise(value)))


def _match_tokens(value):
    return {
        token
        for token in MATCH_WORD_RE.findall(_normalise(value))
        if len(token) >= 3 and token not in MATCH_STOP_WORDS
    }


def _title_match_score(plan_title, result_title):
    plan_text = _match_text(plan_title)
    result_text = _match_text(result_title)
    if not plan_text or not result_text:
        return 0.0
    if min(len(plan_text), len(result_text)) >= 15 and (
        result_text in plan_text or plan_text in result_text
    ):
        return 1.0

    plan_tokens = _match_tokens(plan_title)
    result_tokens = _match_tokens(result_title)
    shared = plan_tokens & result_tokens
    if len(shared) < 2 or not result_tokens:
        return 0.0
    result_coverage = len(shared) / len(result_tokens)
    smaller_coverage = len(shared) / min(len(plan_tokens), len(result_tokens))
    return (result_coverage * 0.72) + (smaller_coverage * 0.28)


def _types_are_related(plan_code, result_code):
    return plan_code == result_code or frozenset((plan_code, result_code)) in RELATED_PLAN_TYPES


def _is_generic_plan(activity):
    normalized = _normalise(activity.title)
    if activity.quantity > 1 and ";" not in activity.title:
        return True
    pattern = GENERIC_PLAN_PATTERNS.get(activity.activity_type.code)
    return bool(pattern and pattern.search(normalized))


def _classify_educational_publication(row):
    text = _normalise(" ".join((row.get("NAME", ""), row.get("SHOW_NAME", ""), row.get("OUT_DATA", ""))))
    if re.search(r"электронн\w*\s+(?:учебн\w*\s+)?курс|онлайн[ -]?курс|\bэор\b|\bэук\b", text):
        return "online_course"
    if re.search(r"рабоч\w*\s+программ\w*\s+дисциплин", text):
        return "work_program"
    if re.search(r"\bучебник\w*", text):
        return "textbook"
    if re.search(r"учебно[ -]?методическ|методическ\w*\s+(?:материал|разработ|указан)", text):
        return "methodical_material"
    return "teaching_aid"


def _classify_research(row):
    text = _normalise(" ".join((row.get("NAME", ""), row.get("SHOW_NAME", ""), row.get("OUT_DATA", ""))))
    if re.search(r"хоз(?:договор|расчет)|договорн\w*\s+работ", text):
        return "contract_research"
    if re.search(r"\bгрант\w*|\bрнф\b|фонд|конкурс", text):
        return "grant"
    return "research_project"


def classify_science_type(row):
    """Translate the source SCIENCE_TYPE code into the plan catalogue."""

    source_type = _clean(row.get("SCIENCE_TYPE"))
    if source_type == "0":
        return "monograph"
    if source_type == "1":
        return _classify_educational_publication(row)
    if source_type == "2":
        return _classify_research(row)
    if source_type == "3":
        return "patent"
    if source_type == "4":
        return "article"
    if source_type == "5":
        return "conference"
    if source_type == "6":
        return "software_registration"
    raise ValueError(f"неизвестный SCIENCE_TYPE={source_type!r}")


def _bibliographic_data(row):
    parts = []
    for value in (
        row.get("OUT_DATA"),
        row.get("VOLUME"),
        (
            f"С. {row.get('PAGE_BEGIN')}-{row.get('PAGE_END')}"
            if row.get("PAGE_BEGIN") and row.get("PAGE_END")
            else ""
        ),
    ):
        value = _clean(value)
        if value and value not in parts:
            parts.append(value)
    return "; ".join(parts)


def _number_or_blank(value, *, integer=False):
    value = _clean(value)
    return not value or bool((INTEGER_RE if integer else NUMBER_RE).fullmatch(value))


def _metadata_start(fields, tail_start):
    """Find VOLUME after a NAME that may itself contain raw semicolons."""

    upper_bound = min(tail_start - 6, 22)
    for index in range(11, upper_bound):
        if index + 5 >= tail_start:
            break
        if not _number_or_blank(fields[index]):
            continue
        if not all(_number_or_blank(fields[position], integer=True) for position in range(index + 1, index + 5)):
            continue
        patent_date = _clean(fields[index + 5])
        if patent_date and not re.match(r"^\d{4}-\d{2}-\d{2}", patent_date):
            continue
        return index
    return 11


def _row_from_fields(fieldnames, fields):
    """Restore key columns when unescaped semicolons shifted the export."""

    if len(fields) == len(fieldnames):
        return dict(zip(fieldnames, fields))
    if len(fieldnames) != EXPECTED_FIELD_COUNT or len(fields) < EXPECTED_FIELD_COUNT:
        return {
            fieldname: fields[index] if index < len(fields) else ""
            for index, fieldname in enumerate(fieldnames)
        }

    tail_start = len(fields) - TRAILING_FIELD_COUNT
    metadata_start = _metadata_start(fields, tail_start)
    row = {fieldname: "" for fieldname in fieldnames}
    for index in range(10):
        row[fieldnames[index]] = fields[index]
    row["NAME"] = "; ".join(
        part.strip() for part in fields[10:metadata_start] if part.strip()
    )
    for fieldname, value in zip(fieldnames[11:17], fields[metadata_start : metadata_start + 6]):
        row[fieldname] = value
    for fieldname, value in zip(fieldnames[-TRAILING_FIELD_COUNT:], fields[-TRAILING_FIELD_COUNT:]):
        row[fieldname] = value

    # The remaining middle contains display/output text.  Exact boundaries are
    # not recoverable, but this preserves the bibliography and classification
    # text without ever guessing an author or approval flag.
    output_start = min(metadata_start + 15, tail_start - 6)
    row["OUT_DATA"] = "; ".join(
        part.strip() for part in fields[output_start : tail_start - 6] if part.strip()
    )
    row["_RECOVERED_FROM_SHIFT"] = True
    row["_RAW_FIELDS"] = fields
    return row


def extract_scientific_results(source_path, *, years=("2025", "2026")):
    """Read only inspector-approved records from a semicolon-delimited export."""

    source_path = Path(source_path)
    allowed_years = {str(year) for year in years}
    records = []
    errors = []
    with source_path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.reader(source, delimiter=";")
        fieldnames = next(reader, [])
        required_fields = {"ID", "SCIENCE_TYPE", "YEAR", "NAME", "AUTHOR_STAFF_ID", "INSPECTOR_FLG"}
        missing_fields = sorted(required_fields - set(fieldnames))
        if missing_fields:
            raise ValueError("В science отсутствуют поля: " + ", ".join(missing_fields))

        for fields in reader:
            row = _row_from_fields(fieldnames, fields)
            if _clean(row.get("YEAR")) not in allowed_years or _clean(row.get("INSPECTOR_FLG")) != "A":
                continue
            try:
                source_id = _clean(row.get("ID"))
                external_author_id = _clean(row.get("AUTHOR_STAFF_ID"))
                title = _clean(row.get("NAME"))
                if not source_id or not external_author_id or not title:
                    raise ValueError("нет ID, автора или названия")
                records.append(
                    ExtractedScientificResult(
                        source_id=source_id,
                        external_author_id=external_author_id,
                        title=title[:700],
                        result_year=int(_clean(row.get("YEAR"))),
                        activity_type_code=classify_science_type(row),
                        publication_name=_clean(row.get("SHOW_NAME"))[:700],
                        publication_details=_clean(row.get("SHOW_PLACE"))[:700],
                        bibliographic_data=_bibliographic_data(row),
                        source_file=source_path.name,
                        source_line=reader.line_num,
                        source_payload={
                            key: value if value is not None else ""
                            for key, value in row.items()
                        },
                    )
                )
            except (TypeError, ValueError) as exc:
                errors.append(f"строка {reader.line_num}, ID {_clean(row.get('ID')) or '—'}: {exc}")
    return records, errors


def allocate_scientific_results(academic_year):
    """Match facts to the concrete plan items and refresh imported statuses."""

    activities = list(
        Activity.objects.filter(academic_year=academic_year, source_key__isnull=False)
        .select_related("activity_type")
        .order_by("owner_id", "activity_type_id", "id")
    )
    results = list(
        ScientificResult.objects.filter(academic_year=academic_year, owner__isnull=False)
        .order_by("owner_id", "activity_type_id", "result_year", "source_id", "id")
    )

    ScientificResult.objects.filter(academic_year=academic_year).update(planned_activity=None)
    for result in results:
        result.planned_activity = None

    activities_by_owner = {}
    results_by_owner = {}
    for activity in activities:
        activities_by_owner.setdefault(activity.owner_id, []).append(activity)
    for result in results:
        results_by_owner.setdefault(result.owner_id, []).append(result)

    linked_results = []
    for owner_id, owner_results in results_by_owner.items():
        owner_activities = activities_by_owner.get(owner_id, ())
        remaining_capacity = {activity.pk: activity.quantity for activity in owner_activities}
        candidates = []
        for result in owner_results:
            result_code = result.activity_type.code
            for activity in owner_activities:
                plan_code = activity.activity_type.code
                if not _types_are_related(plan_code, result_code):
                    continue
                score = _title_match_score(activity.title, result.title)
                same_type = plan_code == result_code
                threshold = 0.52 if same_type else 0.78
                if score >= threshold:
                    candidates.append((score + (0.04 if same_type else 0), result.pk, activity.pk, result, activity))

        assigned_result_ids = set()
        for _score, _result_pk, _activity_pk, result, activity in sorted(
            candidates,
            key=lambda item: (-item[0], item[1], item[2]),
        ):
            if result.pk in assigned_result_ids or remaining_capacity[activity.pk] <= 0:
                continue
            result.planned_activity = activity
            linked_results.append(result)
            assigned_result_ids.add(result.pk)
            remaining_capacity[activity.pk] -= 1

        # After exact title matches, any remaining fact of the same catalogue
        # type fills the remaining capacity in that column. Generic targets
        # ("подготовить 3 статьи") go first; specific targets still receive a
        # same-type result rather than producing "0 / 1" and "+1 вне плана"
        # in the same cell.
        remaining_activities = [
            activity
            for activity in owner_activities
            if remaining_capacity[activity.pk]
        ]
        remaining_activities.sort(key=lambda activity: (not _is_generic_plan(activity), activity.pk))
        for activity in remaining_activities:
            for result in owner_results:
                if remaining_capacity[activity.pk] <= 0:
                    break
                if result.pk in assigned_result_ids or result.activity_type_id != activity.activity_type_id:
                    continue
                result.planned_activity = activity
                linked_results.append(result)
                assigned_result_ids.add(result.pk)
                remaining_capacity[activity.pk] -= 1

    if linked_results:
        ScientificResult.objects.bulk_update(linked_results, ("planned_activity",))
    linked = len(linked_results)

    linked_counts = {}
    for result in ScientificResult.objects.filter(
        academic_year=academic_year,
        planned_activity__isnull=False,
    ).values_list("planned_activity_id", flat=True):
        linked_counts[result] = linked_counts.get(result, 0) + 1

    completed = in_progress = planned = 0
    for activity in activities:
        if activity.source_is_overridden:
            continue
        actual_count = linked_counts.get(activity.pk, 0)
        status = (
            ActivityStatus.COMPLETED
            if actual_count >= activity.quantity
            else ActivityStatus.IN_PROGRESS
            if actual_count
            else ActivityStatus.PLANNED
        )
        if activity.status != status:
            activity.status = status
            activity.save(update_fields=("status", "updated_at"))
        if status == ActivityStatus.COMPLETED:
            completed += 1
        elif status == ActivityStatus.IN_PROGRESS:
            in_progress += 1
        else:
            planned += 1

    return {
        "linked": linked,
        "unplanned": len(results) - linked,
        "completed_plan_items": completed,
        "in_progress_plan_items": in_progress,
        "planned_plan_items": planned,
    }


def sync_scientific_results(records, academic_year, *, prune=False):
    """Create/update factual results idempotently and match them with plan items."""

    records = list(records)
    activity_types = {item.code: item for item in ActivityType.objects.all()}
    missing_types = sorted({record.activity_type_code for record in records} - set(activity_types))
    if missing_types:
        raise ValueError("В справочнике отсутствуют типы результатов: " + ", ".join(missing_types))

    User = get_user_model()
    users = {
        str(external_id): user
        for external_id, user in (
            (user.external_directory_id, user)
            for user in User.objects.exclude(external_directory_id__isnull=True).exclude(external_directory_id="")
        )
    }
    created = updated = deleted = 0
    unmatched = []
    seen_keys = set()
    with transaction.atomic():
        for record in records:
            owner = users.get(record.external_author_id)
            if owner is None:
                unmatched.append(
                    {
                        "source_id": record.source_id,
                        "external_author_id": record.external_author_id,
                        "title": record.title,
                    }
                )
            defaults = {
                "source_id": record.source_id,
                "external_author_id": record.external_author_id,
                "owner": owner,
                "activity_type": activity_types[record.activity_type_code],
                "planned_activity": None,
                "title": record.title,
                "result_year": record.result_year,
                "academic_year": academic_year,
                "publication_name": record.publication_name,
                "publication_details": record.publication_details,
                "bibliographic_data": record.bibliographic_data,
                "source_file": record.source_file,
                "source_line": record.source_line,
                "source_payload": record.source_payload,
            }
            _result, was_created = ScientificResult.objects.update_or_create(
                source_key=record.source_key,
                defaults=defaults,
            )
            seen_keys.add(record.source_key)
            if was_created:
                created += 1
            else:
                updated += 1

        if prune:
            stale = ScientificResult.objects.filter(academic_year=academic_year)
            if seen_keys:
                stale = stale.exclude(source_key__in=seen_keys)
            deleted, _details = stale.delete()

        allocation = allocate_scientific_results(academic_year)

    return {
        "records": len(records),
        "created": created,
        "updated": updated,
        "deleted": deleted,
        "unmatched": unmatched,
        **allocation,
    }
