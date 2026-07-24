import io
import re
import unicodedata
import zipfile
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree


WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
W = f"{{{WORD_NS}}}"
R = f"{{{REL_NS}}}"

SUPPORTED_EXTENSIONS = {".docx", ".doc", ".pdf", ".tex", ".txt", ".md", ".rtf"}
TEXT_EXTENSIONS = {".tex", ".txt", ".md", ".rtf"}
DANGEROUS_EXTENSIONS = {
    ".bat",
    ".cmd",
    ".com",
    ".dll",
    ".exe",
    ".hta",
    ".jar",
    ".js",
    ".jse",
    ".lnk",
    ".msi",
    ".ps1",
    ".scr",
    ".vbs",
    ".vbe",
    ".wsf",
}

SPACE_RE = re.compile(r"\s+")
EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,63}(?![\w.-])", re.I)
URL_RE = re.compile(r"https?://[^\s<>\]\[(){}]+", re.I)
DOI_LABEL_RE = re.compile(r"\bDOI[ \t]*:?[ \t]*([^\s,;]+)?", re.I)
DOI_RE = re.compile(r"^10\.\d{4,9}/[-._;()/:A-Z0-9]+$", re.I)
ORCID_LABEL_RE = re.compile(r"\bORCID[ \t]*:?[ \t]*([^\s,;]+)?", re.I)
EDN_LABEL_RE = re.compile(r"\bEDN[ \t]*:?[ \t]*([^\s,;]+)?", re.I)
FIGURE_CAPTION_RE = re.compile(r"^\s*(?:рис(?:унок)?\.?|figure)\s*(\d+)\b", re.I)
TABLE_CAPTION_RE = re.compile(r"^\s*(?:таблица|table)\s*(\d+)\b", re.I)
FIGURE_REFERENCE_RE = re.compile(r"\b(?:рис(?:унке|унка|унок)?\.?|figure)\s*(\d+)\b", re.I)
TABLE_REFERENCE_RE = re.compile(r"\b(?:табл(?:ице|ицы|ица)?\.?|table)\s*(\d+)\b", re.I)
CITATION_RE = re.compile(r"\[([^\]]{1,120})\]")
COMPACT_AUTHOR_RE = re.compile(
    r"(?:[А-ЯЁA-Z]\s*\.\s*){1,2}[А-ЯЁA-Z][А-ЯЁа-яёA-Za-z'’-]+"
    r"|[А-ЯЁA-Z][А-ЯЁа-яёA-Za-z'’-]+\s+(?:[А-ЯЁA-Z]\s*\.\s*){1,2}"
)
FULL_NAME_RE = re.compile(
    r"\b[А-ЯЁ][а-яё'’-]+\s+[А-ЯЁ][а-яё'’-]+(?:\s+[А-ЯЁ][а-яё'’-]+)?\b"
)
SECTION_ALIASES = {
    "Введение": ("введение", "introduction"),
    "Материалы и методы": ("материалы и методы", "материал и методы", "методы", "materials and methods"),
    "Результаты": ("результаты", "results"),
    "Обсуждение": ("обсуждение", "discussion"),
    "Заключение": ("заключение", "выводы", "conclusion", "conclusions"),
    "Список литературы": (
        "список литературы",
        "список использованной литературы",
        "список использованных источников",
        "список использованных источников и литературы",
        "список литературы и источников",
        "список источников и литературы",
        "использованная литература",
        "использованные источники",
        "библиографический список",
        "библиография",
        "литература",
        "источники",
        "references",
        "reference list",
    ),
}


def normalize_space(value):
    return SPACE_RE.sub(" ", (value or "").replace("\x00", " ")).strip()


def normalize_for_match(value):
    return normalize_space(unicodedata.normalize("NFKC", value or "")).casefold().replace("ё", "е")


def read_file_bytes(uploaded_file):
    position = None
    try:
        position = uploaded_file.tell()
    except (AttributeError, OSError):
        pass
    try:
        uploaded_file.seek(0)
        return uploaded_file.read()
    finally:
        if position is not None:
            try:
                uploaded_file.seek(position)
            except (AttributeError, OSError):
                pass


def _decode_text(data):
    variants = []
    for encoding in ("utf-8", "utf-8-sig", "cp1251", "utf-16le"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        variants.append(text)
    if not variants:
        return data.decode("utf-8", errors="ignore")
    return max(
        variants,
        key=lambda value: (
            sum("А" <= char <= "я" or char in "Ёё" for char in value),
            sum(char.isalpha() for char in value),
        ),
    )


def _element_text_without_nested_paragraphs(element):
    chunks = []

    def visit(node, *, is_root=False):
        if not is_root and node.tag == W + "p":
            return
        if node.tag == W + "t" and node.text:
            chunks.append(node.text)
            return
        if node.tag in {W + "tab", W + "br", W + "cr"}:
            chunks.append(" ")
            return
        for child in node:
            visit(child)

    visit(element, is_root=True)
    return normalize_space("".join(chunks))


def _style_names(archive):
    names = {}
    try:
        root = ElementTree.fromstring(archive.read("word/styles.xml"))
    except (KeyError, ElementTree.ParseError):
        return names
    for style in root.findall(f".//{W}style"):
        style_id = style.attrib.get(W + "styleId", "")
        name = style.find(W + "name")
        if style_id and name is not None:
            names[style_id] = name.attrib.get(W + "val", style_id)
    return names


def _paragraph_records(root, styles):
    records = []
    for index, paragraph in enumerate(root.iter(W + "p")):
        text = _element_text_without_nested_paragraphs(paragraph)
        if not text:
            continue
        style_id = ""
        style_element = paragraph.find(f"./{W}pPr/{W}pStyle")
        if style_element is not None:
            style_id = style_element.attrib.get(W + "val", "")
        records.append(
            {
                "index": index,
                "text": text,
                "style": styles.get(style_id, style_id),
            }
        )
    return records


def _table_records(root):
    tables = []
    for table_index, table in enumerate(root.iter(W + "tbl")):
        rows = []
        for row in table.findall(f"./{W}tr"):
            cells = []
            for cell in row.findall(f"./{W}tc"):
                cell_parts = []
                for paragraph in cell.findall(f"./{W}p"):
                    text = _element_text_without_nested_paragraphs(paragraph)
                    if text:
                        cell_parts.append(text)
                cells.append(normalize_space(" ".join(cell_parts)))
            rows.append(cells)
        tables.append({"index": table_index, "rows": rows})
    return tables


def _relationships(archive):
    external = []
    image_targets = set()
    dangerous_targets = []
    for name in archive.namelist():
        if not name.endswith(".rels"):
            continue
        try:
            root = ElementTree.fromstring(archive.read(name))
        except ElementTree.ParseError:
            continue
        for relation in root:
            target = relation.attrib.get("Target", "")
            relation_type = relation.attrib.get("Type", "")
            if relation_type.endswith("/image"):
                image_targets.add(target)
            if relation.attrib.get("TargetMode") != "External":
                continue
            external.append(target)
            parsed = urlparse(target)
            if target.startswith("\\\\") or parsed.scheme.casefold() in {"file", "javascript", "smb"}:
                dangerous_targets.append(target)
    return sorted(external), sorted(image_targets), sorted(dangerous_targets)


def _inspect_archive_members(archive):
    members = []
    dangerous = []
    compressed_total = 0
    uncompressed_total = 0
    for info in archive.infolist():
        members.append(info.filename)
        compressed_total += max(0, info.compress_size)
        uncompressed_total += max(0, info.file_size)
        suffix = Path(info.filename).suffix.casefold()
        lowered = info.filename.casefold()
        if (
            suffix in DANGEROUS_EXTENSIONS
            or "vbaproject.bin" in lowered
            or lowered.startswith("word/embeddings/")
            or lowered.startswith("embeddings/")
        ):
            dangerous.append(info.filename)
    ratio = round(uncompressed_total / max(compressed_total, 1), 2)
    return members, dangerous, ratio, uncompressed_total


def _parse_docx(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = set(archive.namelist())
        if "word/document.xml" not in names:
            raise ValueError("В DOCX отсутствует основной XML документа.")
        root = ElementTree.fromstring(archive.read("word/document.xml"))
        paragraphs = _paragraph_records(root, _style_names(archive))
        tables = _table_records(root)
        external, image_targets, dangerous_targets = _relationships(archive)
        members, dangerous_members, compression_ratio, unpacked_size = _inspect_archive_members(archive)

    all_text = "\n".join(record["text"] for record in paragraphs)
    return {
        "paragraphs": paragraphs,
        "tables": tables,
        "text": all_text,
        "image_count": len(image_targets),
        "external_relationships": external,
        "dangerous_relationships": dangerous_targets,
        "archive_members": members,
        "dangerous_members": dangerous_members,
        "compression_ratio": compression_ratio,
        "unpacked_size": unpacked_size,
        "parse_error": "",
    }


def analyze_document_bytes(data, file_name):
    suffix = Path(file_name or "").suffix.casefold()
    base = {
        "file_name": Path(file_name or "document").name,
        "suffix": suffix,
        "size": len(data),
        "magic_hex": data[:8].hex(),
        "paragraphs": [],
        "tables": [],
        "text": "",
        "image_count": 0,
        "external_relationships": [],
        "dangerous_relationships": [],
        "archive_members": [],
        "dangerous_members": [],
        "compression_ratio": 1.0,
        "unpacked_size": len(data),
        "parse_error": "",
    }
    if suffix == ".docx":
        try:
            base.update(_parse_docx(data))
        except (OSError, ValueError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
            base["parse_error"] = str(exc) or "DOCX поврежден или имеет неверную структуру."
    elif suffix in TEXT_EXTENSIONS:
        if suffix == ".tex":
            from document_template_engine import latex_to_plain_text

            text = latex_to_plain_text(data)
        else:
            text = _decode_text(data)
        paragraphs = [normalize_space(item) for item in re.split(r"[\r\n]+", text)]
        base["paragraphs"] = [
            {"index": index, "text": value, "style": ""}
            for index, value in enumerate(paragraphs)
            if value
        ]
        base["text"] = "\n".join(item["text"] for item in base["paragraphs"])
    elif suffix in {".doc", ".pdf"}:
        base["parse_error"] = (
            "Файл принят, но глубокий анализ структуры этого формата ограничен. "
            "Для полной проверки используйте DOCX."
        )
    else:
        base["parse_error"] = "Формат файла не поддерживается."

    base["metadata"] = extract_metadata(base)
    return base


def _uppercase_ratio(value):
    letters = [char for char in value if char.isalpha()]
    if not letters:
        return 0
    return sum(char.isupper() for char in letters) / len(letters)


def _extract_title(paragraphs):
    candidates = []
    for order, paragraph in enumerate(paragraphs[:35]):
        text = paragraph["text"]
        normalized = normalize_for_match(text)
        if len(text) < 25 or len(text) > 700:
            continue
        if any(marker in normalized for marker in ("удк", "doi:", "аннотация", "ключевые слова", "@")):
            continue
        score = 0
        if _uppercase_ratio(text) >= 0.72:
            score += 5
        if "heading" in paragraph.get("style", "").casefold():
            score += 2
        if order < 20:
            score += 1
        score += min(len(text) / 120, 2)
        candidates.append((score, -order, text))
    return max(candidates, default=(0, 0, ""))[2]


def _author_surname(value):
    tokens = re.findall(r"[А-ЯЁA-Z][А-ЯЁа-яёA-Za-z'’-]+", value)
    if not tokens:
        return normalize_for_match(value)
    non_initials = [token for token in tokens if len(token.replace(".", "")) > 1]
    return normalize_for_match(non_initials[-1] if non_initials else tokens[-1])


def _extract_authors(paragraphs, title):
    title_index = next((index for index, item in enumerate(paragraphs) if item["text"] == title), -1)
    search_records = paragraphs[title_index + 1 : title_index + 12] if title_index >= 0 else paragraphs[:20]
    candidates = []
    for paragraph in search_records:
        text = paragraph["text"]
        if "@" in text or len(text) > 300:
            continue
        matches = [normalize_space(match.group(0)) for match in COMPACT_AUTHOR_RE.finditer(text)]
        if len(matches) >= 1:
            candidates.extend(matches)
            if len(matches) >= 2:
                break

    if not candidates:
        for paragraph in paragraphs[:15]:
            text = paragraph["text"]
            if "@" in text or len(text) > 160:
                continue
            matches = [normalize_space(match.group(0)) for match in FULL_NAME_RE.finditer(text)]
            if matches:
                candidates.extend(matches)

    result = []
    seen = set()
    for candidate in candidates:
        surname = _author_surname(candidate)
        if not surname or surname in seen:
            continue
        seen.add(surname)
        result.append(candidate)
    return result[:30]


def _extract_prefixed_value(paragraphs, prefixes):
    for paragraph in paragraphs:
        text = paragraph["text"]
        normalized = normalize_for_match(text)
        for prefix in prefixes:
            normalized_prefix = normalize_for_match(prefix)
            if normalized.startswith(normalized_prefix):
                value = re.sub(r"^[^:]{1,60}:\s*", "", text, count=1)
                return normalize_space(value)
    return ""


def _unique_emails(text):
    emails = []
    seen = set()
    for email in EMAIL_RE.findall(text or ""):
        lowered = email.casefold()
        if lowered not in seen:
            seen.add(lowered)
            emails.append(email)
    return emails


def _extract_contact_emails(paragraphs, title, full_text):
    """Prefer the article metadata block and ignore Word header/text-box duplicates."""
    title_index = next((index for index, item in enumerate(paragraphs) if item["text"] == title), -1)
    if title_index >= 0:
        metadata_lines = []
        for paragraph in paragraphs[title_index + 1 : title_index + 16]:
            text = paragraph["text"]
            normalized = normalize_for_match(text)
            if normalized.startswith(("ключевые слова", "keywords", "аннотация", "abstract")):
                break
            metadata_lines.append(text)
        emails = _unique_emails("\n".join(metadata_lines))
        if emails:
            return emails
    return _unique_emails(full_text)


def extract_metadata(snapshot):
    paragraphs = snapshot.get("paragraphs") or []
    title = _extract_title(paragraphs)
    authors = _extract_authors(paragraphs, title)
    abstract = _extract_prefixed_value(paragraphs, ("Аннотация:", "Abstract:"))
    keywords = _extract_prefixed_value(paragraphs, ("Ключевые слова:", "Keywords:"))
    emails = _extract_contact_emails(paragraphs, title, snapshot.get("text") or "")

    organizations = []
    seen_organizations = set()
    organization_markers = (
        "кафедр",
        "фгбоу",
        "университет",
        "институт",
        "академ",
        "organization",
        "university",
    )
    for paragraph in paragraphs[:35]:
        text = paragraph["text"]
        normalized = normalize_for_match(text)
        if any(marker in normalized for marker in organization_markers):
            if normalized not in seen_organizations:
                seen_organizations.add(normalized)
                organizations.append(text)

    return {
        "title": title,
        "authors": authors,
        "document_authors": "\n".join(authors),
        "organizations": "\n".join(organizations),
        "emails": emails,
        "contact_emails": ", ".join(emails),
        "abstract": abstract,
        "keywords": keywords,
    }


def _parse_author_identity(author):
    normalized = normalize_for_match(author)
    surname = _author_surname(author)
    initials = []
    for letter in re.findall(r"([а-яёa-z])\s*\.", normalized, re.I):
        initials.append(letter.casefold().replace("ё", "е"))
    if not initials:
        tokens = re.findall(r"[а-яёa-z'’-]+", normalized, re.I)
        if tokens and normalize_for_match(tokens[0]) == surname:
            initials = [token[0] for token in tokens[1:3] if token]
    return surname, initials


def match_authors_to_users(authors, users):
    matches = []
    used_ids = set()
    for author in authors:
        surname, initials = _parse_author_identity(author)
        best = None
        best_score = 0
        for user in users:
            if user.id in used_ids:
                continue
            display = normalize_space(user.get_full_name() or user.username)
            tokens = re.findall(r"[А-ЯЁа-яёA-Za-z'’-]+", display)
            if not tokens:
                continue
            user_surname = normalize_for_match(user.last_name or tokens[0])
            if user_surname != surname:
                continue
            score = 5
            user_initials = [normalize_for_match(token[0]) for token in tokens[1:3] if token]
            if initials:
                score += sum(
                    index < len(user_initials) and initial == user_initials[index]
                    for index, initial in enumerate(initials[:2])
                )
            if score > best_score:
                best = user
                best_score = score
        if best is not None:
            used_ids.add(best.id)
            matches.append(
                {
                    "author": author,
                    "user_id": best.id,
                    "user_name": str(best),
                    "username": best.username,
                }
            )
    return matches
