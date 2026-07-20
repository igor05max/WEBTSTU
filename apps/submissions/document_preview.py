import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from xml.etree import ElementTree
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from django.conf import settings


DOCX_MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
DOCX_MAX_MEMBERS = 5000
DOCX_MAX_BLOCKS = 2500
DOCX_MAX_CHARACTERS = 500_000
TEXT_PREVIEW_MAX_BYTES = 2 * 1024 * 1024

PREVIEW_KINDS = {
    ".pdf": "pdf",
    ".txt": "text",
    ".doc": "legacy_doc",
    ".docx": "docx",
}

WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WORD = f"{{{WORD_NAMESPACE}}}"


class DocumentPreviewError(ValueError):
    pass


def get_preview_kind(file_name):
    return PREVIEW_KINDS.get(Path(file_name or "").suffix.lower())


def get_display_filename(file_name):
    return Path(file_name or "").name


def read_text_preview(field_file):
    with field_file.open("rb") as source:
        raw = source.read(TEXT_PREVIEW_MAX_BYTES + 1)

    is_truncated = len(raw) > TEXT_PREVIEW_MAX_BYTES
    raw = raw[:TEXT_PREVIEW_MAX_BYTES]
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings = ("utf-16", "utf-8-sig", "cp1251")
    else:
        encodings = ("utf-8-sig", "cp1251", "cp866")

    for encoding in encodings:
        try:
            return raw.decode(encoding), is_truncated
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), is_truncated


def read_docx_preview(field_file):
    try:
        with field_file.open("rb") as source, ZipFile(source) as archive:
            members = archive.infolist()
            if len(members) > DOCX_MAX_MEMBERS:
                raise DocumentPreviewError("В документе слишком много внутренних файлов.")
            if sum(member.file_size for member in members) > DOCX_MAX_UNCOMPRESSED_BYTES:
                raise DocumentPreviewError("Документ слишком большой для безопасного просмотра.")

            try:
                document_xml = archive.read("word/document.xml")
            except KeyError as exc:
                raise DocumentPreviewError("Не удалось найти содержимое документа DOCX.") from exc
            style_names = _read_style_names(archive)
    except (BadZipFile, OSError) as exc:
        raise DocumentPreviewError("Файл DOCX повреждён или имеет неверный формат.") from exc

    try:
        root = ElementTree.fromstring(document_xml)
    except ElementTree.ParseError as exc:
        raise DocumentPreviewError("Не удалось прочитать структуру документа DOCX.") from exc

    body = root.find(f"{WORD}body")
    if body is None:
        return [], False

    blocks = []
    character_count = 0
    is_truncated = False
    for child in body:
        if len(blocks) >= DOCX_MAX_BLOCKS or character_count >= DOCX_MAX_CHARACTERS:
            is_truncated = True
            break

        if child.tag == f"{WORD}p":
            block = _paragraph_block(child, style_names)
        elif child.tag == f"{WORD}tbl":
            block = _table_block(child)
        else:
            continue

        if block is None:
            continue
        blocks.append(block)
        character_count += _block_character_count(block)

    return blocks, is_truncated


def build_word_document_pdf(version):
    suffix = Path(version.file.name).suffix.lower()
    format_name = "DOCX" if suffix == ".docx" else "DOC"
    try:
        source_path = Path(version.file.path).resolve(strict=True)
        source_stat = source_path.stat()
    except (NotImplementedError, OSError) as exc:
        raise DocumentPreviewError(f"Не удалось открыть исходный файл {format_name}.") from exc

    cache_directory = Path(settings.MEDIA_ROOT) / "document_previews" / "submission_versions"
    cache_directory.mkdir(parents=True, exist_ok=True)
    cache_name = f"{version.pk}-{source_stat.st_size}-{source_stat.st_mtime_ns}.pdf"
    output_path = cache_directory / cache_name
    if _is_valid_pdf(output_path):
        return output_path

    if os.name != "nt":
        return _build_word_document_pdf_with_libreoffice(
            source_path=source_path,
            output_path=output_path,
            format_name=format_name,
        )

    conversion_script = Path(__file__).with_name("convert_word_to_pdf.ps1")
    if not conversion_script.exists():
        raise DocumentPreviewError("На сервере не настроен конвертер документов Word.")

    temporary_output = cache_directory / f".{version.pk}-{uuid4().hex}.pdf"
    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(conversion_script),
        "-SourcePath",
        str(source_path),
        "-OutputPath",
        str(temporary_output),
    ]
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            creationflags=creation_flags,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        temporary_output.unlink(missing_ok=True)
        raise DocumentPreviewError(
            f"Не удалось преобразовать {format_name}. Скачайте оригинал или сохраните его как PDF."
        ) from exc

    if result.returncode != 0 or not _is_valid_pdf(temporary_output):
        temporary_output.unlink(missing_ok=True)
        raise DocumentPreviewError(
            f"Microsoft Word не смог подготовить просмотр этого {format_name}. Скачайте оригинал или сохраните его как PDF."
        )

    os.replace(temporary_output, output_path)
    return output_path


def _build_word_document_pdf_with_libreoffice(*, source_path, output_path, format_name):
    configured_binary = getattr(settings, "LIBREOFFICE_BINARY", "")
    executable = configured_binary or shutil.which("libreoffice") or shutil.which("soffice")
    if not executable:
        raise DocumentPreviewError(
            f"На сервере не установлен LibreOffice для просмотра {format_name}. "
            "Скачайте оригинал или сохраните его как PDF."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="word-preview-",
        dir=output_path.parent,
    ) as temporary_directory:
        temporary_directory = Path(temporary_directory)
        profile_directory = temporary_directory / "libreoffice-profile"
        command = [
            executable,
            f"-env:UserInstallation={profile_directory.as_uri()}",
            "--headless",
            "--convert-to",
            "pdf:writer_pdf_Export",
            "--outdir",
            str(temporary_directory),
            str(source_path),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                check=False,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DocumentPreviewError(
                f"Не удалось преобразовать {format_name} для просмотра. "
                "Скачайте оригинал или сохраните его как PDF."
            ) from exc

        converted_path = temporary_directory / f"{source_path.stem}.pdf"
        if result.returncode != 0 or not _is_valid_pdf(converted_path):
            raise DocumentPreviewError(
                f"LibreOffice не смог подготовить просмотр этого {format_name}. "
                "Скачайте оригинал или сохраните его как PDF."
            )

        os.replace(converted_path, output_path)
    return output_path


def build_legacy_doc_pdf(version):
    return build_word_document_pdf(version)


def _read_style_names(archive):
    try:
        styles_xml = archive.read("word/styles.xml")
    except KeyError:
        return {}
    try:
        root = ElementTree.fromstring(styles_xml)
    except ElementTree.ParseError:
        return {}

    names = {}
    for style in root.findall(f"{WORD}style"):
        style_id = style.get(f"{WORD}styleId")
        name_node = style.find(f"{WORD}name")
        if style_id and name_node is not None:
            names[style_id] = name_node.get(f"{WORD}val", style_id)
    return names


def _paragraph_block(paragraph, style_names):
    text = _node_text(paragraph).strip()
    if not text:
        return None

    properties = paragraph.find(f"{WORD}pPr")
    style_id = ""
    is_list_item = False
    if properties is not None:
        style_node = properties.find(f"{WORD}pStyle")
        if style_node is not None:
            style_id = style_node.get(f"{WORD}val", "")
        is_list_item = properties.find(f"{WORD}numPr") is not None

    style_name = style_names.get(style_id, style_id)
    normalized_style = f"{style_id} {style_name}".lower()
    heading_level = None
    for level in range(1, 7):
        if f"heading {level}" in normalized_style or f"heading{level}" in normalized_style:
            heading_level = level
            break
        if f"заголовок {level}" in normalized_style or f"заголовок{level}" in normalized_style:
            heading_level = level
            break

    if heading_level is not None:
        return {"kind": "heading", "level": heading_level, "text": text}
    if is_list_item:
        return {"kind": "list_item", "text": text}
    if "quote" in normalized_style or "цитат" in normalized_style:
        return {"kind": "quote", "text": text}
    return {"kind": "paragraph", "text": text}


def _table_block(table):
    rows = []
    for row in table.findall(f"{WORD}tr"):
        cells = []
        for cell in row.findall(f"{WORD}tc"):
            cells.append(_node_text(cell).strip())
        if cells:
            rows.append(cells)
    if not rows:
        return None
    return {"kind": "table", "rows": rows}


def _node_text(node):
    parts = []
    for descendant in node.iter():
        if descendant.tag == f"{WORD}t" and descendant.text:
            parts.append(descendant.text)
        elif descendant.tag == f"{WORD}tab":
            parts.append("\t")
        elif descendant.tag in {f"{WORD}br", f"{WORD}cr"}:
            parts.append("\n")
        elif descendant.tag == f"{WORD}p" and parts and parts[-1] != "\n":
            parts.append("\n")
    return "".join(parts).strip()


def _block_character_count(block):
    if block["kind"] == "table":
        return sum(len(cell) for row in block["rows"] for cell in row)
    return len(block["text"])


def _is_valid_pdf(path):
    try:
        if path.stat().st_size < 100:
            return False
        with path.open("rb") as source:
            return source.read(5) == b"%PDF-"
    except OSError:
        return False
