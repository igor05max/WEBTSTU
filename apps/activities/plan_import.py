"""Import planned results from individual Excel plans without changing the source files."""

from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from django.db import transaction

from apps.accounts.publication_plans import (
    _read_shared_strings,
    _read_workbook_sheets,
    normalize_text,
)
from apps.activities.models import (
    Activity,
    ActivityPeriod,
    ActivityStatus,
    ActivityType,
    GrantType,
    PlanningRosterEntry,
)
from apps.activities.roster import (
    _extract_fio_from_general_info,
    _extract_fio_from_title_page,
    _read_sheet_cells,
)
from apps.activities.source_files import current_individual_plan_paths


RESULT_HEADER = "наименование результата"
LETTER_RE = re.compile(r"[A-Za-zА-Яа-яЁё]")
SPACE_RE = re.compile(r"\s+")
COUNT_TOKEN = r"(?P<count>\d+|один|одна|одно|одного|одной|две|два|двух|три|трех|трёх|четыре|четырех|четырёх|пять|пяти|шесть|шести|семь|семи|восемь|восьми|девять|девяти|десять|десяти)"

NUMBER_WORDS = {
    "один": 1,
    "одна": 1,
    "одно": 1,
    "одного": 1,
    "одной": 1,
    "две": 2,
    "два": 2,
    "двух": 2,
    "три": 3,
    "трех": 3,
    "трёх": 3,
    "четыре": 4,
    "четырех": 4,
    "четырёх": 4,
    "пять": 5,
    "пяти": 5,
    "шесть": 6,
    "шести": 6,
    "семь": 7,
    "семи": 7,
    "восемь": 8,
    "восьми": 8,
    "девять": 9,
    "девяти": 9,
    "десять": 10,
    "десяти": 10,
}

TYPE_PATTERNS = (
    (
        "software_registration",
        re.compile(
            r"(?:регистрац(?:ия|ии)|свидетельств\w*\s+о\s+регистрац\w*|заяв\w*.*?свидетельств\w*)"
            r".*?(?:программ|эвм|баз[аы]\s+данн)"
        ),
    ),
    ("grant", re.compile(r"\bгрант\w*|заяв\w*.*?(?:рнф|президент|минобрнауки|фонд|конкурс)")),
    ("article", re.compile(r"\bстат[ьяеию]")),
    ("monograph", re.compile(r"монограф")),
    ("contract_research", re.compile(r"хоз(?:договор|расчет)|договорн\w*\s+работ")),
    ("conference", re.compile(r"конференц|доклад\w*")),
    ("patent", re.compile(r"патент|изобретен")),
    ("dissertation", re.compile(r"диссертац|\bнкр\b")),
    ("textbook", re.compile(r"\bучебник\w*")),
    ("teaching_aid", re.compile(r"учебн\w*\s+пособ")),
    ("work_program", re.compile(r"рабоч\w*\s+программ\w*\s+дисциплин")),
    ("online_course", re.compile(r"электронн\w*\s+(?:учебн\w*\s+)?курс|электронн\w*\s+образовательн\w*\s+ресурс|онлайн[ -]?курс|\bэор\b|\bэук\b")),
    ("student_research", re.compile(r"(?:руководств|научн\w*\s+работ\w*).*?студент|студент\w*.*?(?:научн\w*\s+работ|исследован)")),
    ("olympiad", re.compile(r"олимпиад|конкурс")),
    ("career_guidance", re.compile(r"профориент|трудоустройств|профильн\w*\s+класс")),
    ("professional_retraining", re.compile(r"профессиональн\w*\s+переподготов")),
    ("advanced_training", re.compile(r"повышен\w*\s+квалификац")),
    ("educational_event", re.compile(r"воспитательн\w*\s+мероприяти|организационн\w*\s+мероприяти")),
    ("methodical_material", re.compile(r"учебно[ -]?методическ|методическ\w*\s+(?:материал|разработ|указан)")),
)
RESEARCH_PATTERN = re.compile(r"\bнир\b|научно[ -]?исследователь|исследовательск\w*\s+проект")
ORGANISATIONAL_PATTERN = re.compile(r"мероприяти|\bгэк\b|куратор\w*|школ\w*\s+молод\w*\s+учен|заседани\w*\s+совет")
OTHER_ACTION_PATTERN = re.compile(
    r"подготовк|разработк|проведен|организац|участи|руководств|модификац|выполнени|курирован|"
    r"план\w*\s+работ|экспертиз|присвоени|протокол|работ\w*\s+в\s+составе"
)

GRANT_PATTERNS = (
    ("rnf", re.compile(r"\bрнф\b|российск\w*\s+научн\w*\s+фонд")),
    ("president", re.compile(r"президент\w*\s+(?:рф|российск)|стипенди\w*\s+президент")),
    ("minobrnauki", re.compile(r"минобрнауки|министерств\w*\s+науки")),
    ("innovation_fund", re.compile(r"фонд\w*\s+содейств\w*\s+инновац")),
    ("regional", re.compile(r"региональн\w*|областн\w*")),
    ("university", re.compile(r"внутривуз|университетск")),
    ("international", re.compile(r"международн\w*")),
)

QUANTITY_PATTERNS = {
    "article": re.compile(COUNT_TOKEN + r"\s+(?:научн\w*\s+)?стат", re.IGNORECASE),
    "monograph": re.compile(COUNT_TOKEN + r"\s+монограф", re.IGNORECASE),
    "grant": re.compile(COUNT_TOKEN + r"\s+(?:заяв\w*|грант\w*)", re.IGNORECASE),
    "conference": re.compile(COUNT_TOKEN + r"\s+(?:конференц|доклад)", re.IGNORECASE),
    "patent": re.compile(COUNT_TOKEN + r"\s+(?:патент|изобретен)", re.IGNORECASE),
    "software_registration": re.compile(COUNT_TOKEN + r"\s+(?:свидетельств|регистрац)", re.IGNORECASE),
    "textbook": re.compile(COUNT_TOKEN + r"\s+учебник", re.IGNORECASE),
    "teaching_aid": re.compile(COUNT_TOKEN + r"\s+учебн\w*\s+пособ", re.IGNORECASE),
}


@dataclass(frozen=True)
class ExtractedPlanActivity:
    department_code: str
    full_name: str
    source_file: str
    source_sheet: str
    source_cell: str
    source_text: str
    title: str
    activity_type_code: str
    grant_type_code: str
    item_index: int
    quantity: int = 1

    @property
    def source_key(self):
        payload = "|".join(
            (
                self.source_file,
                self.source_sheet,
                self.source_cell,
                str(self.item_index),
                self.activity_type_code,
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalise(value):
    return SPACE_RE.sub(" ", normalize_text(value or "")).strip().casefold().replace("ё", "е")


def _column_name(column):
    result = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _is_result_text(value):
    normalized = _normalise(value)
    if not normalized or normalized in {"нет", "не планируется", "-"}:
        return False
    if not LETTER_RE.search(value):
        return False
    return RESULT_HEADER not in normalized and not normalized.startswith("планируемые результаты")


def _result_cells(cells):
    headers = [
        (row, column)
        for row, column, value in cells
        if RESULT_HEADER in _normalise(value)
    ]
    seen = set()
    for header_row, result_column in headers:
        for row, column, value in cells:
            key = (row, column)
            if row <= header_row or column != result_column or key in seen:
                continue
            if _is_result_text(value):
                seen.add(key)
                yield row, column, value


def _classify_activity_codes(text):
    normalized = _normalise(text)
    codes = [code for code, pattern in TYPE_PATTERNS if pattern.search(normalized)]
    if "grant" in codes and "olympiad" in codes:
        codes.remove("olympiad")
    if codes:
        return tuple(codes)
    if RESEARCH_PATTERN.search(normalized):
        return ("research_project",)
    if ORGANISATIONAL_PATTERN.search(normalized):
        return ("educational_event",)
    return ("other",)


def _should_import_item(title, activity_type_codes):
    if activity_type_codes != ("other",):
        return True
    normalized = _normalise(title)
    return len(normalized) >= 12 and bool(OTHER_ACTION_PATTERN.search(normalized))


def _grant_type_code(text):
    normalized = _normalise(text)
    for code, pattern in GRANT_PATTERNS:
        if pattern.search(normalized):
            return code
    return "other"


def _quantity_for(text, activity_type_code):
    pattern = QUANTITY_PATTERNS.get(activity_type_code)
    normalized = _normalise(text)
    quantities = []
    if pattern is not None:
        for match in pattern.finditer(normalized):
            value = match.group("count")
            quantities.append(int(value) if value.isdigit() else NUMBER_WORDS.get(value, 1))
    if quantities:
        quantity = sum(quantities)
        return quantity if 1 <= quantity <= 50 else 1

    # Some plans contain a named list after a heading instead of an explicit
    # number, for example "Статьи: ...; ...; ...".  Every semicolon-separated
    # entry is a separate planned result in that notation.
    list_heading_patterns = {
        "article": r"^статьи\s*:",
        "conference": r"^(?:тезисы\s+докладов|доклады|конференции)\b[^:]*:",
    }
    heading_pattern = list_heading_patterns.get(activity_type_code)
    if heading_pattern and re.search(heading_pattern, normalized) and ";" in text:
        listed_quantity = len([part for part in text.split(";") if LETTER_RE.search(part)])
        if 1 <= listed_quantity <= 50:
            return listed_quantity
    return 1


def _split_result_items(text):
    pieces = [SPACE_RE.sub(" ", part).strip(" -\t") for part in re.split(r"[;\n]+", text) if part.strip()]
    if len(pieces) < 2:
        return pieces
    result = []
    for piece in pieces:
        explicit_type = _classify_activity_codes(piece) != ("other",)
        if result and (not explicit_type or not LETTER_RE.search(piece)):
            result[-1] = f"{result[-1]}; {piece}"
        else:
            result.append(piece)
    return result


def extract_plan_activities(source_root):
    """Return planned-result records from sheets 2–5 of individual plans."""

    source_root = Path(source_root)
    records = []
    errors = []
    for path in current_individual_plan_paths(source_root):
        try:
            with zipfile.ZipFile(path) as archive:
                shared_strings = _read_shared_strings(archive)
                sheets = dict(_read_workbook_sheets(archive))
                general_info_path = sheets.get("Общие сведения")
                if not general_info_path:
                    continue
                full_name = _extract_fio_from_general_info(
                    _read_sheet_cells(archive, shared_strings, general_info_path)
                )
                if not full_name:
                    title_sheet_path = next(
                        (sheet_path for sheet_name, sheet_path in sheets.items() if sheet_name.startswith("Тит.")),
                        "",
                    )
                    if title_sheet_path:
                        full_name = _extract_fio_from_title_page(
                            _read_sheet_cells(archive, shared_strings, title_sheet_path)
                        )
                if not full_name:
                    errors.append(f"{path.name}: не удалось определить ФИО преподавателя")
                    continue

                relative_path = path.relative_to(source_root)
                for sheet_name in ("2", "3", "4", "5"):
                    sheet_path = sheets.get(sheet_name)
                    if not sheet_path:
                        continue
                    for row, column, raw_text in _result_cells(
                        _read_sheet_cells(archive, shared_strings, sheet_path)
                    ):
                        source_cell = f"{_column_name(column)}{row}"
                        for item_index, title in enumerate(_split_result_items(raw_text), start=1):
                            activity_type_codes = _classify_activity_codes(title)
                            if not _should_import_item(title, activity_type_codes):
                                continue
                            for activity_type_code in activity_type_codes:
                                records.append(
                                    ExtractedPlanActivity(
                                        department_code=relative_path.parts[0],
                                        full_name=SPACE_RE.sub(" ", full_name).strip(),
                                        source_file=relative_path.as_posix(),
                                        source_sheet=sheet_name,
                                        source_cell=source_cell,
                                        source_text=raw_text,
                                        title=title[:700],
                                        activity_type_code=activity_type_code,
                                        grant_type_code=(
                                            _grant_type_code(title)
                                            if activity_type_code == "grant"
                                            else ""
                                        ),
                                        item_index=item_index,
                                        quantity=_quantity_for(title, activity_type_code),
                                    )
                                )
        except (KeyError, OSError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
            errors.append(f"{path.name}: {exc}")
    return records, errors


def sync_plan_activities(records, academic_year, *, prune=False):
    """Create or update imported activities; manually created records are never touched."""

    records = list(records)
    activity_types = {item.code: item for item in ActivityType.objects.all()}
    grant_types = {item.code: item for item in GrantType.objects.all()}
    missing_type_codes = sorted({item.activity_type_code for item in records} - set(activity_types))
    if missing_type_codes:
        raise ValueError("В справочнике отсутствуют типы результатов: " + ", ".join(missing_type_codes))
    if any(item.activity_type_code == "grant" for item in records) and "other" not in grant_types:
        raise ValueError("В справочнике отсутствует вид гранта «Другой».")

    source_users = {}
    for entry in PlanningRosterEntry.objects.filter(academic_year=academic_year).select_related("user"):
        for source_file in entry.source_files:
            source_users[source_file] = entry.user

    created = updated = 0
    unmatched = []
    seen_keys = set()
    with transaction.atomic():
        for record in records:
            owner = source_users.get(record.source_file)
            if owner is None:
                unmatched.append(
                    {
                        "department_code": record.department_code,
                        "full_name": record.full_name,
                        "source_file": record.source_file,
                    }
                )
                continue
            activity_type = activity_types[record.activity_type_code]
            grant_type = (
                grant_types.get(record.grant_type_code) or grant_types["other"]
                if record.activity_type_code == "grant"
                else None
            )
            defaults = {
                "owner": owner,
                "activity_type": activity_type,
                "grant_type": grant_type,
                "title": record.title,
                "quantity": record.quantity,
                "academic_year": academic_year,
                "period": ActivityPeriod.WHOLE_YEAR,
                "status": ActivityStatus.PLANNED,
                "source_file": record.source_file,
                "source_sheet": record.source_sheet,
                "source_cell": record.source_cell,
                "source_text": record.source_text,
            }
            _activity = Activity.objects.filter(source_key=record.source_key).first()
            was_created = _activity is None
            if was_created:
                Activity.objects.create(source_key=record.source_key, **defaults)
            elif _activity.source_is_overridden:
                _activity.source_file = record.source_file
                _activity.source_sheet = record.source_sheet
                _activity.source_cell = record.source_cell
                _activity.source_text = record.source_text
                _activity.save(
                    update_fields=(
                        "source_file",
                        "source_sheet",
                        "source_cell",
                        "source_text",
                        "updated_at",
                    )
                )
            else:
                for field_name, value in defaults.items():
                    setattr(_activity, field_name, value)
                _activity.save()
            seen_keys.add(record.source_key)
            if was_created:
                created += 1
            else:
                updated += 1

        deleted = 0
        if prune and seen_keys:
            deleted, _details = (
                Activity.objects.filter(academic_year=academic_year, source_key__isnull=False)
                .exclude(source_key__in=seen_keys)
                .delete()
            )

        # Reapplying an individual plan must not erase statuses supported by
        # already imported factual results.
        from apps.activities.science_import import allocate_scientific_results

        allocation = allocate_scientific_results(academic_year)

    return {
        "created": created,
        "updated": updated,
        "deleted": deleted,
        "records": len(records),
        "unmatched": unmatched,
        **allocation,
    }
