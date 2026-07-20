import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction

from apps.accounts.publication_plans import (
    XML_NS,
    _cell_text,
    _read_shared_strings,
    _read_workbook_sheets,
    normalize_text,
)
from apps.activities.models import PlanningRosterEntry
from apps.activities.source_files import current_individual_plan_paths


SPACE_RE = re.compile(r"\s+")
NON_ALNUM_RE = re.compile(r"[^0-9A-ZА-ЯЁ]+")
CELL_REF_RE = re.compile(r"^(?P<column>[A-Z]+)(?P<row>\d+)$")
FIO_RE = re.compile(r"^[А-ЯЁ][а-яё-]+\s+[А-ЯЁ][а-яё-]+\s+[А-ЯЁ][а-яё-]+$")


@dataclass(frozen=True)
class ExtractedRosterPerson:
    department_code: str
    full_name: str
    source_file: str


def normalize_person_name(value):
    return NON_ALNUM_RE.sub(" ", str(value or "").upper().replace("Ё", "Е")).strip()


def _cell_position(reference):
    match = CELL_REF_RE.match(reference or "")
    if match is None:
        return 0, 0
    column = 0
    for char in match.group("column"):
        column = column * 26 + ord(char) - ord("A") + 1
    return int(match.group("row")), column


def _read_sheet_cells(archive, shared_strings, sheet_path):
    root = ElementTree.fromstring(archive.read(sheet_path))
    result = []
    for cell in root.findall(".//main:c", XML_NS):
        reference = cell.attrib.get("r", "")
        text = normalize_text(_cell_text(cell, shared_strings))
        if text:
            row, column = _cell_position(reference)
            result.append((row, column, text))
    return result


def _extract_fio_from_general_info(cells):
    for row, column, text in cells:
        if normalize_person_name(text) != "Ф И О":
            continue
        values_on_same_row = [
            (candidate_column, candidate_text)
            for candidate_row, candidate_column, candidate_text in cells
            if candidate_row == row and candidate_column > column
        ]
        if values_on_same_row:
            return min(values_on_same_row)[1]
    return ""


def _extract_fio_from_title_page(cells):
    label_rows = [
        row
        for row, _column, text in cells
        if "ФАМИЛИЯ ИМЯ ОТЧЕСТВО ПРЕПОДАВАТЕЛЯ" in normalize_person_name(text)
    ]
    if not label_rows:
        return ""
    label_row = min(label_rows)
    candidates = [
        (row, column, text)
        for row, column, text in cells
        if label_row - 4 <= row < label_row and FIO_RE.match(SPACE_RE.sub(" ", text).strip())
    ]
    if not candidates:
        return ""
    return max(candidates, key=lambda item: (item[0], -item[1]))[2]


def extract_roster_people(source_root):
    source_root = Path(source_root)
    records = []
    errors = []
    for path in current_individual_plan_paths(source_root):
        try:
            with zipfile.ZipFile(path) as archive:
                shared_strings = _read_shared_strings(archive)
                sheets = dict(_read_workbook_sheets(archive))
                if "Общие сведения" not in sheets:
                    continue
                full_name = _extract_fio_from_general_info(
                    _read_sheet_cells(archive, shared_strings, sheets["Общие сведения"])
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
        except (KeyError, OSError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
            errors.append(f"{path.name}: {exc}")
            continue

        if not full_name:
            errors.append(f"{path.name}: не найдено ФИО преподавателя")
            continue
        relative_path = path.relative_to(source_root)
        records.append(
            ExtractedRosterPerson(
                department_code=relative_path.parts[0],
                full_name=SPACE_RE.sub(" ", full_name).strip(),
                source_file=relative_path.as_posix(),
            )
        )
    return records, errors


def _build_user_name_index():
    index = defaultdict(list)
    for user in get_user_model().objects.filter(is_active=True, is_superuser=False):
        raw_values = (
            user.first_name,
            f"{user.first_name} {user.last_name}",
            f"{user.last_name} {user.first_name}",
            user.get_full_name(),
            user.username.replace("_", " "),
        )
        for raw_value in raw_values:
            normalized = normalize_person_name(raw_value)
            if normalized and user not in index[normalized]:
                index[normalized].append(user)
    return index


def _build_username(full_name):
    transliteration = {
        "А": "a", "Б": "b", "В": "v", "Г": "g", "Д": "d", "Е": "e", "Ё": "e",
        "Ж": "zh", "З": "z", "И": "i", "Й": "i", "К": "k", "Л": "l", "М": "m",
        "Н": "n", "О": "o", "П": "p", "Р": "r", "С": "s", "Т": "t", "У": "u",
        "Ф": "f", "Х": "h", "Ц": "ts", "Ч": "ch", "Ш": "sh", "Щ": "sch", "Ъ": "",
        "Ы": "y", "Ь": "", "Э": "e", "Ю": "yu", "Я": "ya", "-": "",
    }
    parts = normalize_person_name(full_name).split()
    base = "".join(transliteration.get(char, "") for char in (parts[0] if parts else "sotrudnik"))
    initials = "".join(transliteration.get(part[:1], "") for part in parts[1:3])
    return f"{base}_{initials}".strip("_") or "sotrudnik"


def _create_missing_user(full_name, name_index):
    User = get_user_model()
    base_username = _build_username(full_name)
    username = base_username
    suffix = 2
    while User.objects.filter(username=username).exists():
        username = f"{base_username}_{suffix}"
        suffix += 1
    user = User(username=username, first_name=full_name, is_active=True)
    user.set_password(settings.DEFAULT_USER_PASSWORD)
    user.save()
    name_index[normalize_person_name(full_name)].append(user)
    return user


def sync_planning_roster(records, academic_year, *, create_missing=False):
    grouped_records = defaultdict(list)
    for record in records:
        grouped_records[(record.department_code, normalize_person_name(record.full_name))].append(record)

    name_index = _build_user_name_index()
    prepared_entries = []
    missing_records = []
    unresolved = []
    users_created = 0
    for (department_code, normalized_name), grouped in grouped_records.items():
        matched_users = name_index.get(normalized_name, [])
        if not matched_users and create_missing:
            missing_records.append((department_code, grouped))
            continue
        if len(matched_users) != 1:
            unresolved.append(
                {
                    "department_code": department_code,
                    "full_name": grouped[0].full_name,
                    "matches": [user.username for user in matched_users],
                }
            )
            continue
        prepared_entries.append(
            {
                "department_code": department_code,
                "full_name": grouped[0].full_name,
                "user": matched_users[0],
                "source_files": sorted({record.source_file for record in grouped}),
            }
        )

    if unresolved:
        return {
            "created": 0,
            "updated": 0,
            "deleted": 0,
            "users_created": 0,
            "unresolved": unresolved,
        }

    created = updated = 0
    seen_keys = set()
    with transaction.atomic():
        for department_code, grouped in missing_records:
            user = _create_missing_user(grouped[0].full_name, name_index)
            users_created += 1
            prepared_entries.append(
                {
                    "department_code": department_code,
                    "full_name": grouped[0].full_name,
                    "user": user,
                    "source_files": sorted({record.source_file for record in grouped}),
                }
            )
        for entry in prepared_entries:
            key = (entry["department_code"], entry["user"].pk)
            seen_keys.add(key)
            _roster_entry, was_created = PlanningRosterEntry.objects.update_or_create(
                academic_year=academic_year,
                department_code=entry["department_code"],
                user=entry["user"],
                defaults={
                    "full_name": entry["full_name"],
                    "source_files": entry["source_files"],
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1

        stale_entries = PlanningRosterEntry.objects.filter(academic_year=academic_year)
        for department_code, user_id in seen_keys:
            stale_entries = stale_entries.exclude(department_code=department_code, user_id=user_id)
        deleted, _details = stale_entries.delete()

    return {
        "created": created,
        "updated": updated,
        "deleted": deleted,
        "users_created": users_created,
        "unresolved": [],
    }
