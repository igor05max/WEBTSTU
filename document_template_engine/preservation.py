from __future__ import annotations

import hashlib
import io
import posixpath
import re
from collections import Counter
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile


_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

_PROTECTED_PREFIXES = (
    "customxml/",
    "word/activex/",
    "word/charts/",
    "word/diagrams/",
    "word/embeddings/",
    "word/media/",
)
_PROTECTED_PART_RE = re.compile(
    r"^word/(?:header\d+|footer\d+|footnotes|endnotes|comments(?:extended)?|"
    r"glossary/document)\.xml$",
    re.I,
)
_PROTECTED_RELS_RE = re.compile(
    r"^word/_rels/(?:header\d+|footer\d+|footnotes|endnotes|comments(?:extended)?|"
    r"glossary/document)\.xml\.rels$",
    re.I,
)
_STRUCTURAL_TAGS = (
    "tbl",
    "drawing",
    "pict",
    "object",
    "oMath",
    "hyperlink",
    "fldChar",
    "instrText",
    "bookmarkStart",
    "bookmarkEnd",
    "commentRangeStart",
    "commentRangeEnd",
    "altChunk",
    "sdt",
    "sectPr",
)


class DocxPreservationError(ValueError):
    """Raised when a supposedly non-destructive DOCX edit loses document payload."""


def _normalized_name(value: str) -> str:
    return posixpath.normpath(str(value or "").replace("\\", "/")).lstrip("./").casefold()


def _is_protected_part(name: str) -> bool:
    normalized = _normalized_name(name)
    return (
        normalized.startswith(_PROTECTED_PREFIXES)
        or bool(_PROTECTED_PART_RE.fullmatch(normalized))
        or bool(_PROTECTED_RELS_RE.fullmatch(normalized))
        or normalized in {"word/vbaproject.bin", "word/_rels/vbaproject.bin.rels"}
    )


def _xml_fingerprint(data: bytes):
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return ("binary", hashlib.sha256(data).hexdigest())

    def element_fingerprint(element):
        return (
            element.tag,
            tuple(sorted(element.attrib.items())),
            element.text or "",
            element.tail or "",
            tuple(element_fingerprint(child) for child in element),
        )

    return ("xml", element_fingerprint(root))


def _protected_parts(archive: ZipFile) -> dict[str, object]:
    return {
        _normalized_name(info.filename): (
            _xml_fingerprint(archive.read(info.filename))
            if info.filename.casefold().endswith((".xml", ".rels"))
            else ("binary", hashlib.sha256(archive.read(info.filename)).hexdigest())
        )
        for info in archive.infolist()
        if not info.is_dir() and _is_protected_part(info.filename)
    }


def _main_relationships(archive: ZipFile) -> set[tuple[str, str, str, str]]:
    try:
        root = ElementTree.fromstring(archive.read("word/_rels/document.xml.rels"))
    except (KeyError, ElementTree.ParseError):
        return set()
    return {
        (
            str(item.attrib.get("Id") or ""),
            str(item.attrib.get("Type") or ""),
            str(item.attrib.get("Target") or ""),
            str(item.attrib.get("TargetMode") or ""),
        )
        for item in root.findall(f"{{{_PACKAGE_REL_NS}}}Relationship")
    }


def _document_structure(archive: ZipFile) -> Counter:
    try:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    except (KeyError, ElementTree.ParseError) as exc:
        raise DocxPreservationError(
            "После редактирования DOCX не содержит читаемого основного документа."
        ) from exc
    counts = Counter(
        item.tag.rsplit("}", 1)[-1]
        for item in root.iter()
        if item.tag.rsplit("}", 1)[-1] in _STRUCTURAL_TAGS
    )
    return Counter({tag: counts[tag] for tag in _STRUCTURAL_TAGS})


def assert_docx_payload_preserved(
    source: bytes,
    result: bytes,
) -> None:
    """
    Verify that a content-preserving edit did not lose opaque Word payload.

    The main text and paragraph formatting may change. Embedded media, equations,
    charts, headers, footers, notes, relationships and structural objects may not.
    """

    try:
        with ZipFile(io.BytesIO(source)) as source_archive, ZipFile(
            io.BytesIO(result)
        ) as result_archive:
            source_parts = _protected_parts(source_archive)
            result_parts = _protected_parts(result_archive)
            if source_parts != result_parts:
                lost = sorted(set(source_parts) - set(result_parts))
                changed = sorted(
                    name
                    for name in set(source_parts) & set(result_parts)
                    if source_parts[name] != result_parts[name]
                )
                details = [*lost[:3], *changed[:3]]
                suffix = f": {', '.join(details)}" if details else ""
                raise DocxPreservationError(
                    "Защитная проверка остановила сохранение: изменены или потеряны "
                    f"вложения, колонтитулы либо служебные части документа{suffix}."
                )

            if _main_relationships(source_archive) != _main_relationships(result_archive):
                raise DocxPreservationError(
                    "Защитная проверка остановила сохранение: изменились связи "
                    "рисунков, формул или других объектов DOCX."
                )

            source_structure = _document_structure(source_archive)
            result_structure = _document_structure(result_archive)
            if source_structure != result_structure:
                changed_tags = [
                    tag
                    for tag in _STRUCTURAL_TAGS
                    if source_structure[tag] != result_structure[tag]
                ]
                raise DocxPreservationError(
                    "Защитная проверка остановила сохранение: изменилась структура "
                    f"таблиц, формул, ссылок или разделов ({', '.join(changed_tags)})."
                )
    except BadZipFile as exc:
        raise DocxPreservationError(
            "Защитная проверка не смогла открыть исходный или готовый DOCX."
        ) from exc
