import io
import posixpath
import re
import statistics
import unicodedata
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse
from xml.etree import ElementTree


WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MATH_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
VML_NS = "urn:schemas-microsoft-com:vml"
W = f"{{{WORD_NS}}}"
R = f"{{{REL_NS}}}"
DR = f"{{{DOC_REL_NS}}}"
M = f"{{{MATH_NS}}}"
A = f"{{{DRAWING_NS}}}"
V = f"{{{VML_NS}}}"

SUPPORTED_EXTENSIONS = {
    ".docx",
    ".dotx",
    ".doc",
    ".pdf",
    ".tex",
    ".txt",
    ".md",
    ".rtf",
}
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
    r"\b[А-ЯЁ][А-ЯЁа-яё'’-]+\s+[А-ЯЁ][А-ЯЁа-яё'’-]+"
    r"(?:\s+[А-ЯЁ][А-ЯЁа-яё'’-]+)?\b"
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
    # Prefer valid Unicode encodings. CP1251 can decode almost any byte
    # sequence and otherwise wins the Cyrillic heuristic even for valid UTF-8,
    # producing mojibake such as "РџСЂРѕРІ...".
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    for encoding in ("cp1251", "utf-16le"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def _latex_command_values(source):
    source = re.sub(r"(?<!\\)%[^\r\n]*", "", str(source or ""))
    values = {}
    command_re = re.compile(r"\\([A-Za-z@]+)\*?\s*(?:\[[^\]]*\]\s*)?\{")
    position = 0
    while True:
        match = command_re.search(source, position)
        if not match:
            break
        depth = 1
        index = match.end()
        value_start = index
        while index < len(source) and depth:
            if source[index] == "{" and (index == 0 or source[index - 1] != "\\"):
                depth += 1
            elif source[index] == "}" and (index == 0 or source[index - 1] != "\\"):
                depth -= 1
            index += 1
        if depth == 0:
            values.setdefault(match.group(1).casefold(), []).append(
                source[value_start : index - 1]
            )
        position = max(match.end(), index)
    return values


def _clean_latex_metadata(value):
    text = str(value or "")
    text = re.sub(r"\\(?:orcid[A-Za-z]?|thanks|footnote)\*?\{[^{}]*\}", "", text)
    text = re.sub(r"\\(?:quad|qquad|and|newline|linebreak)\b", " ", text)
    text = re.sub(r"\\[A-Za-z@]+\*?(?:\[[^\]]*\])?", " ", text)
    text = text.replace(r"\&", "&").replace(r"\%", "%").replace("~", " ")
    text = text.replace("{", " ").replace("}", " ")
    return normalize_space(text)


def _latex_bibliography_entries(source):
    """Return one normalized reference for every explicit ``\bibitem``."""

    source = re.sub(r"(?<!\\)%[^\r\n]*", "", str(source or ""))
    environment = re.search(
        r"\\begin\s*\{thebibliography\}(?:\s*\{[^{}]*\})?(.*?)"
        r"\\end\s*\{thebibliography\}",
        source,
        re.I | re.S,
    )
    if not environment:
        return []

    body = environment.group(1)
    markers = list(
        re.finditer(
            r"\\bibitem(?:\s*\[[^\]]*\])?\s*\{[^{}]*\}",
            body,
            re.I,
        )
    )
    references = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(body)
        value = body[marker.end() : end]
        value = re.sub(
            r"\\(?:newblock|allowbreak|urlprefix)\b",
            " ",
            value,
            flags=re.I,
        )
        value = value.replace(r"\_", "_").replace(r"\#", "#")
        cleaned = _clean_latex_metadata(value)
        if cleaned:
            references.append(cleaned)
    return references


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


def _on_off(element):
    if element is None:
        return None
    return str(element.attrib.get(W + "val", "true")).casefold() not in {
        "0",
        "false",
        "off",
        "no",
    }


def _half_points(element):
    if element is None:
        return None
    try:
        return round(float(element.attrib.get(W + "val")) / 2, 1)
    except (TypeError, ValueError):
        return None


def _dominant_weighted(values):
    totals = {}
    for value, weight in values:
        if value in (None, "") or weight <= 0:
            continue
        totals[value] = totals.get(value, 0) + weight
    return max(totals, key=totals.get) if totals else None


def _paragraph_properties(paragraph):
    alignments = {
        "both": "justify",
        "center": "center",
        "distribute": "distribute",
        "end": "right",
        "left": "left",
        "right": "right",
        "start": "left",
    }
    paragraph_properties = paragraph.find(f"./{W}pPr")
    alignment_element = (
        paragraph_properties.find(f"./{W}jc")
        if paragraph_properties is not None
        else None
    )
    outline_element = (
        paragraph_properties.find(f"./{W}outlineLvl")
        if paragraph_properties is not None
        else None
    )
    alignment = ""
    if alignment_element is not None:
        raw_alignment = alignment_element.attrib.get(W + "val", "").casefold()
        alignment = alignments.get(raw_alignment, raw_alignment)
    try:
        outline_level = (
            int(outline_element.attrib.get(W + "val"))
            if outline_element is not None
            else None
        )
    except (TypeError, ValueError):
        outline_level = None

    font_names = []
    font_sizes = []
    bold_weights = []
    italic_weights = []
    for run in paragraph.findall(f".//{W}r"):
        run_text = "".join(node.text or "" for node in run.findall(f".//{W}t"))
        weight = max(1, len(run_text.strip()))
        properties = run.find(f"./{W}rPr")
        if properties is None:
            continue
        fonts = properties.find(f"./{W}rFonts")
        if fonts is not None:
            font_name = next(
                (
                    fonts.attrib.get(W + key)
                    for key in ("ascii", "hAnsi", "eastAsia", "cs")
                    if fonts.attrib.get(W + key)
                ),
                None,
            )
            font_names.append((font_name, weight))
        size_element = properties.find(f"./{W}sz")
        if size_element is None:
            size_element = properties.find(f"./{W}szCs")
        font_sizes.append((_half_points(size_element), weight))
        bold_weights.append((_on_off(properties.find(f"./{W}b")), weight))
        italic_weights.append((_on_off(properties.find(f"./{W}i")), weight))

    def dominant_boolean(values):
        present = [(value, weight) for value, weight in values if value is not None]
        if not present:
            return None
        true_weight = sum(weight for value, weight in present if value)
        total_weight = sum(weight for _value, weight in present)
        return true_weight >= total_weight / 2

    return {
        "alignment": alignment,
        "outline_level": outline_level,
        "font_family": _dominant_weighted(font_names) or "",
        "font_size_pt": _dominant_weighted(font_sizes),
        "bold": dominant_boolean(bold_weights),
        "italic": dominant_boolean(italic_weights),
    }


def _paragraph_records(root, styles, *, region="document"):
    parent_map = {
        child: parent
        for parent in root.iter()
        for child in parent
    }
    records = []
    for index, paragraph in enumerate(root.iter(W + "p")):
        text = _element_text_without_nested_paragraphs(paragraph)
        if not text:
            continue
        style_id = ""
        style_element = paragraph.find(f"./{W}pPr/{W}pStyle")
        if style_element is not None:
            style_id = style_element.attrib.get(W + "val", "")
        container = "body"
        ancestor = parent_map.get(paragraph)
        while ancestor is not None:
            if ancestor.tag == W + "txbxContent":
                container = "textbox"
                break
            if ancestor.tag == W + "tc":
                container = "table_cell"
                break
            ancestor = parent_map.get(ancestor)
        records.append(
            {
                "index": index,
                "block_id": f"{region}:p:{index}",
                "text": text,
                "style": styles.get(style_id, style_id),
                "style_id": style_id,
                "region": region,
                "container": container,
                **_paragraph_properties(paragraph),
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
        tables.append(
            {
                "index": table_index,
                "block_id": f"document:table:{table_index}",
                "rows": rows,
            }
        )
    return tables


def _formula_records(root):
    formulas = []
    for index, formula in enumerate(root.iter(M + "oMath")):
        text = normalize_space(
            "".join(node.text or "" for node in formula.iter(M + "t"))
        )
        formulas.append(
            {
                "index": index,
                "block_id": f"document:formula:{index}",
                "text": text,
                "source": "omml",
                "confidence": 1.0,
            }
        )
    return formulas


def _document_relationship_targets(archive):
    try:
        root = ElementTree.fromstring(
            archive.read("word/_rels/document.xml.rels")
        )
    except (KeyError, ElementTree.ParseError):
        return {}
    return {
        relation.attrib.get("Id", ""): relation.attrib.get("Target", "")
        for relation in root
        if relation.attrib.get("Id")
    }


def _figure_records(root, relationship_targets=None, member_sizes=None):
    relationship_targets = relationship_targets or {}
    member_sizes = member_sizes or {}
    relationship_ids = []
    for blip in root.iter(A + "blip"):
        relationship_id = blip.attrib.get(DR + "embed") or blip.attrib.get(DR + "link")
        if relationship_id:
            relationship_ids.append(relationship_id)
    for image_data in root.iter(V + "imagedata"):
        relationship_id = image_data.attrib.get(DR + "id")
        if relationship_id:
            relationship_ids.append(relationship_id)
    records = []
    for index, relationship_id in enumerate(dict.fromkeys(relationship_ids)):
        target = str(relationship_targets.get(relationship_id) or "")
        normalized_target = str(
            PurePosixPath("word") / PurePosixPath(target)
        ).replace("word/../", "")
        size = int(member_sizes.get(normalized_target, 0))
        suffix = Path(target).suffix.casefold()
        kind = (
            "embedded_equation"
            if suffix in {".wmf", ".emf"} and 0 < size < 20_000
            else "figure"
        )
        records.append(
            {
            "index": index,
            "block_id": f"document:figure:{index}",
            "relationship_id": relationship_id,
            "target": target,
            "media_type": suffix.lstrip("."),
            "size_bytes": size,
            "kind": kind,
            "confidence": 1.0,
            }
        )
    return records


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


def _ole_embedding_records(root, relationship_targets):
    records = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "OLEObject":
            continue
        relationship_id = element.attrib.get(DR + "id", "")
        target = str(relationship_targets.get(relationship_id) or "")
        normalized_target = posixpath.normpath(
            target.lstrip("/")
            if target.startswith("/word/")
            else f"word/{target}"
        ).lstrip("/")
        prog_id = str(element.attrib.get("ProgID") or "").strip()
        object_type = str(element.attrib.get("Type") or "").strip()
        is_equation = (
            object_type.casefold() == "embed"
            and prog_id.casefold().startswith(("equation.", "mathtype"))
            and normalized_target.casefold().startswith("word/embeddings/")
        )
        records.append(
            {
                "relationship_id": relationship_id,
                "member": normalized_target,
                "prog_id": prog_id,
                "object_type": object_type,
                "is_equation": is_equation,
            }
        )
    return records


def _inspect_archive_members(archive, *, equation_members=None):
    members = []
    dangerous = []
    embedded_objects = []
    equation_members = {
        str(value).casefold()
        for value in (equation_members or [])
        if value
    }
    compressed_total = 0
    uncompressed_total = 0
    for info in archive.infolist():
        members.append(info.filename)
        compressed_total += max(0, info.compress_size)
        uncompressed_total += max(0, info.file_size)
        suffix = Path(info.filename).suffix.casefold()
        lowered = info.filename.casefold()
        if suffix in DANGEROUS_EXTENSIONS or "vbaproject.bin" in lowered:
            dangerous.append(info.filename)
        elif (
            lowered.startswith("word/embeddings/")
            or lowered.startswith("embeddings/")
        ) and lowered not in equation_members:
            embedded_objects.append(info.filename)
    ratio = round(uncompressed_total / max(compressed_total, 1), 2)
    return members, dangerous, embedded_objects, ratio, uncompressed_total


def _parse_docx(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = set(archive.namelist())
        if "word/document.xml" not in names:
            raise ValueError("В DOCX отсутствует основной XML документа.")
        root = ElementTree.fromstring(archive.read("word/document.xml"))
        styles = _style_names(archive)
        paragraphs = _paragraph_records(root, styles)
        tables = _table_records(root)
        formulas = _formula_records(root)
        member_sizes = {
            info.filename: info.file_size
            for info in archive.infolist()
        }
        relationship_targets = _document_relationship_targets(archive)
        figures = _figure_records(
            root,
            relationship_targets,
            member_sizes,
        )
        ole_embeddings = _ole_embedding_records(root, relationship_targets)
        equation_embeddings = [
            item for item in ole_embeddings if item["is_equation"]
        ]
        embedded_equations = [
            figure for figure in figures if figure.get("kind") == "embedded_equation"
        ]
        content_figures = [
            figure for figure in figures if figure.get("kind") == "figure"
        ]
        formulas.extend(
            {
                "index": len(formulas) + index,
                "block_id": f"document:embedded-formula:{index}",
                "text": "",
                "source": "embedded_vector_equation",
                "relationship_id": figure["relationship_id"],
                "confidence": 0.72,
            }
            for index, figure in enumerate(embedded_equations)
        )
        auxiliary_blocks = []
        auxiliary_parts = [
            name
            for name in names
            if re.fullmatch(
                r"word/(?:header\d+|footer\d+|footnotes|endnotes|comments)\.xml",
                name,
                re.I,
            )
        ]
        for name in sorted(auxiliary_parts):
            try:
                auxiliary_root = ElementTree.fromstring(archive.read(name))
            except ElementTree.ParseError:
                continue
            region_match = re.match(r"word/([a-z]+)", name, re.I)
            region = region_match.group(1).casefold() if region_match else "auxiliary"
            auxiliary_blocks.extend(
                _paragraph_records(auxiliary_root, styles, region=region)
            )
        external, image_targets, dangerous_targets = _relationships(archive)
        (
            members,
            dangerous_members,
            embedded_object_members,
            compression_ratio,
            unpacked_size,
        ) = _inspect_archive_members(
            archive,
            equation_members={
                item["member"]
                for item in equation_embeddings
            },
        )
        ole_embeddings_by_member = {
            item["member"]: item
            for item in ole_embeddings
        }

    all_text = "\n".join(record["text"] for record in paragraphs)
    return {
        "paragraphs": paragraphs,
        "auxiliary_blocks": auxiliary_blocks,
        "tables": tables,
        "text": all_text,
        "figures": content_figures,
        "formulas": formulas,
        "image_count": len(content_figures),
        "embedded_image_count": max(len(figures), len(image_targets)),
        "external_relationships": external,
        "dangerous_relationships": dangerous_targets,
        "archive_members": members,
        "dangerous_members": dangerous_members,
        "embedded_objects": [
            ole_embeddings_by_member.get(
                member,
                {
                    "relationship_id": "",
                    "member": member,
                    "prog_id": "",
                    "object_type": "",
                    "is_equation": False,
                },
            )
            for member in embedded_object_members
        ],
        "equation_embeddings": equation_embeddings,
        "compression_ratio": compression_ratio,
        "unpacked_size": unpacked_size,
        "parse_error": "",
    }


def _pdf_plain_paragraphs(reader):
    paragraph_values = []
    before_abstract = True
    pending = None

    def append_text(left, right):
        if not left:
            return right
        if left.endswith(("-", "‑")) and right[:1].islower():
            return left[:-1] + right
        return f"{left} {right}"

    def flush():
        nonlocal pending
        if pending and pending["text"]:
            paragraph_values.append(pending)
        pending = None

    author_pattern = re.compile(
        r"(?:[А-ЯЁA-Z]\s*\.\s*){1,3}[А-ЯЁA-Z][А-ЯЁа-яёA-Za-z'’-]+"
        r"|[А-ЯЁA-Z][А-ЯЁа-яёA-Za-z'’-]+\s+(?:[А-ЯЁA-Z]\s*\.\s*){1,3}"
    )
    known_headings = {
        normalize_for_match(alias)
        for aliases in SECTION_ALIASES.values()
        for alias in aliases
    }

    for page_number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").replace("\x00", " ")
        lines = [normalize_space(line) for line in text.splitlines()]
        lines = [line for line in lines if line]
        trailing_number_count = sum(
            bool(re.search(r"\s+\d{1,4}\s*$", line))
            for line in lines
        )
        if trailing_number_count >= 8:
            lines = [
                re.sub(r"\s+\d{1,4}\s*$", "", line).rstrip()
                for line in lines
            ]
        if page_number == 1:
            article_index = next(
                (
                    index
                    for index, line in enumerate(lines[:30])
                    if normalize_for_match(line) == "article"
                ),
                None,
            )
            if article_index is not None:
                lines = lines[article_index:]

        expanded_lines = []
        line_index = 0
        while line_index < len(lines):
            line = lines[line_index]
            if (
                re.match(r"^\d+(?:\.\d+){0,3}[.)]?\s+[A-ZА-ЯЁ]", line)
                and not line.rstrip().endswith((".", "!", "?", ":", ";"))
            ):
                combined = line
                lookahead = line_index + 1
                while lookahead < len(lines) and lookahead <= line_index + 2:
                    combined = f"{combined} {lines[lookahead]}"
                    line_index = lookahead
                    if combined.rstrip().endswith((".", "!", "?", ":", ";")):
                        break
                    lookahead += 1
                line = combined
            inline_heading = re.match(
                r"^(?P<head>\d+(?:\.\d+){0,3}[.)]?\s+"
                r"[A-ZА-ЯЁ][^,.!?]{1,220}\.)\s+(?P<body>.+)$",
                line,
            )
            if (
                inline_heading
                and len(
                    re.findall(
                        r"[0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё'’-]*",
                        inline_heading.group("head"),
                    )
                )
                <= 12
            ):
                expanded_lines.extend(
                    [
                        inline_heading.group("head").rstrip("."),
                        inline_heading.group("body"),
                    ]
                )
            else:
                expanded_lines.append(line)
            line_index += 1
        lines = expanded_lines

        for line in lines:
            normalized = normalize_for_match(line)
            if re.fullmatch(
                r"(?:version|версия).*(?:\d+\s+of\s+\d+|\d+\s+из\s+\d+)",
                normalized,
                re.I,
            ) or DOI_RE.fullmatch(line):
                continue
            is_abstract = bool(
                re.match(r"^(?:abstract|аннотация|резюме)\b", line, re.I)
            )
            is_keywords = bool(
                re.match(r"^(?:keywords?|ключевые слова)\b", line, re.I)
            )
            numbered_heading = re.match(
                r"^\d+(?:\.\d+){0,3}[.)]?\s+[A-ZА-ЯЁ][^.!?]{1,180}$",
                line,
            )
            is_heading = normalized in known_headings or bool(numbered_heading)
            is_udc = normalized.startswith(("удк", "udc"))
            is_author = (
                before_abstract
                and len(line) <= 300
                and bool(author_pattern.search(line))
            )
            is_title = (
                before_abstract
                and len(line) >= 15
                and _uppercase_ratio(line) >= 0.72
                and not is_author
                and not is_udc
            )
            role = (
                "abstract"
                if is_abstract
                else "keywords"
                if is_keywords
                else "heading"
                if is_heading
                else "udc"
                if is_udc
                else "authors"
                if is_author
                else "title"
                if is_title
                else "body"
            )
            if is_abstract:
                before_abstract = False
            if pending and pending["role"] in {"abstract", "keywords"}:
                if role == "body":
                    pending["text"] = append_text(pending["text"], line)
                    pending["last_page"] = page_number
                    if (
                        pending["role"] == "keywords"
                        and pending["text"].rstrip().endswith((".", ";"))
                    ):
                        flush()
                    continue
                flush()
            if pending and role == pending["role"] and role in {"title", "authors"}:
                pending["text"] = append_text(pending["text"], line)
                pending["last_page"] = page_number
                continue
            if role in {"heading", "udc", "abstract", "keywords", "title", "authors"}:
                flush()
                pending = {
                    "text": line,
                    "role": role,
                    "page": page_number,
                    "last_page": page_number,
                }
                if role in {"heading", "udc"} or (
                    role == "keywords"
                    and line.rstrip().endswith((".", ";"))
                ):
                    flush()
                continue
            if pending is None:
                pending = {
                    "text": line,
                    "role": "body",
                    "page": page_number,
                    "last_page": page_number,
                }
            else:
                pending["text"] = append_text(pending["text"], line)
                pending["last_page"] = page_number
            if pending["text"].rstrip().endswith((".", "!", "?", ";")):
                flush()
    flush()

    paragraphs = []
    pages = [
        {
            "number": page_number,
            "width_pt": round(float(page.mediabox.width), 2),
            "height_pt": round(float(page.mediabox.height), 2),
            "block_ids": [],
        }
        for page_number, page in enumerate(reader.pages, start=1)
    ]
    page_counts = Counter()
    for index, block in enumerate(paragraph_values):
        page_number = block["page"]
        block_id = f"page:{page_number}:plain:{page_counts[page_number]}"
        page_counts[page_number] += 1
        role = block["role"]
        paragraphs.append(
            {
                "index": index,
                "block_id": block_id,
                "text": re.sub(
                    r"\b([B-HJ-Z])\s+([a-z]{3,})",
                    r"\1\2",
                    normalize_space(block["text"]),
                ),
                "style": f"pdf:{role}" if role != "body" else "",
                "style_id": "",
                "region": "document",
                "container": "page",
                "page": page_number,
                "alignment": "",
                "outline_level": 0 if role == "heading" else None,
                "font_family": "",
                "font_size_pt": None,
                "bold": True if role in {"title", "heading"} else None,
                "italic": None,
            }
        )
        pages[page_number - 1]["block_ids"].append(block_id)
    return paragraphs, pages


def _abstract_character_count(paragraphs):
    collecting = False
    total = 0
    for paragraph in paragraphs:
        text = normalize_space(paragraph.get("text"))
        if re.match(r"^(?:abstract|аннотация|резюме)\b", text, re.I):
            collecting = True
            total += len(
                re.sub(
                    r"^(?:abstract|аннотация|резюме)\s*[:.–—-]?\s*",
                    "",
                    text,
                    flags=re.I,
                )
            )
            continue
        if collecting and re.match(
            r"^(?:keywords?|ключевые слова)\b",
            text,
            re.I,
        ):
            break
        if collecting:
            keyword_match = re.search(
                r"\b(?:keywords?|ключевые слова)\s*:",
                text,
                re.I,
            )
            if keyword_match:
                total += len(text[: keyword_match.start()])
                break
            total += len(text)
    return total


def _reference_tail(paragraphs):
    aliases = {
        normalize_for_match(alias)
        for alias in SECTION_ALIASES["Список литературы"]
    }
    heading_index = next(
        (
            index
            for index, record in enumerate(paragraphs)
            if normalize_for_match(record.get("text")).strip(" .:") in aliases
        ),
        None,
    )
    if heading_index is None:
        return None, 0
    count = sum(
        bool(
            re.match(
                r"^\s*(?:\[\s*\d+\s*\]|\d{1,3}[.)])\s+\S",
                normalize_space(record.get("text")),
            )
        )
        for record in paragraphs[heading_index + 1 :]
    )
    return heading_index, count


def _pdf_structural_objects(paragraphs):
    figures = []
    tables = []
    formulas = []
    seen_figures = set()
    seen_tables = set()
    seen_equations = set()
    for order, record in enumerate(paragraphs):
        text = normalize_space(record.get("text"))
        block_id = str(record.get("block_id") or f"pdf:p:{order}")
        page = record.get("page")

        figure_match = re.search(
            r"\b(?:figure|рис(?:унок)?\.?)\s*(\d{1,3})\b",
            text,
            re.I,
        )
        if (
            figure_match
            and figure_match.start() <= 24
            and figure_match.group(1) not in seen_figures
        ):
            seen_figures.add(figure_match.group(1))
            figures.append(
                {
                    "index": len(figures),
                    "block_id": block_id,
                    "number": int(figure_match.group(1)),
                    "caption": text,
                    "page": page,
                    "source": "pdf_caption",
                    "confidence": 0.9,
                }
            )

        table_match = re.match(
            r"^\s*(?:table|табл(?:ица)?\.?)\s*(\d{1,3})\b",
            text,
            re.I,
        )
        if table_match and table_match.group(1) not in seen_tables:
            seen_tables.add(table_match.group(1))
            tables.append(
                {
                    "index": len(tables),
                    "block_id": block_id,
                    "number": int(table_match.group(1)),
                    "caption": text,
                    "page": page,
                    "rows": [],
                    "source": "pdf_caption",
                    "confidence": 0.9,
                }
            )

        equation_numbers = re.findall(r"\(\s*(\d{1,3})\s*\)", text)
        math_signal = bool(
            re.search(r"[=≈±∑∫√∆Δφℓ∈{}]", text)
            or re.search(r"\b(?:exp|log|sin|cos)\s*\(", text, re.I)
        )
        if not math_signal:
            continue
        for number in equation_numbers:
            if number in seen_equations:
                continue
            seen_equations.add(number)
            formulas.append(
                {
                    "index": len(formulas),
                    "block_id": block_id,
                    "number": int(number),
                    "text": text,
                    "page": page,
                    "source": "pdf_text_equation",
                    "confidence": 0.82,
                }
            )
    return figures, tables, formulas


def _pdf_paragraphs(data):
    try:
        from pypdf import PdfReader
    except ImportError:
        return [], [], 0, "Для разбора PDF не установлена библиотека pypdf."

    reader = PdfReader(io.BytesIO(data), strict=False)
    paragraphs = []
    pages = []
    image_count = 0
    all_lines = []

    def inline_join(left, right):
        left = normalize_space(left)
        right = normalize_space(right)
        if not left:
            return right
        if not right:
            return left
        if right[:1] in ",.;:!?)]}" or left[-1:] in "([{":
            return left + right
        return f"{left} {right}"

    for page_number, page in enumerate(reader.pages, start=1):
        media_box = page.mediabox
        page_width = float(media_box.width)
        page_height = float(media_box.height)
        fragments = []

        def visitor(text, current_matrix, text_matrix, font, font_size):
            text = normalize_space(text)
            if not text:
                return
            preliminary_text_scale = max(
                0.01,
                (
                    float(text_matrix[0]) ** 2
                    + float(text_matrix[1]) ** 2
                )
                ** 0.5,
            )
            preliminary_matrix_scale = max(
                0.01,
                (
                    float(current_matrix[0]) ** 2
                    + float(current_matrix[1]) ** 2
                )
                ** 0.5,
            )
            preliminary_size = (
                float(font_size)
                * preliminary_text_scale
                * preliminary_matrix_scale
            )
            if preliminary_size <= 6.2 and re.fullmatch(r"\d{1,4}", text):
                return
            invalid_position = (
                abs(float(text_matrix[4])) < 0.01
                and abs(float(text_matrix[5])) < 0.01
                and fragments
            )
            if invalid_position:
                fragments[-1]["text"] = inline_join(fragments[-1]["text"], text)
                return
            x = (
                float(text_matrix[4]) * float(current_matrix[0])
                + float(text_matrix[5]) * float(current_matrix[2])
                + float(current_matrix[4])
            )
            y = (
                float(text_matrix[4]) * float(current_matrix[1])
                + float(text_matrix[5]) * float(current_matrix[3])
                + float(current_matrix[5])
            )
            text_scale = max(
                0.01,
                (
                    float(text_matrix[0]) ** 2
                    + float(text_matrix[1]) ** 2
                )
                ** 0.5,
            )
            matrix_scale = max(
                0.01,
                (
                    float(current_matrix[0]) ** 2
                    + float(current_matrix[1]) ** 2
                )
                ** 0.5,
            )
            effective_size = round(float(font_size) * text_scale * matrix_scale, 2)
            font_name = str((font or {}).get("/BaseFont") or "")
            fragments.append(
                {
                    "text": text,
                    "x": x,
                    "y": y,
                    "font_size_pt": effective_size,
                    "font_family": font_name,
                    "bold": "bold" in font_name.casefold(),
                    "italic": any(
                        marker in font_name.casefold()
                        for marker in ("italic", "oblique")
                    ),
                }
            )

        page.extract_text(visitor_text=visitor)
        try:
            image_count += len(page.images)
        except Exception:
            pass

        y_groups = []
        for fragment in sorted(fragments, key=lambda item: (-item["y"], item["x"])):
            tolerance = max(1.8, fragment["font_size_pt"] * 0.28)
            group = next(
                (
                    candidate
                    for candidate in reversed(y_groups[-5:])
                    if abs(candidate["y"] - fragment["y"]) <= tolerance
                ),
                None,
            )
            if group is None:
                group = {"y": fragment["y"], "fragments": []}
                y_groups.append(group)
            group["fragments"].append(fragment)

        page_lines = []
        for group in sorted(y_groups, key=lambda item: -item["y"]):
            current = None
            for fragment in sorted(group["fragments"], key=lambda item: item["x"]):
                approximate_width = (
                    len(fragment["text"])
                    * max(fragment["font_size_pt"], 6)
                    * 0.43
                )
                if current is not None:
                    gap = fragment["x"] - current["estimated_end"]
                    split_threshold = max(
                        45,
                        max(
                            current["font_size_pt"],
                            fragment["font_size_pt"],
                        )
                        * 5,
                    )
                    if gap <= split_threshold:
                        current["text"] = inline_join(
                            current["text"],
                            fragment["text"],
                        )
                        current["estimated_end"] = max(
                            current["estimated_end"],
                            fragment["x"] + approximate_width,
                        )
                        current["bold"] = current["bold"] or fragment["bold"]
                        current["italic"] = current["italic"] or fragment["italic"]
                        current["font_size_pt"] = max(
                            current["font_size_pt"],
                            fragment["font_size_pt"],
                        )
                        continue
                if current is not None:
                    page_lines.append(current)
                current = {
                    **fragment,
                    "estimated_end": fragment["x"] + approximate_width,
                    "page": page_number,
                    "page_width": page_width,
                    "page_height": page_height,
                }
            if current is not None:
                page_lines.append(current)

        for line in page_lines:
            normalized = normalize_for_match(line["text"])
            line["region"] = "document"
            if (
                line["font_size_pt"] <= 6.2
                and re.fullmatch(r"\d{1,4}", normalized)
            ):
                line["region"] = "line_number"
            elif (
                page_number == 1
                and line["font_size_pt"] <= 8.2
                and line["x"] < page_width * 0.14
                and line["y"] < page_height - 90
            ):
                line["region"] = "sidebar"
        all_lines.extend(page_lines)
        pages.append(
            {
                "number": page_number,
                "width_pt": round(page_width, 2),
                "height_pt": round(page_height, 2),
                "block_ids": [],
            }
        )

    repeated_edge_text = Counter(
        normalize_for_match(line["text"])
        for line in all_lines
        if line["text"]
        and (
            line["y"] >= line["page_height"] - 55
            or line["y"] <= 55
        )
    )
    for line in all_lines:
        normalized = normalize_for_match(line["text"])
        if repeated_edge_text.get(normalized, 0) >= 2:
            line["region"] = "header_footer"

    document_lines = [
        line
        for line in all_lines
        if line["region"] == "document"
    ]
    body_sizes = [
        line["font_size_pt"]
        for line in document_lines
        if len(line["text"]) >= 60 and line["font_size_pt"] >= 7
    ]
    body_size = statistics.median(body_sizes) if body_sizes else 10.0
    known_headings = {
        normalize_for_match(alias)
        for aliases in SECTION_ALIASES.values()
        for alias in aliases
    }

    logical_blocks = []
    pending = None

    def append_line(block, line):
        text = line["text"]
        if block["text"].endswith(("-", "‑")) and text[:1].islower():
            block["text"] = block["text"][:-1] + text
        else:
            block["text"] = inline_join(block["text"], text)
        block["font_size_pt"] = max(block["font_size_pt"], line["font_size_pt"])
        block["bold"] = block["bold"] or line["bold"]
        block["italic"] = block["italic"] or line["italic"]
        block["last_y"] = line["y"]
        block["last_page"] = line["page"]

    def flush():
        nonlocal pending
        if pending and normalize_space(pending["text"]):
            logical_blocks.append(pending)
        pending = None

    for line in sorted(document_lines, key=lambda item: (item["page"], -item["y"], item["x"])):
        text = line["text"]
        normalized = normalize_for_match(text)
        numbered = re.match(
            r"^\d+(?:\.\d+){0,3}[.)]?\s+([A-ZА-ЯЁ][^.!?]{1,160})$",
            text,
        )
        heading_has_math = bool(
            re.search(r"[=±∑∫√\\_]", text)
            or re.search(r"\(\s*\d{1,3}\s*\)\s*$", text)
        )
        plausible_numbered_heading = bool(
            numbered
            and not heading_has_math
            and len(re.findall(r"[0-9A-Za-zА-Яа-яЁё][\w'’-]*", text)) <= 18
            and text.count(",") < 2
        )
        is_label = bool(
            re.match(
                r"^(?:abstract|аннотация|keywords?|ключевые слова)\s*:?\s*$",
                text,
                re.I,
            )
        )
        is_heading = (
            normalized in known_headings
            or plausible_numbered_heading
            or (
                line["bold"]
                and line["font_size_pt"] >= body_size + 1
                and len(text) <= 220
                and not heading_has_math
                and len(re.findall(r"[0-9A-Za-zА-Яа-яЁё][\w'’-]*", text)) <= 18
            )
        )
        is_large_title_line = (
            line["bold"]
            and line["font_size_pt"] >= body_size + 4
            and len(text) <= 240
        )
        if is_heading or is_label:
            if (
                pending
                and pending.get("large_title")
                and is_large_title_line
                and line["page"] == pending["last_page"]
            ):
                append_line(pending, line)
                continue
            flush()
            pending = {
                **line,
                "text": text,
                "last_y": line["y"],
                "last_page": line["page"],
                "is_heading": is_heading,
                "large_title": is_large_title_line,
            }
            continue

        if pending is None:
            pending = {
                **line,
                "text": text,
                "last_y": line["y"],
                "last_page": line["page"],
                "is_heading": False,
                "large_title": False,
            }
            continue
        font_changed = abs(pending["font_size_pt"] - line["font_size_pt"]) > 1.4
        emphasis_changed = pending["bold"] != line["bold"]
        new_page = line["page"] != pending["last_page"]
        previous_complete = pending["text"].rstrip().endswith((".", "!", "?", ";"))
        if (
            font_changed
            or emphasis_changed
            or (new_page and previous_complete)
            or (
                previous_complete
                and line["x"] > pending["x"] + max(8, body_size * 0.8)
            )
        ):
            flush()
            pending = {
                **line,
                "text": text,
                "last_y": line["y"],
                "last_page": line["page"],
                "is_heading": False,
                "large_title": False,
            }
        else:
            append_line(pending, line)
    flush()

    for next_index, block in enumerate(logical_blocks):
        page_number = block["page"]
        block_id = f"page:{page_number}:p:{sum(1 for item in paragraphs if item['page'] == page_number)}"
        style = "PDF Heading" if block.get("is_heading") else ""
        record = {
            "index": next_index,
            "block_id": block_id,
            "text": re.sub(
                r"\b([B-HJ-Z])\s+([a-z]{3,})",
                r"\1\2",
                normalize_space(block["text"]),
            ),
            "style": style,
            "style_id": "",
            "region": "document",
            "container": "page",
            "page": page_number,
            "alignment": "",
            "outline_level": 0 if block.get("is_heading") else None,
            "font_family": block.get("font_family") or "",
            "font_size_pt": round(block["font_size_pt"], 1),
            "bold": bool(block["bold"]),
            "italic": bool(block["italic"]),
        }
        paragraphs.append(record)
        pages[page_number - 1]["block_ids"].append(block_id)
    plain_paragraphs, plain_pages = _pdf_plain_paragraphs(reader)
    coordinate_reference_index, coordinate_reference_count = _reference_tail(
        paragraphs
    )
    plain_reference_index, plain_reference_count = _reference_tail(
        plain_paragraphs
    )
    if (
        coordinate_reference_index is not None
        and plain_reference_index is not None
        and plain_reference_count >= coordinate_reference_count + 3
        and plain_reference_count > coordinate_reference_count * 1.35
    ):
        paragraphs = (
            paragraphs[: coordinate_reference_index + 1]
            + plain_paragraphs[plain_reference_index + 1 :]
        )
        for index, record in enumerate(paragraphs):
            record["index"] = index
    coordinate_abstract_length = _abstract_character_count(paragraphs)
    plain_abstract_length = _abstract_character_count(plain_paragraphs)
    coordinate_mixed_metadata = any(
        re.match(r"^(?:abstract|аннотация|резюме)\b", record["text"], re.I)
        and re.search(
            r"\b(?:keywords?|ключевые слова)\s*:",
            record["text"],
            re.I,
        )
        for record in paragraphs
    )
    if (
        plain_abstract_length >= 250
        and (
            coordinate_mixed_metadata
            or plain_abstract_length
            > max(200, coordinate_abstract_length * 1.6)
        )
    ):
        return plain_paragraphs, plain_pages, image_count, ""
    return paragraphs, pages, image_count, ""


def analyze_document_bytes(data, file_name, *, semantic_complete=None):
    suffix = Path(file_name or "").suffix.casefold()
    base = {
        "file_name": Path(file_name or "document").name,
        "suffix": suffix,
        "size": len(data),
        "magic_hex": data[:8].hex(),
        "paragraphs": [],
        "auxiliary_blocks": [],
        "tables": [],
        "figures": [],
        "formulas": [],
        "pages": [],
        "text": "",
        "image_count": 0,
        "embedded_image_count": 0,
        "requires_ocr": False,
        "external_relationships": [],
        "dangerous_relationships": [],
        "archive_members": [],
        "dangerous_members": [],
        "embedded_objects": [],
        "equation_embeddings": [],
        "compression_ratio": 1.0,
        "unpacked_size": len(data),
        "parse_error": "",
    }
    if suffix in {".docx", ".dotx"}:
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
            {
                "index": index,
                "block_id": f"document:p:{index}",
                "text": value,
                "style": "",
                "style_id": "",
                "region": "document",
                "container": "body",
                "alignment": "",
                "outline_level": None,
                "font_family": "",
                "font_size_pt": None,
                "bold": None,
                "italic": None,
            }
            for index, value in enumerate(paragraphs)
            if value
        ]
        base["text"] = "\n".join(item["text"] for item in base["paragraphs"])
        if suffix == ".tex":
            decoded = _decode_text(data)
            commands = _latex_command_values(decoded)
            title_values = commands.get("title") or []
            author_values = (
                commands.get("authornames")
                or commands.get("authors")
                or commands.get("author")
                or []
            )
            institution_values = (
                commands.get("address")
                or commands.get("affiliation")
                or commands.get("institution")
                or []
            )
            abstract_values = commands.get("abstract") or []
            keyword_values = (
                commands.get("keyword")
                or commands.get("keywords")
                or []
            )
            heading_values = {
                normalize_for_match(_clean_latex_metadata(value)): level
                for command_name, level in (
                    ("section", 1),
                    ("subsection", 2),
                    ("subsubsection", 3),
                    ("paragraph", 4),
                )
                for value in commands.get(command_name) or []
            }
            ignored_values = {
                normalize_for_match(_clean_latex_metadata(value))
                for command_name in ("label", "ref", "pageref", "eqref", "cite")
                for value in commands.get(command_name) or []
            }
            body_records = base["paragraphs"]
            first_heading_index = next(
                (
                    index
                    for index, record in enumerate(body_records)
                    if normalize_for_match(record["text"]) in heading_values
                ),
                len(body_records),
            )
            body_records = [
                record
                for record in body_records[first_heading_index:]
                if normalize_for_match(record["text"]) not in ignored_values
            ]
            bibliography_entries = _latex_bibliography_entries(decoded)
            if bibliography_entries:
                reference_aliases = {
                    normalize_for_match(alias)
                    for alias in SECTION_ALIASES["Список литературы"]
                }
                reference_heading_index = next(
                    (
                        index
                        for index, record in enumerate(body_records)
                        if normalize_for_match(record["text"]).strip(" .:")
                        in reference_aliases
                    ),
                    None,
                )
                if reference_heading_index is not None:
                    body_records = body_records[: reference_heading_index + 1]
            front_values = []
            if title_values:
                front_values.append(
                    (_clean_latex_metadata(title_values[0]), "latex:title")
                )
            if author_values:
                front_values.append(
                    (_clean_latex_metadata(author_values[0]), "latex:authors")
                )
            if institution_values:
                front_values.append(
                    (
                        _clean_latex_metadata(institution_values[0]),
                        "latex:institution",
                    )
                )
            if abstract_values:
                front_values.append(
                    (
                        f"Abstract: {_clean_latex_metadata(abstract_values[0])}",
                        "latex:abstract",
                    )
                )
            if keyword_values:
                front_values.append(
                    (
                        f"Keywords: {_clean_latex_metadata(keyword_values[0])}",
                        "latex:keywords",
                    )
                )
            rebuilt_records = []
            for text_value, style in front_values:
                if not text_value:
                    continue
                rebuilt_records.append(
                    {
                        "index": len(rebuilt_records),
                        "block_id": f"document:p:{len(rebuilt_records)}",
                        "text": text_value,
                        "style": style,
                        "style_id": style,
                        "region": "document",
                        "container": "body",
                        "alignment": "",
                        "outline_level": None,
                        "font_family": "",
                        "font_size_pt": None,
                        "bold": None,
                        "italic": None,
                    }
                )
            for record in body_records:
                normalized_text = normalize_for_match(record["text"])
                record = dict(record)
                record["index"] = len(rebuilt_records)
                record["block_id"] = f"document:p:{len(rebuilt_records)}"
                if normalized_text in heading_values:
                    level = heading_values[normalized_text]
                    record["style"] = f"latex:heading:{level}"
                    record["style_id"] = record["style"]
                    record["outline_level"] = level - 1
                rebuilt_records.append(record)
            for reference in bibliography_entries:
                index = len(rebuilt_records)
                rebuilt_records.append(
                    {
                        "index": index,
                        "block_id": f"document:p:{index}",
                        "text": reference,
                        "style": "latex:reference",
                        "style_id": "latex:reference",
                        "region": "document",
                        "container": "body",
                        "alignment": "",
                        "outline_level": None,
                        "font_family": "",
                        "font_size_pt": None,
                        "bold": None,
                        "italic": None,
                    }
                )
            base["paragraphs"] = rebuilt_records
            base["text"] = "\n".join(
                record["text"] for record in rebuilt_records
            )
            base["formulas"] = [
                {
                    "index": index,
                    "block_id": f"document:formula:{index}",
                    "text": normalize_space(match.group(1) or match.group(2) or ""),
                    "source": "latex",
                    "confidence": 0.95,
                }
                for index, match in enumerate(
                    re.finditer(
                        r"\\begin\s*\{(?:equation|align|gather|multline)\*?\}(.*?)"
                        r"\\end\s*\{(?:equation|align|gather|multline)\*?\}"
                        r"|\$\$(.*?)\$\$",
                        decoded,
                        re.I | re.S,
                    )
                )
            ]
            base["figures"] = [
                {
                    "index": index,
                    "block_id": f"document:figure:{index}",
                    "source": "latex",
                    "confidence": 0.95,
                }
                for index, _match in enumerate(
                    re.finditer(r"\\begin\s*\{figure\*?\}", decoded, re.I)
                )
            ]
            base["image_count"] = len(base["figures"])
            base["tables"] = [
                {
                    "index": index,
                    "block_id": f"document:table:{index}",
                    "rows": [],
                    "source": "latex",
                }
                for index, _match in enumerate(
                    re.finditer(r"\\begin\s*\{table\*?\}", decoded, re.I)
                )
            ]
    elif suffix == ".pdf":
        try:
            paragraphs, pages, image_count, parse_error = _pdf_paragraphs(data)
            figures, tables, formulas = _pdf_structural_objects(paragraphs)
            base["paragraphs"] = paragraphs
            base["pages"] = pages
            base["figures"] = figures
            base["tables"] = tables
            base["formulas"] = formulas
            base["image_count"] = max(image_count, len(figures))
            base["text"] = "\n".join(record["text"] for record in paragraphs)
            base["parse_error"] = parse_error
            base["requires_ocr"] = bool(pages and not base["text"].strip())
            if base["requires_ocr"]:
                base["parse_error"] = (
                    "В PDF не найден текстовый слой. Нужен OCR или исходный DOCX/LaTeX."
                )
        except Exception as exc:
            base["parse_error"] = f"Не удалось разобрать PDF: {type(exc).__name__}."
    elif suffix == ".doc":
        from apps.submissions.document_conversion import (
            LegacyDocConversionError,
            convert_legacy_doc_to_docx,
        )

        try:
            converted_data = convert_legacy_doc_to_docx(data)
        except LegacyDocConversionError as exc:
            base["parse_error"] = (
                f"{exc} Загрузите DOCX или настройте серверный "
                "LibreOffice-конвертер."
            )
        else:
            converted_name = f"{Path(file_name or 'document').stem}.docx"
            converted = analyze_document_bytes(
                converted_data,
                converted_name,
                semantic_complete=semantic_complete,
            )
            converted.update(
                {
                    "file_name": Path(file_name or "document.doc").name,
                    "suffix": ".doc",
                    "size": len(data),
                    "magic_hex": data[:8].hex(),
                    "source_suffix": ".doc",
                    "converted_suffix": ".docx",
                    "conversion": {
                        "status": "completed",
                        "engine": "server_document_converter",
                        "target_format": "docx",
                    },
                }
            )
            converted["article"]["source_format"] = "doc"
            return converted
    else:
        base["parse_error"] = "Формат файла не поддерживается."

    from apps.submissions.article_extraction import (
        article_to_legacy_metadata,
        extract_article_structure,
        refine_article_with_model,
    )

    article = extract_article_structure(base)
    if semantic_complete is not None and article.get("needs_review"):
        article = refine_article_with_model(
            base,
            article,
            complete_json=semantic_complete,
        )
    base["article"] = article
    base["metadata"] = article_to_legacy_metadata(article)
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
    if not non_initials:
        return normalize_for_match(tokens[-1])

    uppercase = [
        token
        for token in non_initials
        if len(token) > 1 and _uppercase_ratio(token) >= 0.9
    ]
    if len(uppercase) == 1:
        return normalize_for_match(uppercase[0])

    cyrillic = [
        token
        for token in non_initials
        if re.search(r"[А-ЯЁа-яё]", token)
    ]
    if len(cyrillic) >= 3:
        normalized = [normalize_for_match(token) for token in cyrillic]
        patronymic_suffixes = (
            "вич",
            "вна",
            "ична",
            "инична",
            "овна",
            "евна",
            "оглы",
            "кызы",
        )
        if normalized[-1].endswith(patronymic_suffixes):
            return normalized[0]
        if normalized[-2].endswith(patronymic_suffixes):
            return normalized[-1]

    return normalize_for_match(non_initials[-1])


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
    from apps.submissions.article_extraction import (
        article_to_legacy_metadata,
        extract_article_structure,
    )

    article = snapshot.get("article") or extract_article_structure(snapshot)
    return article_to_legacy_metadata(article)


def _parse_author_identity(author):
    normalized = normalize_for_match(author)
    surname = _author_surname(author)
    initials = []
    for letter in re.findall(r"([а-яёa-z])\s*\.", normalized, re.I):
        initials.append(letter.casefold().replace("ё", "е"))
    if not initials:
        tokens = re.findall(r"[а-яёa-z'’-]+", normalized, re.I)
        name_tokens = [
            token
            for token in tokens
            if normalize_for_match(token) != surname
        ]
        initials = [normalize_for_match(token[0]) for token in name_tokens[:2] if token]
    return surname, initials


def _user_author_identity(user):
    first_name = normalize_space(getattr(user, "first_name", ""))
    last_name = normalize_space(getattr(user, "last_name", ""))
    if last_name:
        surname = normalize_for_match(last_name)
        name_tokens = re.findall(r"[А-ЯЁа-яёA-Za-z'’-]+", first_name)
    else:
        display = normalize_space(user.get_full_name() or user.username)
        tokens = re.findall(r"[А-ЯЁа-яёA-Za-z'’-]+", display)
        surname = normalize_for_match(tokens[0]) if tokens else ""
        name_tokens = tokens[1:]
    initials = [
        normalize_for_match(token[0])
        for token in name_tokens[:2]
        if token
    ]
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
            user_surname, user_initials = _user_author_identity(user)
            if user_surname != surname:
                continue
            score = 5
            compared = min(len(initials), len(user_initials), 2)
            if compared and any(
                initials[index] != user_initials[index]
                for index in range(compared)
            ):
                continue
            score += compared * 2
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
                    "full_name": normalize_space(best.get_full_name()),
                    "username": best.username,
                }
            )
    return matches
