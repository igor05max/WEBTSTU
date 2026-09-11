from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from typing import Any

from lxml import etree

from apps.template_workspace.v2.formatting.effective import EffectiveFormattingResolver
from apps.template_workspace.v2.models.document_info import (
    DocumentReport,
    DrawingInfo,
    EmbeddedObjectSummary,
    FlowBlock,
    FormulaInfo,
    HeaderFooterInfo,
    HyperlinkInfo,
    NumberingInfo,
    NumberingLevelInfo,
    PackageSummary,
    ParagraphInfo,
    RunInfo,
    SectionInfo,
    SemanticRoleLayer,
    StyleInfo,
    TableCellInfo,
    TableInfo,
)
from apps.template_workspace.v2.models.fingerprint import DocumentFingerprint
from apps.template_workspace.v2.ooxml.namespaces import NS, local_name, qn, xml_string
from apps.template_workspace.v2.ooxml.package import WordPackage


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def sha_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def first_child(element: etree._Element | None, name: str) -> etree._Element | None:
    return None if element is None else element.find(qn(name))


def child_attr(element: etree._Element | None, child_name: str, attr_name: str = "w:val") -> str | None:
    child = first_child(element, child_name)
    return child.get(qn(attr_name)) if child is not None else None


def bool_prop(element: etree._Element | None, child_name: str) -> bool | None:
    child = first_child(element, child_name)
    if child is None:
        return None
    value = child.get(qn("w:val"))
    return value not in {"0", "false", "False", "off"}


def selected_attrs(element: etree._Element | None, names: list[str]) -> dict[str, Any]:
    if element is None:
        return {}
    result: dict[str, Any] = {}
    for name in names:
        child = first_child(element, name)
        if child is None:
            continue
        attrs: dict[str, str] = {}
        for attr_key, attr_value in child.attrib.items():
            attrs[etree.QName(attr_key).localname] = attr_value
        result[name.split(":", 1)[1]] = attrs or True
    return result


def element_text(element: etree._Element) -> str:
    return "".join(element.xpath(".//w:t/text()", namespaces=NS))


def rel_target(package: WordPackage, rel_id: str | None, source_part: str = "word/document.xml") -> str | None:
    if not rel_id:
        return None
    relationship = package.relationships_for(source_part).get(rel_id)
    return relationship.resolved_target if relationship is not None else None


class DocumentInspector:
    """Builds a read-only structural report for a DOCX package."""

    def __init__(self, path: Path | str, *, semantic_cache_dir: Path | None = None):
        self.package = WordPackage(path)
        self.path = Path(path)
        self.semantic_cache_dir = semantic_cache_dir
        self._formatting: EffectiveFormattingResolver | None = None
        self._style_names: dict[str, str] = {}
        self._style_props: dict[str, dict[str, Any]] = {}
        self._paragraphs: list[ParagraphInfo] = []
        self._tables: list[TableInfo] = []
        self._drawings: list[DrawingInfo] = []
        self._formulas: list[FormulaInfo] = []
        self._hyperlinks: list[HyperlinkInfo] = []

    def inspect(self) -> DocumentReport:
        styles = self._inspect_styles()
        self._style_names = {style.style_id: style.name or style.style_id for style in styles}
        self._style_props = {
            style.style_id: {
                "paragraph": style.paragraph_properties,
                "run": style.run_properties,
                "based_on": style.based_on,
            }
            for style in styles
        }
        self._formatting = EffectiveFormattingResolver(self.package.styles, styles)
        flow = self._inspect_flow()
        sections = self._inspect_sections(flow)
        headers = self._inspect_story_parts(self.package.header_parts, "header")
        footers = self._inspect_story_parts(self.package.footer_parts, "footer")
        numbering = self._inspect_numbering()
        fingerprint = self._fingerprint(sections, headers, footers, numbering)
        embedded_objects = self._embedded_objects_summary()
        relationships = self.package.relationships
        package_summary = PackageSummary(
            path=str(self.path),
            part_count=len(self.package.names),
            important_parts=[part.name for part in self.package.package_parts if part.important],
            media_files=self.package.media_parts,
            relationship_count=sum(len(items) for items in relationships.values()),
        )
        return DocumentReport(
            source_path=str(self.path),
            package=package_summary,
            flow=flow,
            paragraphs=self._paragraphs,
            tables=self._tables,
            drawings=self._drawings,
            formulas=self._formulas,
            hyperlinks=self._hyperlinks,
            sections=sections,
            headers=headers,
            footers=footers,
            styles=styles,
            numbering=numbering,
            fingerprint=asdict(fingerprint),
            embedded_objects=embedded_objects,
        )

    def _inspect_flow(self) -> list[FlowBlock]:
        root = self.package.main_document
        body = root.find("w:body", namespaces=NS)
        if body is None:
            return []
        flow: list[FlowBlock] = []
        paragraph_index = 0
        table_index = 0
        for index, child in enumerate(body, start=1):
            name = local_name(child)
            if name == "p":
                paragraph_index += 1
                paragraph = self._inspect_paragraph(child, paragraph_index, f"block_{index:04d}")
                self._paragraphs.append(paragraph)
                flow.append(
                    FlowBlock(
                        id=paragraph.id,
                        kind="paragraph",
                        index=index,
                        text_preview=paragraph.normalized_text[:180],
                        role_hint=paragraph.role_hint,
                        object_id=paragraph.id,
                    )
                )
            elif name == "tbl":
                table_index += 1
                table = self._inspect_table(child, table_index, f"block_{index:04d}", self._nearby_caption(body, index - 1))
                self._tables.append(table)
                flow.append(
                    FlowBlock(
                        id=table.id,
                        kind="table",
                        index=index,
                        text_preview=table.caption_nearby or "",
                        role_hint="table",
                        object_id=table.id,
                    )
                )
            elif name == "sectPr":
                flow.append(FlowBlock(id=f"block_{index:04d}", kind="section_properties", index=index, text_preview=""))
        return flow

    def _inspect_paragraph(self, paragraph: etree._Element, index: int, block_id: str) -> ParagraphInfo:
        p_pr = first_child(paragraph, "w:pPr")
        style_id = child_attr(p_pr, "w:pStyle")
        normalized = normalize_text(element_text(paragraph))
        drawings = self._inspect_drawings(paragraph, block_id, self._caption_hint(normalized))
        formulas = self._inspect_formulas(paragraph, block_id)
        hyperlinks = self._inspect_hyperlinks(paragraph, block_id)
        self._drawings.extend(drawings)
        self._formulas.extend(formulas)
        self._hyperlinks.extend(hyperlinks)
        effective = self._formatting.paragraph(style_id, self._paragraph_properties(p_pr)) if self._formatting else {}
        return ParagraphInfo(
            id=block_id,
            index=index,
            text=element_text(paragraph),
            normalized_text=normalized,
            style_id=style_id,
            style_name=self._style_names.get(style_id or ""),
            role_hint=self._role_hint(normalized, style_id),
            properties=self._paragraph_properties(p_pr),
            direct_formatting=selected_attrs(p_pr, ["w:jc", "w:spacing", "w:ind", "w:keepNext", "w:keepLines", "w:pageBreakBefore"]),
            numbering=self._numbering_properties(p_pr),
            effective_formatting=effective,
            runs=self._inspect_runs(paragraph, style_id),
            drawings=drawings,
            formulas=formulas,
            hyperlinks=hyperlinks,
        )

    def _inspect_runs(self, paragraph: etree._Element, style_id: str | None) -> list[RunInfo]:
        runs: list[RunInfo] = []
        for index, run in enumerate(paragraph.xpath(".//w:r[not(ancestor::m:oMath) and not(ancestor::m:oMathPara)]", namespaces=NS), start=1):
            r_pr = first_child(run, "w:rPr")
            font = self._run_properties(r_pr)
            run_style_id = child_attr(r_pr, "w:rStyle")
            runs.append(
                RunInfo(
                    index=index,
                    text=element_text(run),
                    style_id=run_style_id,
                    font=font,
                    direct_formatting={key: value for key, value in font.items() if value not in (None, {}, [])},
                    inherited_formatting=self._style_props.get(style_id or "", {}).get("run", {}),
                    effective_formatting=self._formatting.run(style_id, run_style_id, font) if self._formatting else font,
                )
            )
        return runs

    def _inspect_table(self, table: etree._Element, index: int, block_id: str, caption: str | None) -> TableInfo:
        grid = [grid_col.get(qn("w:w")) for grid_col in table.xpath("./w:tblGrid/w:gridCol", namespaces=NS)]
        table_drawings = self._inspect_drawings(table, block_id, caption)
        table_formulas = self._inspect_formulas(table, block_id)
        table_hyperlinks = self._inspect_hyperlinks(table, block_id)
        self._drawings.extend(table_drawings)
        self._formulas.extend(table_formulas)
        self._hyperlinks.extend(table_hyperlinks)
        rows: list[list[TableCellInfo]] = []
        merge_cells: list[dict[str, Any]] = []
        xml_cell_count = 0
        max_columns = len(grid)
        nested_table_count = len(table.xpath(".//w:tbl", namespaces=NS)) - 1
        for row_index, row in enumerate(table.xpath("./w:tr", namespaces=NS), start=1):
            row_cells: list[TableCellInfo] = []
            cursor = 0
            for cell in row.xpath("./w:tc", namespaces=NS):
                xml_cell_count += 1
                tc_pr = first_child(cell, "w:tcPr")
                grid_span = self._int(child_attr(tc_pr, "w:gridSpan"), 1)
                v_merge = child_attr(tc_pr, "w:vMerge") if first_child(tc_pr, "w:vMerge") is not None else None
                if first_child(tc_pr, "w:vMerge") is not None and v_merge is None:
                    v_merge = "continue"
                text = normalize_text(element_text(cell))
                cell_info = TableCellInfo(
                    row=row_index,
                    grid_column=cursor + 1,
                    grid_span=grid_span,
                    v_merge=v_merge,
                    width=self._width_dict(first_child(tc_pr, "w:tcW")),
                    text=text[:500],
                    has_drawing=bool(cell.xpath(".//w:drawing|.//w:pict", namespaces=NS)),
                    has_formula=bool(cell.xpath(".//m:oMath|.//m:oMathPara", namespaces=NS)),
                    nested_table_count=max(0, len(cell.xpath(".//w:tbl", namespaces=NS)) - 1),
                )
                row_cells.append(cell_info)
                if grid_span > 1 or v_merge:
                    merge_cells.append(
                        {
                            "row": row_index,
                            "grid_column": cursor + 1,
                            "grid_span": grid_span,
                            "v_merge": v_merge,
                        }
                    )
                cursor += grid_span
            max_columns = max(max_columns, cursor)
            rows.append(row_cells)
        structure_payload = {
            "rows": len(rows),
            "columns": max_columns,
            "grid": grid,
            "cell_spans": [
                [(cell.grid_column, cell.grid_span, cell.v_merge) for cell in row]
                for row in rows
            ],
        }
        content_payload = [[cell.text for cell in row] for row in rows]
        merge_payload = [[(cell.grid_column, cell.grid_span, cell.v_merge) for cell in row if cell.grid_span > 1 or cell.v_merge] for row in rows]
        return TableInfo(
            id=block_id,
            index=index,
            row_count=len(rows),
            xml_cell_count=xml_cell_count,
            logical_column_count=max_columns,
            grid=grid,
            tbl_properties=self._table_properties(table),
            rows=rows,
            merge_cells=merge_cells,
            nested_table_count=nested_table_count,
            has_drawings=bool(table.xpath(".//w:drawing|.//w:pict", namespaces=NS)),
            has_formulas=bool(table.xpath(".//m:oMath|.//m:oMathPara", namespaces=NS)),
            classification=self._classify_table(rows, table_drawings, nested_table_count),
            structure_hash=sha_text(repr(structure_payload)),
            content_hash=sha_text(repr(content_payload)),
            merge_topology_hash=sha_text(repr(merge_payload)),
            caption_nearby=caption,
        )

    def _inspect_drawings(self, root: etree._Element, block_id: str, caption: str | None) -> list[DrawingInfo]:
        result: list[DrawingInfo] = []
        media_hashes = self.package.media_hashes
        for index, drawing in enumerate(root.xpath(".//w:drawing|.//w:pict", namespaces=NS), start=1):
            inline = bool(drawing.xpath("./wp:inline", namespaces=NS))
            anchor = bool(drawing.xpath("./wp:anchor", namespaces=NS))
            rel_id = None
            blip = drawing.xpath(".//a:blip", namespaces=NS)
            if blip:
                rel_id = blip[0].get(qn("r:embed")) or blip[0].get(qn("r:link"))
            image_data = drawing.xpath(".//v:imagedata", namespaces=NS)
            if rel_id is None and image_data:
                rel_id = image_data[0].get(qn("r:id"))
            target = rel_target(self.package, rel_id)
            extent = drawing.xpath(".//wp:extent", namespaces=NS)
            result.append(
                DrawingInfo(
                    id=f"{block_id}_drawing_{index:03d}",
                    block_id=block_id,
                    kind=self._drawing_kind(drawing, target),
                    inline=inline,
                    anchor=anchor,
                    relationship_id=rel_id,
                    media_target=target,
                    media_sha256=media_hashes.get(target or ""),
                    size=dict(extent[0].attrib) if extent else {},
                    position={
                        "positionH": [dict(item.attrib) for item in drawing.xpath(".//wp:positionH", namespaces=NS)],
                        "positionV": [dict(item.attrib) for item in drawing.xpath(".//wp:positionV", namespaces=NS)],
                    },
                    caption_nearby=caption,
                )
            )
        return result

    def _inspect_formulas(self, root: etree._Element, block_id: str) -> list[FormulaInfo]:
        result: list[FormulaInfo] = []
        for index, formula in enumerate(root.xpath(".//m:oMathPara|.//m:oMath[not(ancestor::m:oMathPara)]", namespaces=NS), start=1):
            xml = xml_string(formula)
            result.append(
                FormulaInfo(
                    id=f"{block_id}_formula_{index:03d}",
                    block_id=block_id,
                    kind=local_name(formula),
                    xml_hash=sha_text(xml),
                    display=local_name(formula) == "oMathPara",
                )
            )
        offset = len(result)
        for index, formula in enumerate(root.xpath(".//w:object|.//o:OLEObject", namespaces=NS), start=1):
            target = None
            rel_id = formula.get(qn("r:id")) or formula.get(qn("r:embed"))
            if rel_id:
                target = rel_target(self.package, rel_id)
            result.append(
                FormulaInfo(
                    id=f"{block_id}_formula_{offset + index:03d}",
                    block_id=block_id,
                    kind=local_name(formula),
                    xml_hash=sha_text(xml_string(formula)),
                    display=False,
                    embedded_target=target,
                )
            )
        return result

    def _inspect_hyperlinks(self, paragraph: etree._Element, block_id: str) -> list[HyperlinkInfo]:
        result: list[HyperlinkInfo] = []
        for index, hyperlink in enumerate(paragraph.xpath(".//w:hyperlink", namespaces=NS), start=1):
            rel_id = hyperlink.get(qn("r:id"))
            result.append(
                HyperlinkInfo(
                    id=f"{block_id}_hyperlink_{index:03d}",
                    block_id=block_id,
                    relationship_id=rel_id,
                    anchor=hyperlink.get(qn("w:anchor")),
                    target=rel_target(self.package, rel_id),
                    text=normalize_text(element_text(hyperlink)),
                )
            )
        return result

    def _inspect_sections(self, flow: list[FlowBlock]) -> list[SectionInfo]:
        result: list[SectionInfo] = []
        section_break_blocks = [
            block for block in flow if block.kind == "paragraph" and block.object_id in {
                paragraph.id
                for paragraph in self._paragraphs
                if paragraph.properties.get("section_break")
            }
        ]
        for index, sect_pr in enumerate(self.package.main_document.xpath("//w:sectPr", namespaces=NS), start=1):
            result.append(self._section_info(sect_pr, index, flow, section_break_blocks))
        return result

    def _section_info(self, sect_pr: etree._Element, index: int, flow: list[FlowBlock], section_break_blocks: list[FlowBlock]) -> SectionInfo:
        page_size = first_child(sect_pr, "w:pgSz")
        margins = first_child(sect_pr, "w:pgMar")
        cols = first_child(sect_pr, "w:cols")
        start_index = 1
        end_index = flow[-1].index if flow else 0
        if index > 1 and index - 2 < len(section_break_blocks):
            start_index = section_break_blocks[index - 2].index + 1
        if index - 1 < len(section_break_blocks):
            end_index = section_break_blocks[index - 1].index
        start_block = next((block.id for block in flow if block.index >= start_index and block.kind != "section_properties"), None)
        end_block = next((block.id for block in reversed(flow) if block.index <= end_index and block.kind != "section_properties"), None)
        previous_block = next((block.id for block in reversed(flow) if block.index < start_index and block.kind != "section_properties"), None)
        next_block = next((block.id for block in flow if block.index > end_index and block.kind != "section_properties"), None)
        return SectionInfo(
            id=f"section_{index:03d}",
            index=index,
            source=self._section_source(sect_pr),
            page_size=dict(page_size.attrib) if page_size is not None else {},
            margins=dict(margins.attrib) if margins is not None else {},
            columns=dict(cols.attrib) if cols is not None else {"w:num": "1"},
            section_type=child_attr(sect_pr, "w:type"),
            header_refs=[self._ref_dict(item) for item in sect_pr.xpath("./w:headerReference", namespaces=NS)],
            footer_refs=[self._ref_dict(item) for item in sect_pr.xpath("./w:footerReference", namespaces=NS)],
            page_numbering=dict(first_child(sect_pr, "w:pgNumType").attrib) if first_child(sect_pr, "w:pgNumType") is not None else {},
            start_block=start_block,
            end_block=end_block,
            previous_block=previous_block,
            next_block=next_block,
        )

    def _inspect_story_parts(self, parts: list[str], kind: str) -> list[HeaderFooterInfo]:
        result: list[HeaderFooterInfo] = []
        for part in parts:
            root = self.package.xml(part)
            if root is None:
                continue
            fields = root.xpath(".//w:instrText|.//w:fldSimple", namespaces=NS)
            field_text = " ".join((item.text or item.get(qn("w:instr")) or "") for item in fields)
            result.append(
                HeaderFooterInfo(
                    part=part,
                    kind=kind,
                    paragraph_count=len(root.xpath(".//w:p", namespaces=NS)),
                    text=normalize_text(element_text(root)),
                    drawing_count=len(root.xpath(".//w:drawing|.//w:pict", namespaces=NS)),
                    field_count=len(fields),
                    page_number_field_count=len(re.findall(r"\bPAGE\b", field_text, flags=re.IGNORECASE)),
                    paragraph_border_count=len(root.xpath(".//w:pPr/w:pBdr", namespaces=NS)),
                    relationships=[rel.to_dict() for rel in self.package.relationships_for(part).values()],
                )
            )
        return result

    def _inspect_styles(self) -> list[StyleInfo]:
        root = self.package.styles
        if root is None:
            return []
        result: list[StyleInfo] = []
        for style in root.xpath("./w:style", namespaces=NS):
            style_id = style.get(qn("w:styleId")) or ""
            result.append(
                StyleInfo(
                    style_id=style_id,
                    type=style.get(qn("w:type")) or "",
                    name=child_attr(style, "w:name"),
                    default=(style.get(qn("w:default")) in {"1", "true", "on"}),
                    based_on=child_attr(style, "w:basedOn"),
                    next_style=child_attr(style, "w:next"),
                    linked=child_attr(style, "w:link"),
                    paragraph_properties=self._paragraph_properties(first_child(style, "w:pPr")),
                    run_properties=self._run_properties(first_child(style, "w:rPr")),
                )
            )
        return result

    def _inspect_numbering(self) -> NumberingInfo:
        root = self.package.numbering
        if root is None:
            return NumberingInfo(abstract_nums=[], nums=[])
        abstract_nums: list[dict[str, Any]] = []
        for abstract in root.xpath("./w:abstractNum", namespaces=NS):
            levels = []
            for level in abstract.xpath("./w:lvl", namespaces=NS):
                levels.append(
                    asdict(
                        NumberingLevelInfo(
                            ilvl=level.get(qn("w:ilvl")) or "",
                            start=child_attr(level, "w:start"),
                            num_format=child_attr(level, "w:numFmt"),
                            level_text=child_attr(level, "w:lvlText"),
                            paragraph_properties=self._paragraph_properties(first_child(level, "w:pPr")),
                            run_properties=self._run_properties(first_child(level, "w:rPr")),
                        )
                    )
                )
            abstract_nums.append({"abstractNumId": abstract.get(qn("w:abstractNumId")), "levels": levels})
        nums = []
        for num in root.xpath("./w:num", namespaces=NS):
            nums.append({"numId": num.get(qn("w:numId")), "abstractNumId": child_attr(num, "w:abstractNumId")})
        return NumberingInfo(abstract_nums=abstract_nums, nums=nums)

    def _paragraph_properties(self, p_pr: etree._Element | None) -> dict[str, Any]:
        if p_pr is None:
            return {}
        return {
            "style_id": child_attr(p_pr, "w:pStyle"),
            "alignment": child_attr(p_pr, "w:jc"),
            "indentation": dict(first_child(p_pr, "w:ind").attrib) if first_child(p_pr, "w:ind") is not None else {},
            "spacing": dict(first_child(p_pr, "w:spacing").attrib) if first_child(p_pr, "w:spacing") is not None else {},
            "keepNext": bool_prop(p_pr, "w:keepNext"),
            "keepLines": bool_prop(p_pr, "w:keepLines"),
            "pageBreakBefore": bool_prop(p_pr, "w:pageBreakBefore"),
            "outline_level": child_attr(p_pr, "w:outlineLvl"),
            "borders": bool(first_child(p_pr, "w:pBdr") is not None),
            "section_break": first_child(p_pr, "w:sectPr") is not None,
        }

    def _run_properties(self, r_pr: etree._Element | None) -> dict[str, Any]:
        if r_pr is None:
            return {}
        fonts = first_child(r_pr, "w:rFonts")
        color = first_child(r_pr, "w:color")
        underline = first_child(r_pr, "w:u")
        return {
            "style_id": child_attr(r_pr, "w:rStyle"),
            "fonts": dict(fonts.attrib) if fonts is not None else {},
            "size": child_attr(r_pr, "w:sz"),
            "size_cs": child_attr(r_pr, "w:szCs"),
            "bold": bool_prop(r_pr, "w:b"),
            "italic": bool_prop(r_pr, "w:i"),
            "underline": underline.get(qn("w:val")) if underline is not None else None,
            "vertical_align": child_attr(r_pr, "w:vertAlign"),
            "color": color.get(qn("w:val")) if color is not None else None,
            "language": dict(first_child(r_pr, "w:lang").attrib) if first_child(r_pr, "w:lang") is not None else {},
            "character_style_id": child_attr(r_pr, "w:rStyle"),
        }

    def _numbering_properties(self, p_pr: etree._Element | None) -> dict[str, Any] | None:
        num_pr = first_child(p_pr, "w:numPr")
        if num_pr is None:
            return None
        return {"numId": child_attr(num_pr, "w:numId"), "ilvl": child_attr(num_pr, "w:ilvl")}

    def _table_properties(self, table: etree._Element) -> dict[str, Any]:
        tbl_pr = first_child(table, "w:tblPr")
        if tbl_pr is None:
            return {}
        return {
            "style_id": child_attr(tbl_pr, "w:tblStyle"),
            "alignment": child_attr(tbl_pr, "w:jc"),
            "width": self._width_dict(first_child(tbl_pr, "w:tblW")),
            "layout": child_attr(tbl_pr, "w:tblLayout", "w:type"),
            "borders": selected_attrs(tbl_pr, ["w:tblBorders"]).get("tblBorders", {}),
            "cell_margins": selected_attrs(tbl_pr, ["w:tblCellMar"]).get("tblCellMar", {}),
        }

    @staticmethod
    def _width_dict(element: etree._Element | None) -> dict[str, Any] | None:
        if element is None:
            return None
        return {"w": element.get(qn("w:w")), "type": element.get(qn("w:type"))}

    @staticmethod
    def _ref_dict(element: etree._Element) -> dict[str, Any]:
        return {
            "type": element.get(qn("w:type")),
            "relationship_id": element.get(qn("r:id")),
        }

    @staticmethod
    def _section_source(sect_pr: etree._Element) -> str:
        parent = sect_pr.getparent()
        if parent is not None and local_name(parent) == "pPr":
            paragraph = parent.getparent()
            if paragraph is not None:
                return "paragraph"
        return "body"

    @staticmethod
    def _int(value: str | None, default: int) -> int:
        try:
            return int(value) if value is not None else default
        except ValueError:
            return default

    def _nearby_caption(self, body: etree._Element, table_position_zero_based: int) -> str | None:
        candidates = []
        for offset in (-1, 1):
            position = table_position_zero_based + offset
            if 0 <= position < len(body) and local_name(body[position]) == "p":
                candidates.append(normalize_text(element_text(body[position])))
        for text in candidates:
            if self._caption_hint(text):
                return text
        return None

    @staticmethod
    def _caption_hint(text: str) -> str | None:
        if re.match(r"^(рис\.?|рисунок|figure|fig\.|таблица|table)\s*\d+", text or "", flags=re.IGNORECASE):
            return text
        return None

    def _role_hint(self, text: str, style_id: str | None) -> str | None:
        style_name = (self._style_names.get(style_id or "") or "").lower()
        lowered = (text or "").lower()
        if not text:
            return None
        if "title" in style_name or "заглав" in style_name or "название" in style_name:
            return "title"
        if "heading" in style_name or "заголовок" in style_name:
            return "heading"
        if self._caption_hint(text):
            return "figure_caption" if re.match(r"^(рис|figure|fig)", lowered) else "table_caption"
        if re.match(r"^(abstract|аннотация)\b", lowered):
            return "abstract"
        if re.match(r"^(keywords|key words|ключевые слова)\b", lowered):
            return "keywords"
        if re.match(r"^(references|литература|список литературы)\b", lowered):
            return "references_heading"
        if "@" in text and len(text) < 160:
            return "email"
        return "body"

    @staticmethod
    def _classify_table(rows: list[list[TableCellInfo]], drawings: list[DrawingInfo], nested_table_count: int) -> str:
        cell_count = sum(len(row) for row in rows)
        drawing_cells = sum(1 for row in rows for cell in row if cell.has_drawing)
        text_cells = sum(1 for row in rows for cell in row if cell.text)
        if cell_count and drawing_cells / cell_count >= 0.5:
            return "FIGURE_CONTAINER"
        if nested_table_count or (cell_count <= 4 and drawing_cells and text_cells <= drawing_cells):
            return "LAYOUT_TABLE"
        if text_cells >= 2 and len(rows) >= 2:
            return "DATA_TABLE"
        if drawings:
            return "FIGURE_CONTAINER"
        return "UNKNOWN"

    @staticmethod
    def _drawing_kind(drawing: etree._Element, media_target: str | None) -> str:
        if drawing.xpath(".//c:chart", namespaces={**NS, "c": "http://schemas.openxmlformats.org/drawingml/2006/chart"}):
            return "CHART"
        if drawing.xpath(".//o:OLEObject", namespaces=NS):
            return "OLE_PREVIEW"
        if drawing.xpath(".//v:shape", namespaces=NS) and not media_target:
            return "SHAPE"
        if media_target:
            return "PICTURE"
        return "UNKNOWN"

    def _embedded_objects_summary(self) -> EmbeddedObjectSummary:
        ole_objects = self.package.main_document.xpath("//w:object|//o:OLEObject", namespaces=NS)
        embedding_parts = sorted(name for name in self.package.names if name.startswith("word/embeddings/"))
        equation_markers = ("equation", "mathtype", "oleobject")
        ole_equations = [
            item
            for item in ole_objects
            if any(marker in xml_string(item).lower() for marker in equation_markers)
        ]
        return EmbeddedObjectSummary(
            omml_formula_count=sum(1 for formula in self._formulas if formula.kind in {"oMath", "oMathPara"}),
            ole_equation_count=len(ole_equations),
            unknown_ole_count=max(0, len(ole_objects) - len(ole_equations)),
            embedding_parts=embedding_parts,
        )

    def _fingerprint(
        self,
        sections: list[SectionInfo],
        headers: list[HeaderFooterInfo],
        footers: list[HeaderFooterInfo],
        numbering: NumberingInfo,
    ) -> DocumentFingerprint:
        relationships = self.package.relationships
        text = "\n".join(paragraph.normalized_text for paragraph in self._paragraphs if paragraph.normalized_text)
        table_payload = [
            {
                "rows": table.row_count,
                "columns": table.logical_column_count,
                "grid": table.grid,
                "merges": table.merge_cells,
                "hash": table.structure_hash,
            }
            for table in self._tables
        ]
        formula_hashes = [formula.xml_hash for formula in self._formulas]
        return DocumentFingerprint(
            paragraph_count=len(self._paragraphs),
            table_count=len(self._tables),
            drawing_count=len(self._drawings),
            image_count=sum(1 for drawing in self._drawings if drawing.media_target),
            formula_count=len(self._formulas),
            hyperlink_count=len(self._hyperlinks),
            bookmark_count=len(self.package.main_document.xpath("//w:bookmarkStart", namespaces=NS)),
            section_count=len(sections),
            header_count=len(headers),
            footer_count=len(footers),
            relationship_count=sum(len(items) for items in relationships.values()),
            media_files=self.package.media_parts,
            numbering_definitions={
                "abstractNum": len(numbering.abstract_nums),
                "num": len(numbering.nums),
            },
            normalized_text_hash=sha_text(normalize_text(text)),
            table_structure_hash=sha_text(repr(table_payload)),
            media_hashes=self.package.media_hashes,
            formula_xml_hashes=formula_hashes,
            critical_object_hashes={
                "tables": [table.structure_hash for table in self._tables],
                "formulas": formula_hashes,
                "media": self.package.media_hashes,
                "headers": {header.part: sha_text(header.text) for header in headers},
                "footers": {footer.part: sha_text(footer.text) for footer in footers},
            },
        )
