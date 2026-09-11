from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree

from apps.template_workspace.v2.classification.roles import RoleClassifierV2
from apps.template_workspace.v2.inspector.document import DocumentInspector, element_text, normalize_text
from apps.template_workspace.v2.models.document_info import DocumentReport
from apps.template_workspace.v2.models.template_profile import ArticleStructure, MappingPreview, TemplateProfile
from apps.template_workspace.v2.ooxml.namespaces import NS, local_name, qn


PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
HEADER_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/header"
FOOTER_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer"

STYLE_PARTS = {
    "word/styles.xml",
    "word/stylesWithEffects.xml",
    "word/numbering.xml",
    "word/fontTable.xml",
    "word/theme/theme1.xml",
}

PARAGRAPH_PROPERTY_ORDER = {
    "pStyle": 1,
    "keepNext": 2,
    "keepLines": 3,
    "pageBreakBefore": 4,
    "widowControl": 5,
    "numPr": 6,
    "tabs": 7,
    "spacing": 8,
    "ind": 9,
    "jc": 10,
    "textDirection": 11,
    "textAlignment": 12,
    "textboxTightWrap": 13,
    "outlineLvl": 14,
}


@dataclass
class RoleOoxmlRule:
    role: str
    block_id: str
    paragraph_properties: etree._Element | None
    run_properties: etree._Element | None


@dataclass
class SafeWordEditorResult:
    output_path: str
    changes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_path": self.output_path,
            "changes": self.changes,
            "warnings": self.warnings,
        }


class SafeWordEditor:
    """Applies TEMPLATE formatting to a copy of ARTICLE without rewriting content."""

    def __init__(self, classifier: RoleClassifierV2 | None = None):
        self.classifier = classifier or RoleClassifierV2()

    def render(
        self,
        *,
        article_path: Path | str,
        template_path: Path | str,
        output_path: Path | str,
        article_report: DocumentReport | None = None,
        template_report: DocumentReport | None = None,
        article_structure: ArticleStructure | None = None,
        template_profile: TemplateProfile | None = None,
        mapping_preview: MappingPreview | None = None,
        copy_template_headers: bool = False,
    ) -> SafeWordEditorResult:
        article_path = Path(article_path)
        template_path = Path(template_path)
        output_path = Path(output_path)
        article_report = article_report or DocumentInspector(article_path).inspect()
        template_report = template_report or DocumentInspector(template_path).inspect()
        article_structure = article_structure or self.classifier.article_structure(article_report)
        rules = _TemplateRuleExtractor(self.classifier).extract(template_path, template_report)
        body_section = _body_section_properties(template_path, template_profile)
        front_section = _front_section_properties(template_path)
        role_by_block = {item["id"]: item for item in article_structure.blocks}
        first_body_index = _first_body_content_index(article_structure)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with ZipFile(article_path) as article_zip, ZipFile(template_path) as template_zip, ZipFile(output_path, "w", ZIP_DEFLATED) as output_zip:
            document_root = etree.fromstring(article_zip.read("word/document.xml"))
            body = document_root.find("w:body", namespaces=NS)
            if body is None:
                raise ValueError("ARTICLE.docx has no document body")
            paragraph_changes = _apply_body_formatting(
                body=body,
                role_by_block=role_by_block,
                rules=rules,
                front_section=front_section,
                body_section=body_section,
                first_body_index=first_body_index,
                copy_template_headers=copy_template_headers,
            )
            if copy_template_headers:
                header_footer_replacements = _merge_header_footer(article_zip, template_zip, document_root)
            else:
                header_footer_replacements = {}
            replacements = {"word/document.xml": _serialize_xml(document_root), **header_footer_replacements}
            for part in STYLE_PARTS:
                if part in template_zip.namelist():
                    replacements[part] = template_zip.read(part)
            for info in article_zip.infolist():
                payload = replacements.get(info.filename)
                if payload is None:
                    payload = article_zip.read(info.filename)
                output_zip.writestr(info, payload)
            article_names = set(article_zip.namelist())
            for part, payload in replacements.items():
                if part not in article_names:
                    output_zip.writestr(part, payload)

        warnings = []
        if not rules:
            warnings.append("TEMPLATE roles produced no paragraph formatting rules; ARTICLE copy was written with only package-level style parts.")
        if template_profile and template_profile.layout.continuous_section_transition_patterns:
            warnings.append("Detected full-width section transition patterns; SafeWordEditor V2 currently applies front/body section layout only.")
        return SafeWordEditorResult(
            output_path=str(output_path),
            changes=[
                f"Applied TEMPLATE role formatting to {paragraph_changes} ARTICLE paragraphs.",
                "Copied TEMPLATE styles/numbering/font/theme parts into result DOCX.",
                (
                    "Merged TEMPLATE headers/footers into result section properties."
                    if copy_template_headers
                    else "Kept ARTICLE header/footer content; TEMPLATE header/footer text was not copied."
                ),
                "Preserved ARTICLE tables, drawings, formulas, media and document relationships.",
            ],
            warnings=warnings,
        )


class _TemplateRuleExtractor:
    def __init__(self, classifier: RoleClassifierV2):
        self.classifier = classifier

    def extract(self, template_path: Path, template_report: DocumentReport) -> dict[str, RoleOoxmlRule]:
        roles = self.classifier.classify(template_report)
        role_by_block = {item["block_id"]: item for item in roles.block_roles}
        with ZipFile(template_path) as archive:
            root = etree.fromstring(archive.read("word/document.xml"))
        body = root.find("w:body", namespaces=NS)
        if body is None:
            return {}
        candidates: dict[str, list[tuple[str, etree._Element]]] = {}
        for index, child in enumerate(body, start=1):
            if local_name(child) != "p" or not normalize_text(element_text(child)):
                continue
            block_id = f"block_{index:04d}"
            decision = role_by_block.get(block_id)
            role = str(decision.get("role_hint") or "") if decision else ""
            if not role:
                continue
            candidates.setdefault(role, []).append((block_id, child))
        return {
            role: RoleOoxmlRule(
                role=role,
                block_id=items[0][0],
                paragraph_properties=_best_paragraph_properties(role, [paragraph for _, paragraph in items]),
                run_properties=_best_text_run_properties(role, [paragraph for _, paragraph in items]),
            )
            for role, items in candidates.items()
        }


def _apply_body_formatting(
    *,
    body: etree._Element,
    role_by_block: dict[str, dict[str, Any]],
    rules: dict[str, RoleOoxmlRule],
    front_section: etree._Element | None,
    body_section: etree._Element | None,
    first_body_index: int | None,
    copy_template_headers: bool,
) -> int:
    changed = 0
    previous_paragraph: etree._Element | None = None
    for index, child in enumerate(body, start=1):
        if local_name(child) == "sectPr" and body_section is not None:
            replacement = _clone(body_section)
            if replacement is not None:
                if not copy_template_headers:
                    _replace_story_references(replacement, child)
                body.replace(child, replacement)
            continue
        if local_name(child) != "p":
            continue
        block_id = f"block_{index:04d}"
        block = role_by_block.get(block_id, {})
        role = str(block.get("detected_role") or "body")
        rule = rules.get(role) or rules.get("body")
        if rule and normalize_text(element_text(child)):
            _replace_paragraph_properties(child, rule.paragraph_properties)
            _apply_run_properties(child, rule.run_properties)
            changed += 1
        if first_body_index and index == first_body_index and previous_paragraph is not None and front_section is not None:
            _append_section_break(previous_paragraph, front_section, keep_story_references=copy_template_headers)
        previous_paragraph = child
    if body.find("w:sectPr", namespaces=NS) is None and body_section is not None:
        replacement = _clone(body_section)
        if replacement is not None:
            body.append(replacement)
    return changed


def _replace_paragraph_properties(paragraph: etree._Element, template_p_pr: etree._Element | None) -> None:
    existing = paragraph.find("w:pPr", namespaces=NS)
    existing_section = _clone(existing.find("w:sectPr", namespaces=NS)) if existing is not None and existing.find("w:sectPr", namespaces=NS) is not None else None
    if template_p_pr is None:
        new_p_pr = etree.Element(qn("w:pPr"))
    else:
        new_p_pr = _clone(template_p_pr)
    for item in list(new_p_pr):
        if local_name(item) == "sectPr":
            new_p_pr.remove(item)
    if existing_section is not None:
        new_p_pr.append(existing_section)
    _normalize_child_order(new_p_pr)
    if existing is None:
        paragraph.insert(0, new_p_pr)
    else:
        paragraph.replace(existing, new_p_pr)


def _apply_run_properties(paragraph: etree._Element, template_r_pr: etree._Element | None) -> None:
    if template_r_pr is None:
        return
    for run in paragraph.xpath(".//w:r[not(ancestor::m:oMath) and not(ancestor::m:oMathPara)]", namespaces=NS):
        if not normalize_text(element_text(run)):
            continue
        if run.xpath(".//w:drawing|.//w:pict|.//w:object|.//o:OLEObject", namespaces=NS):
            continue
        existing = run.find("w:rPr", namespaces=NS)
        preserved = _preserved_run_properties(existing)
        new_r_pr = _clone(template_r_pr)
        for child in preserved:
            _replace_child_by_local_name(new_r_pr, child)
        if existing is None:
            run.insert(0, new_r_pr)
        else:
            run.replace(existing, new_r_pr)


def _append_section_break(paragraph: etree._Element, section_properties: etree._Element, *, keep_story_references: bool = True) -> None:
    p_pr = paragraph.find("w:pPr", namespaces=NS)
    if p_pr is None:
        p_pr = etree.Element(qn("w:pPr"))
        paragraph.insert(0, p_pr)
    for child in list(p_pr):
        if local_name(child) == "sectPr":
            p_pr.remove(child)
    replacement = _clone(section_properties)
    if replacement is not None:
        if not keep_story_references:
            _remove_story_references(replacement)
        p_pr.append(replacement)


def _body_section_properties(template_path: Path, template_profile: TemplateProfile | None) -> etree._Element | None:
    sections = _section_properties(template_path)
    if not sections:
        return None
    target_columns = template_profile.layout.default_body_column_count if template_profile else 1
    section = _clone(_best_section_for_columns(sections, target_columns))
    if section is None:
        return None
    _strip_section_references(section)
    _set_section_columns(section, target_columns)
    return section


def _front_section_properties(template_path: Path) -> etree._Element | None:
    sections = _section_properties(template_path)
    if not sections:
        return None
    section = _clone(sections[0])
    if section is None:
        return None
    _strip_section_references(section)
    _set_section_columns(section, 1)
    section_type = section.find("w:type", namespaces=NS)
    if section_type is None:
        section_type = etree.Element(qn("w:type"))
        section.insert(0, section_type)
    section_type.set(qn("w:val"), "continuous")
    return section


def _section_properties(path: Path) -> list[etree._Element]:
    with ZipFile(path) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    return list(root.xpath("//w:sectPr", namespaces=NS))


def _best_section_for_columns(sections: list[etree._Element], count: int) -> etree._Element:
    for section in sections:
        if _section_column_count(section) == count:
            return section
    return sections[-1]


def _section_column_count(section: etree._Element) -> int:
    cols = section.find("w:cols", namespaces=NS)
    if cols is None:
        return 1
    value = cols.get(qn("w:num"))
    try:
        return int(value) if value else 1
    except ValueError:
        return 1


def _strip_section_references(section: etree._Element) -> None:
    for child in list(section):
        if local_name(child) == "pgNumType":
            section.remove(child)


def _remove_story_references(section: etree._Element) -> None:
    for child in list(section):
        if local_name(child) in {"headerReference", "footerReference", "pgNumType"}:
            section.remove(child)


def _replace_story_references(section: etree._Element, source_section: etree._Element) -> None:
    _remove_story_references(section)
    insertion_index = 0
    for child in source_section:
        if local_name(child) not in {"headerReference", "footerReference", "pgNumType"}:
            continue
        section.insert(insertion_index, _clone(child))
        insertion_index += 1


def _merge_header_footer(article_zip: ZipFile, template_zip: ZipFile, document_root: etree._Element) -> dict[str, bytes]:
    template_rels = _read_relationships(template_zip, "word/_rels/document.xml.rels")
    needed_ids = {
        node.get(qn("r:id"))
        for node in document_root.xpath("//w:sectPr/w:headerReference|//w:sectPr/w:footerReference", namespaces=NS)
        if node.get(qn("r:id"))
    }
    template_story_rels = {
        rel_id: rel
        for rel_id, rel in template_rels.items()
        if rel_id in needed_ids and rel.get("Type") in {HEADER_REL_TYPE, FOOTER_REL_TYPE}
    }
    if not template_story_rels:
        return {}

    article_rels_root = _relationships_root(article_zip)
    replacements: dict[str, bytes] = {}
    id_map: dict[str, str] = {}
    next_id = _next_relationship_id(article_rels_root)
    for template_id, rel in template_story_rels.items():
        new_id = f"rId{next_id}"
        next_id += 1
        id_map[template_id] = new_id
        relationship = etree.Element(f"{{{PACKAGE_REL_NS}}}Relationship")
        relationship.set("Id", new_id)
        relationship.set("Type", rel["Type"])
        relationship.set("Target", rel["Target"])
        if rel.get("TargetMode"):
            relationship.set("TargetMode", rel["TargetMode"])
        article_rels_root.append(relationship)
        story_part = _relationship_target_part("word/document.xml", rel["Target"])
        if story_part in template_zip.namelist():
            replacements[story_part] = template_zip.read(story_part)
        story_rels_part = _rels_part_name(story_part)
        if story_rels_part in template_zip.namelist():
            replacements[story_rels_part] = template_zip.read(story_rels_part)

    for node in document_root.xpath("//w:sectPr/w:headerReference|//w:sectPr/w:footerReference", namespaces=NS):
        rel_id = node.get(qn("r:id"))
        if rel_id in id_map:
            node.set(qn("r:id"), id_map[rel_id])

    replacements["word/_rels/document.xml.rels"] = _serialize_xml(article_rels_root)
    content_types = _merge_content_types(article_zip, template_zip, replacements)
    if content_types is not None:
        replacements["[Content_Types].xml"] = content_types
    return replacements


def _relationships_root(archive: ZipFile) -> etree._Element:
    if "word/_rels/document.xml.rels" in archive.namelist():
        return etree.fromstring(archive.read("word/_rels/document.xml.rels"))
    return etree.Element(f"{{{PACKAGE_REL_NS}}}Relationships")


def _read_relationships(archive: ZipFile, part: str) -> dict[str, dict[str, str]]:
    if part not in archive.namelist():
        return {}
    root = etree.fromstring(archive.read(part))
    return {
        item.get("Id"): dict(item.attrib)
        for item in root.findall(f"{{{PACKAGE_REL_NS}}}Relationship")
        if item.get("Id")
    }


def _next_relationship_id(root: etree._Element) -> int:
    highest = 0
    for item in root.findall(f"{{{PACKAGE_REL_NS}}}Relationship"):
        value = item.get("Id", "")
        if value.startswith("rId"):
            try:
                highest = max(highest, int(value[3:]))
            except ValueError:
                continue
    return highest + 1


def _relationship_target_part(source_part: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    source_dir = posix_parent(source_part)
    return posixpath_norm(f"{source_dir}/{target}")


def _rels_part_name(source_part: str) -> str:
    directory = posix_parent(source_part)
    name = source_part.rsplit("/", 1)[-1]
    return posixpath_norm(f"{directory}/_rels/{name}.rels")


def posix_parent(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def posixpath_norm(path: str) -> str:
    parts = []
    for part in path.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _merge_content_types(article_zip: ZipFile, template_zip: ZipFile, replacements: dict[str, bytes]) -> bytes | None:
    if "[Content_Types].xml" not in article_zip.namelist() or "[Content_Types].xml" not in template_zip.namelist():
        return None
    article_root = etree.fromstring(article_zip.read("[Content_Types].xml"))
    template_root = etree.fromstring(template_zip.read("[Content_Types].xml"))
    existing_overrides = {item.get("PartName") for item in article_root.findall(f"{{{CONTENT_TYPES_NS}}}Override")}
    existing_defaults = {item.get("Extension") for item in article_root.findall(f"{{{CONTENT_TYPES_NS}}}Default")}
    copied_part_names = {f"/{name}" for name in replacements if name.startswith("word/header") or name.startswith("word/footer")}
    for item in template_root.findall(f"{{{CONTENT_TYPES_NS}}}Override"):
        part_name = item.get("PartName")
        if part_name in copied_part_names and part_name not in existing_overrides:
            article_root.append(_clone(item))
            existing_overrides.add(part_name)
    for item in template_root.findall(f"{{{CONTENT_TYPES_NS}}}Default"):
        extension = item.get("Extension")
        if extension and extension not in existing_defaults:
            article_root.append(_clone(item))
            existing_defaults.add(extension)
    return _serialize_xml(article_root)


def _set_section_columns(section: etree._Element, count: int) -> None:
    cols = section.find("w:cols", namespaces=NS)
    if cols is None:
        cols = etree.Element(qn("w:cols"))
        section.append(cols)
    if count <= 1:
        cols.attrib.pop(qn("w:num"), None)
    else:
        cols.set(qn("w:num"), str(count))


def _first_body_content_index(article: ArticleStructure) -> int | None:
    for block in article.blocks:
        role = block.get("detected_role")
        if role in {"heading_1", "heading_2", "heading_3"} and block["kind"] == "paragraph":
            return int(block["index"])
    for block in article.blocks:
        role = block.get("detected_role")
        if role == "body" and float(block.get("confidence") or 0) >= 0.74 and block["kind"] == "paragraph":
            return int(block["index"])
    return None


def _best_text_run_properties(role: str, paragraphs: list[etree._Element]) -> etree._Element | None:
    prefer_plain = role in {"abstract", "body", "keywords", "citation", "reference_item", "figure_caption", "table_caption"}
    best: tuple[int, etree._Element] | None = None
    fallback: tuple[int, etree._Element] | None = None
    for paragraph in paragraphs:
        for run in paragraph.xpath(".//w:r[not(ancestor::m:oMath) and not(ancestor::m:oMathPara)]", namespaces=NS):
            text = normalize_text(element_text(run))
            if not text:
                continue
            r_pr = run.find("w:rPr", namespaces=NS)
            if r_pr is None:
                continue
            score = len(text)
            fallback = max(fallback or (0, r_pr), (score, r_pr), key=lambda item: item[0])
            if prefer_plain and (_has_bool_run_property(r_pr, "w:b") or _has_bool_run_property(r_pr, "w:i")):
                continue
            best = max(best or (0, r_pr), (score, r_pr), key=lambda item: item[0])
    selected = best or fallback
    return _clone(selected[1]) if selected else None


def _best_paragraph_properties(role: str, paragraphs: list[etree._Element]) -> etree._Element | None:
    prefer_flowing = role in {"abstract", "body", "citation", "reference_item"}
    best: tuple[int, etree._Element] | None = None
    fallback: tuple[int, etree._Element] | None = None
    for paragraph in paragraphs:
        p_pr = paragraph.find("w:pPr", namespaces=NS)
        if p_pr is None:
            continue
        score = len(normalize_text(element_text(paragraph)))
        fallback = max(fallback or (0, p_pr), (score, p_pr), key=lambda item: item[0])
        if prefer_flowing and _paragraph_alignment(p_pr) == "center":
            continue
        best = max(best or (0, p_pr), (score, p_pr), key=lambda item: item[0])
    selected = best or fallback
    return _clone(selected[1]) if selected else None


def _paragraph_alignment(paragraph_properties: etree._Element) -> str | None:
    jc = paragraph_properties.find("w:jc", namespaces=NS)
    return jc.get(qn("w:val")) if jc is not None else None


def _has_bool_run_property(run_properties: etree._Element, name: str) -> bool:
    node = run_properties.find(name, namespaces=NS)
    if node is None:
        return False
    return node.get(qn("w:val")) not in {"0", "false", "False", "off"}


def _preserved_run_properties(existing: etree._Element | None) -> list[etree._Element]:
    if existing is None:
        return []
    result = []
    for child in existing:
        if local_name(child) in {"vertAlign", "lang", "rtl", "cs"}:
            result.append(_clone(child))
    return result


def _replace_child_by_local_name(parent: etree._Element, child: etree._Element) -> None:
    child_name = local_name(child)
    for existing in list(parent):
        if local_name(existing) == child_name:
            parent.replace(existing, child)
            return
    parent.append(child)


def _normalize_child_order(parent: etree._Element) -> None:
    children = list(parent)
    if not children:
        return
    for child in children:
        parent.remove(child)
    for child in sorted(children, key=lambda item: PARAGRAPH_PROPERTY_ORDER.get(local_name(item), 100)):
        parent.append(child)


def _clone(element: etree._Element | None) -> etree._Element | None:
    if element is None:
        return None
    return etree.fromstring(etree.tostring(element, with_tail=False))


def _serialize_xml(root: etree._Element) -> bytes:
    etree.cleanup_namespaces(root)
    return etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True)
