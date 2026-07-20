import re
from dataclasses import dataclass
from html import unescape

from django.conf import settings
from django.contrib.auth import get_user_model

from apps.accounts.roles import ensure_chair_head_role_for_org_unit, is_chair_head_position
from apps.directory.models import OrgUnit, OrgUnitType, Position

_STAFF_LINK_RE = re.compile(
    r"""<a\s+href="javascript:passBack\('(?P<external_id>\d*)','[^']*'\);">(?P<label>[^<]+)</a>""",
    re.IGNORECASE,
)

_POSITION_STARTERS = {
    "ассистент",
    "бухгалтер",
    "ведущий",
    "врио",
    "главный",
    "декан",
    "директор",
    "диспетчер",
    "доцент",
    "заведующий",
    "инженер",
    "лаборант",
    "методист",
    "младший",
    "начальник",
    "помощник",
    "профессор",
    "преподаватель",
    "проректор",
    "ректор",
    "секретарь",
    "советник",
    "специалист",
    "старший",
    "учитель",
    "экономист",
    "юрисконсульт",
}

_ORG_UNIT_STARTERS = (
    "абонентский",
    "автошкола",
    "гараж",
    "деканат",
    "издательский",
    "институт",
    "кафедра",
    "контрактная",
    "многопрофильный",
    "научная",
    "научно-",
    "отдел",
    "политехнический",
    "ректорат",
    "региональный",
    "режим",
    "сектор",
    "служба",
    "столовая",
    "студенческое",
    "студенческий",
    "тематические",
    "технологический",
    "типография",
    "тррц",
    "управление",
    "учебно-",
    "учебный",
    "факультет",
    "финансово-экономическое",
    "центр",
    "экспертная",
    "эксплуатационно-техническое",
    "юридический",
)

_CYRILLIC_TO_LATIN = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "i",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


@dataclass(frozen=True)
class StaffDirectoryEntry:
    external_id: str
    full_name: str
    position_name: str
    org_unit_name: str


def _normalize_spaces(value):
    return re.sub(r"\s+", " ", value).strip()


def _looks_like_org_unit(value):
    normalized = _normalize_spaces(value)
    if not normalized:
        return False

    first_word = normalized.split(" ", 1)[0].strip('"').lower()
    if first_word in _POSITION_STARTERS:
        return False

    lower_value = normalized.lower()
    return lower_value.startswith(_ORG_UNIT_STARTERS)


def _split_position_and_unit(details):
    details = _normalize_spaces(details)
    if ", " not in details:
        return details, ""

    parts = details.split(", ")
    for index in range(1, len(parts)):
        position_name = ", ".join(parts[:index]).strip()
        org_unit_name = ", ".join(parts[index:]).strip()
        if _looks_like_org_unit(org_unit_name):
            return position_name, org_unit_name

    position_name, org_unit_name = details.rsplit(", ", 1)
    return position_name.strip(), org_unit_name.strip()


def parse_staff_directory_html(html_text):
    entries = []
    for match in _STAFF_LINK_RE.finditer(html_text):
        external_id = (match.group("external_id") or "").strip()
        label = _normalize_spaces(unescape(match.group("label") or ""))
        if not external_id or label == "%":
            continue
        if " / " not in label:
            continue
        full_name, details = label.split(" / ", 1)
        position_name, org_unit_name = _split_position_and_unit(details)
        entries.append(
            StaffDirectoryEntry(
                external_id=external_id,
                full_name=_normalize_spaces(full_name),
                position_name=position_name,
                org_unit_name=org_unit_name,
            )
        )
    return entries


def _transliterate_token(value):
    result = []
    for char in value.lower():
        if char in _CYRILLIC_TO_LATIN:
            result.append(_CYRILLIC_TO_LATIN[char])
        elif char.isascii() and char.isalnum():
            result.append(char)
    return "".join(result)


def _build_username_base(full_name, external_id):
    parts = [item for item in full_name.split() if item]
    if not parts:
        return f"user_{external_id}"

    surname = _transliterate_token(parts[0]) or "user"
    initials = "".join(_transliterate_token(part[:1]) for part in parts[1:3])
    if initials:
        return f"{surname}_{initials}"
    return f"{surname}_{external_id}"


def _generate_username(full_name, external_id):
    User = get_user_model()
    base_username = _build_username_base(full_name, external_id)
    username = base_username
    suffix = 2
    while User.objects.filter(username=username).exists():
        username = f"{base_username}_{suffix}"
        suffix += 1
    return username


def _build_org_unit_code(name):
    transliterated = [_transliterate_token(chunk) for chunk in re.split(r"[^0-9A-Za-zА-Яа-яЁё]+", name) if chunk]
    code = "-".join(filter(None, transliterated)).strip("-")
    return code[:64]


def _guess_org_unit_type(name):
    lower_name = name.lower()
    if "комис" in lower_name or "совет" in lower_name:
        return OrgUnitType.COMMITTEE
    if lower_name.startswith("кафедра") or any(
        token in lower_name
        for token in ("институт", "колледж", "факультет", "лицей")
    ):
        return OrgUnitType.DEPARTMENT
    return OrgUnitType.OFFICE


def _get_or_create_position(position_name, cache, stats):
    cached = cache.get(position_name)
    if cached is not None:
        return cached

    position, created = Position.objects.get_or_create(name=position_name)
    if created:
        stats["positions_created"] += 1
    cache[position_name] = position
    return position


def _get_or_create_org_unit(org_unit_name, cache, stats):
    if not org_unit_name:
        return None

    cached = cache.get(org_unit_name)
    if cached is not None:
        return cached

    org_unit, created = OrgUnit.objects.get_or_create(
        name=org_unit_name,
        defaults={
            "code": _build_org_unit_code(org_unit_name),
            "type": _guess_org_unit_type(org_unit_name),
            "is_active": True,
        },
    )
    if created:
        stats["org_units_created"] += 1
    cache[org_unit_name] = org_unit
    return org_unit


def _should_update_org_unit(user, org_unit):
    if org_unit is None:
        return False
    if user.org_unit_id is None:
        return True
    if user.org_unit_id == org_unit.id:
        return False
    return not user.groups.exists()


def _extract_chair_org_unit(org_unit):
    if org_unit is None:
        return None
    if org_unit.name.startswith("Кафедра"):
        return org_unit
    return None


def _sync_chair_head_role(user, *, position, chair_org_unit):
    role = ensure_chair_head_role_for_org_unit(chair_org_unit)
    should_have_role = chair_org_unit is not None and is_chair_head_position(position)
    if should_have_role:
        user.groups.add(role)
        return
    user.groups.remove(role)


def sync_staff_directory_entries(entries):
    User = get_user_model()
    stats = {
        "entries_total": 0,
        "users_created": 0,
        "users_updated": 0,
        "positions_created": 0,
        "org_units_created": 0,
    }
    position_cache = {}
    org_unit_cache = {}
    users_by_external_id = {
        user.external_directory_id: user
        for user in User.objects.exclude(external_directory_id__isnull=True).exclude(external_directory_id="")
    }
    users_by_full_name = {
        user.get_full_name().strip(): user
        for user in User.objects.all()
        if user.get_full_name().strip()
    }

    for entry in entries:
        stats["entries_total"] += 1
        position = _get_or_create_position(entry.position_name, position_cache, stats)
        org_unit = _get_or_create_org_unit(entry.org_unit_name, org_unit_cache, stats)
        chair_org_unit = _extract_chair_org_unit(org_unit)

        user = users_by_external_id.get(entry.external_id) or users_by_full_name.get(entry.full_name)
        if user is None:
            username = _generate_username(entry.full_name, entry.external_id)
            user = User.objects.create_user(
                username=username,
                password=settings.DEFAULT_USER_PASSWORD,
                first_name=entry.full_name,
                last_name="",
                email="",
                position=position,
                org_unit=org_unit,
                chair_org_unit=chair_org_unit,
                external_directory_id=entry.external_id,
                is_active=True,
            )
            _sync_chair_head_role(user, position=position, chair_org_unit=chair_org_unit)
            stats["users_created"] += 1
            users_by_external_id[entry.external_id] = user
            users_by_full_name[entry.full_name] = user
            continue

        if user.username == settings.ROOT_ADMIN_USERNAME:
            continue

        update_fields = []
        if user.position_id != position.id:
            user.position = position
            update_fields.append("position")
        if user.external_directory_id != entry.external_id:
            user.external_directory_id = entry.external_id
            update_fields.append("external_directory_id")
        if user.chair_org_unit_id != (chair_org_unit.id if chair_org_unit is not None else None):
            user.chair_org_unit = chair_org_unit
            update_fields.append("chair_org_unit")
        if _should_update_org_unit(user, org_unit):
            user.org_unit = org_unit
            update_fields.append("org_unit")

        if update_fields:
            user.save(update_fields=update_fields)
            stats["users_updated"] += 1

        _sync_chair_head_role(user, position=position, chair_org_unit=chair_org_unit)

        users_by_external_id[entry.external_id] = user
        users_by_full_name[entry.full_name] = user

    return stats


def sync_staff_directory_html(html_text):
    return sync_staff_directory_entries(parse_staff_directory_html(html_text))
