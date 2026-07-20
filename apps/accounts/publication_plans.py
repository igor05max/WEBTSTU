import posixpath
import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from xml.etree import ElementTree

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import PublicationPlanItem
from apps.submissions.models import Submission, SubmissionStatus


XML_NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pkg_rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}

ARTICLE_RE = re.compile(
    r"стать[ьяи]\s+в\s+журнале\s+"
    r"(?P<journal>.+?)\s+"
    r"[\"«](?P<title>.+?)[\"»]\s*"
    r"\((?P<level>[БB]\s*[СC]\s*\d+|Q\s*\d+)\)",
    re.IGNORECASE | re.DOTALL,
)
SPACE_RE = re.compile(r"\s+")
JOURNAL_KEY_RE = re.compile(r"[^0-9A-ZА-ЯЁ]+")


@dataclass
class ParsedPlanItem:
    level: str
    journal_name: str
    article_title: str
    raw_text: str
    source_sheet: str
    source_cell: str


def normalize_plan_level(value):
    normalized = SPACE_RE.sub("", str(value or "").strip().upper())
    normalized = normalized.replace("B", "Б").replace("C", "С")
    if normalized.startswith("БС"):
        suffix = normalized[2:]
        return f"БС{suffix}" if suffix.isdigit() else normalized
    if normalized.startswith("Q"):
        suffix = normalized[1:]
        return f"Q{suffix}" if suffix.isdigit() else normalized
    return normalized


def normalize_text(value):
    return SPACE_RE.sub(" ", str(value or "").strip())


def normalize_journal_key(value):
    return JOURNAL_KEY_RE.sub("", normalize_text(value).upper())


def _read_shared_strings(archive):
    try:
        raw_xml = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []

    root = ElementTree.fromstring(raw_xml)
    strings = []
    for item in root.findall("main:si", XML_NS):
        parts = [node.text or "" for node in item.findall(".//main:t", XML_NS)]
        strings.append("".join(parts))
    return strings


def _read_workbook_sheets(archive):
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    relationship_targets = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in relationships.findall("pkg_rel:Relationship", XML_NS)
        if "Id" in rel.attrib and "Target" in rel.attrib
    }

    sheets = []
    for sheet in workbook.findall("main:sheets/main:sheet", XML_NS):
        rel_id = sheet.attrib.get(f"{{{XML_NS['rel']}}}id")
        target = relationship_targets.get(rel_id)
        if not target:
            continue
        path = posixpath.normpath(posixpath.join("xl", target))
        sheets.append((sheet.attrib.get("name", path), path))
    return sheets


def _cell_text(cell, shared_strings):
    cell_type = cell.attrib.get("t")
    if cell_type == "s":
        value_node = cell.find("main:v", XML_NS)
        if value_node is None or value_node.text is None:
            return ""
        try:
            return shared_strings[int(value_node.text)]
        except (IndexError, ValueError):
            return ""
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//main:t", XML_NS))

    value_node = cell.find("main:v", XML_NS)
    return value_node.text if value_node is not None and value_node.text is not None else ""


def parse_publication_plan_xlsx(file_obj):
    with zipfile.ZipFile(file_obj) as archive:
        shared_strings = _read_shared_strings(archive)
        sheets = _read_workbook_sheets(archive)
        parsed_items = []
        for sheet_name, sheet_path in sheets:
            root = ElementTree.fromstring(archive.read(sheet_path))
            for cell in root.findall(".//main:c", XML_NS):
                text = normalize_text(_cell_text(cell, shared_strings))
                if not text or "стать" not in text.lower() or "(" not in text:
                    continue

                for match in ARTICLE_RE.finditer(text):
                    parsed_items.append(
                        ParsedPlanItem(
                            level=normalize_plan_level(match.group("level")),
                            journal_name=normalize_text(match.group("journal")),
                            article_title=normalize_text(match.group("title")),
                            raw_text=normalize_text(match.group(0)),
                            source_sheet=sheet_name,
                            source_cell=cell.attrib.get("r", ""),
                        )
                    )
        return parsed_items


def save_publication_plan_items(plan, parsed_items):
    with transaction.atomic():
        plan.items.all().delete()
        PublicationPlanItem.objects.bulk_create(
            [
                PublicationPlanItem(
                    plan=plan,
                    level=item.level,
                    journal_name=item.journal_name,
                    article_title=item.article_title,
                    raw_text=item.raw_text,
                    source_sheet=item.source_sheet,
                    source_cell=item.source_cell,
                    order=index,
                )
                for index, item in enumerate(parsed_items, start=1)
            ],
            batch_size=500,
        )
        plan.parsed_at = timezone.now()
        plan.save(update_fields=["parsed_at"])
    return parsed_items


def replace_publication_plan_items(plan):
    with plan.file.open("rb") as source:
        parsed_items = parse_publication_plan_xlsx(source)
    return save_publication_plan_items(plan, parsed_items)


def submission_publication_level(submission):
    white_list_level = getattr(submission.journal, "white_list_level", None)
    if white_list_level is None:
        return ""
    return f"БС{white_list_level}"


def _build_plan_journal_level_map(plan_items):
    levels_by_journal = {}
    for item in plan_items:
        journal_key = normalize_journal_key(item.journal_name)
        if not journal_key:
            continue
        levels_by_journal.setdefault(journal_key, set()).add(normalize_plan_level(item.level))

    return {
        journal_key: next(iter(levels))
        for journal_key, levels in levels_by_journal.items()
        if len(levels) == 1
    }


def _fallback_plan_level_for_submission(submission, plan_journal_levels):
    journal_name = getattr(submission.journal, "name", "")
    journal_key = normalize_journal_key(journal_name)
    if not journal_key:
        return ""
    if journal_key in plan_journal_levels:
        return plan_journal_levels[journal_key]

    for plan_journal_key, level in plan_journal_levels.items():
        if journal_key in plan_journal_key or plan_journal_key in journal_key:
            return level
    return ""


def submission_effective_publication_level(submission, plan_journal_levels=None):
    level = submission_publication_level(submission)
    if level:
        return level
    if not plan_journal_levels:
        return ""
    return _fallback_plan_level_for_submission(submission, plan_journal_levels)


def _submission_level_counts(queryset, plan_journal_levels):
    counts = Counter()
    submissions_by_level = {}
    for submission in queryset:
        level = submission_effective_publication_level(submission, plan_journal_levels)
        if not level:
            continue
        counts[level] += 1
        submissions_by_level.setdefault(level, []).append(submission)
    return counts, submissions_by_level


def _level_sort_key(level):
    level = normalize_plan_level(level)
    match = re.match(r"^(БС|Q)(\d+)$", level)
    if not match:
        return (9, level)
    prefix, number = match.groups()
    prefix_order = 0 if prefix == "БС" else 1
    return (prefix_order, int(number))


def build_publication_plan_progress(author):
    plan = getattr(author, "publication_plan", None)
    plan_items = list(plan.items.all()) if plan is not None else []
    planned_counts = Counter(normalize_plan_level(item.level) for item in plan_items)
    plan_journal_levels = _build_plan_journal_level_map(plan_items)

    author_submissions = (
        Submission.objects.filter(authors=author)
        .select_related("journal")
        .prefetch_related("authors")
        .distinct()
    )
    sent_submissions = author_submissions.filter(submitted_at__isnull=False).exclude(status=SubmissionStatus.DRAFT)
    approved_submissions = author_submissions.filter(status=SubmissionStatus.APPROVED)
    sent_counts, sent_by_level = _submission_level_counts(sent_submissions, plan_journal_levels)
    approved_counts, approved_by_level = _submission_level_counts(approved_submissions, plan_journal_levels)

    all_levels = sorted(
        set(planned_counts) | set(sent_counts) | set(approved_counts),
        key=_level_sort_key,
    )
    rows = []
    for level in all_levels:
        planned = planned_counts[level]
        approved = approved_counts[level]
        remaining = max(planned - approved, 0) if planned else 0
        rows.append(
            {
                "level": level,
                "planned": planned,
                "sent": sent_counts[level],
                "approved": approved,
                "remaining": remaining,
                "progress": f"{min(approved, planned)}/{planned}" if planned else f"{approved}/0",
                "is_complete": planned > 0 and approved >= planned,
                "sent_submissions": sent_by_level.get(level, []),
                "approved_submissions": approved_by_level.get(level, []),
            }
        )

    unmatched_plan_items = [
        item for item in plan_items if item.level.startswith("Q")
    ]
    unknown_level_submissions = [
        submission
        for submission in author_submissions
        if submission.status == SubmissionStatus.APPROVED
        and not submission_effective_publication_level(submission, plan_journal_levels)
    ]

    return {
        "plan": plan,
        "plan_items": plan_items,
        "rows": rows,
        "planned_total": sum(planned_counts.values()),
        "sent_total": sum(sent_counts.values()),
        "approved_total": sum(approved_counts.values()),
        "unmatched_plan_items": unmatched_plan_items,
        "unknown_level_submissions": unknown_level_submissions,
    }
