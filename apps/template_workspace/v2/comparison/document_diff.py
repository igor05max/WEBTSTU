from __future__ import annotations

from collections import Counter
from typing import Any

from apps.template_workspace.v2.models.diff import Change, DocumentDiff
from apps.template_workspace.v2.models.document_info import DocumentReport, FlowBlock, ParagraphInfo


def compact_props(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, {}, [], "")}


class DocumentDiffBuilder:
    def compare(self, before: DocumentReport, after: DocumentReport) -> DocumentDiff:
        diff = DocumentDiff(
            source_path=before.source_path,
            target_path=after.source_path,
            summary=self._summary(before, after),
        )
        self._compare_fingerprints(before, after, diff)
        self._compare_layout(before, after, diff)
        self._compare_flow(before.flow, after.flow, diff)
        self._compare_styles(before, after, diff)
        self._compare_paragraphs(before.paragraphs, after.paragraphs, diff)
        self._compare_tables(before, after, diff)
        self._compare_drawings(before, after, diff)
        self._compare_formulas(before, after, diff)
        self._compare_stories(before.headers, after.headers, diff, "header")
        self._compare_stories(before.footers, after.footers, diff, "footer")
        return diff

    def _summary(self, before: DocumentReport, after: DocumentReport) -> dict[str, Any]:
        before_fp = before.fingerprint
        after_fp = after.fingerprint
        return {
            "count_delta": {
                key: after_fp.get(key, 0) - before_fp.get(key, 0)
                for key in (
                    "paragraph_count",
                    "table_count",
                    "drawing_count",
                    "image_count",
                    "formula_count",
                    "hyperlink_count",
                    "section_count",
                    "header_count",
                    "footer_count",
                    "relationship_count",
                )
            },
            "text_hash_changed": before_fp.get("normalized_text_hash") != after_fp.get("normalized_text_hash"),
            "table_structure_changed": before_fp.get("table_structure_hash") != after_fp.get("table_structure_hash"),
            "media_changed": before_fp.get("media_hashes") != after_fp.get("media_hashes"),
            "formula_hashes_changed": before_fp.get("formula_xml_hashes") != after_fp.get("formula_xml_hashes"),
            "role_counts_before": before.semantic_roles.role_counts,
            "role_counts_after": after.semantic_roles.role_counts,
        }

    def _compare_fingerprints(self, before: DocumentReport, after: DocumentReport, diff: DocumentDiff) -> None:
        if before.fingerprint.get("normalized_text_hash") != after.fingerprint.get("normalized_text_hash"):
            diff.content_changes.append(
                Change(
                    category="fingerprint",
                    change_type="normalized_text_hash",
                    source="document",
                    target="document",
                    before=before.fingerprint.get("normalized_text_hash"),
                    after=after.fingerprint.get("normalized_text_hash"),
                    classification="CONTENT",
                )
            )
        for key in ("paragraph_count", "table_count", "drawing_count", "image_count", "formula_count", "hyperlink_count"):
            if before.fingerprint.get(key) != after.fingerprint.get(key):
                diff.content_changes.append(
                    Change(
                        category="fingerprint",
                        change_type=f"{key}_changed",
                        source="document",
                        target="document",
                        before=before.fingerprint.get(key),
                        after=after.fingerprint.get(key),
                        classification="CONTENT" if key in {"formula_count", "hyperlink_count"} else "FLOW",
                    )
                )

    def _compare_layout(self, before: DocumentReport, after: DocumentReport, diff: DocumentDiff) -> None:
        self._append_count_change(diff.sections, "sections", before.fingerprint["section_count"], after.fingerprint["section_count"], "LAYOUT")
        self._append_count_change(diff.header_changes, "headers", before.fingerprint["header_count"], after.fingerprint["header_count"], "LAYOUT")
        self._append_count_change(diff.footer_changes, "footers", before.fingerprint["footer_count"], after.fingerprint["footer_count"], "LAYOUT")
        for index, (left, right) in enumerate(zip(before.sections, after.sections), start=1):
            for field in ("page_size", "margins", "columns", "section_type", "header_refs", "footer_refs", "page_numbering"):
                before_value = getattr(left, field)
                after_value = getattr(right, field)
                if before_value != after_value:
                    change = Change(
                        category="section",
                        change_type=f"{field}_changed",
                        source=left.id,
                        target=right.id,
                        before=before_value,
                        after=after_value,
                        classification="LAYOUT",
                    )
                    diff.layout.append(change)
                    diff.sections.append(change)

    def _compare_flow(self, before: list[FlowBlock], after: list[FlowBlock], diff: DocumentDiff) -> None:
        before_counts = Counter(block.kind for block in before)
        after_counts = Counter(block.kind for block in after)
        if before_counts != after_counts:
            diff.flow_changes.append(
                Change(
                    category="flow",
                    change_type="block_kind_counts_changed",
                    source="document",
                    target="document",
                    before=dict(before_counts),
                    after=dict(after_counts),
                    classification="FLOW",
                )
            )
        before_sequence = [block.kind for block in before]
        after_sequence = [block.kind for block in after]
        if before_sequence != after_sequence:
            diff.flow_changes.append(
                Change(
                    category="flow",
                    change_type="block_sequence_changed",
                    source="document",
                    target="document",
                    before=before_sequence[:240],
                    after=after_sequence[:240],
                    classification="FLOW",
                )
            )

    def _compare_styles(self, before: DocumentReport, after: DocumentReport, diff: DocumentDiff) -> None:
        before_styles = {style.style_id: style for style in before.styles}
        after_styles = {style.style_id: style for style in after.styles}
        if set(before_styles) != set(after_styles):
            diff.styles.append(
                Change(
                    category="styles",
                    change_type="style_set_changed",
                    source="styles.xml",
                    target="styles.xml",
                    before=sorted(set(before_styles) - set(after_styles)),
                    after=sorted(set(after_styles) - set(before_styles)),
                    classification="FORMAT",
                )
            )
        for style_id in sorted(set(before_styles) & set(after_styles)):
            left = before_styles[style_id]
            right = after_styles[style_id]
            if left.paragraph_properties != right.paragraph_properties or left.run_properties != right.run_properties:
                diff.styles.append(
                    Change(
                        category="styles",
                        change_type="style_properties_changed",
                        source=style_id,
                        target=style_id,
                        before={"pPr": compact_props(left.paragraph_properties), "rPr": compact_props(left.run_properties)},
                        after={"pPr": compact_props(right.paragraph_properties), "rPr": compact_props(right.run_properties)},
                        classification="FORMAT",
                    )
                )

    def _compare_paragraphs(self, before: list[ParagraphInfo], after: list[ParagraphInfo], diff: DocumentDiff) -> None:
        for left, right in zip(before[:300], after[:300]):
            if left.style_id != right.style_id:
                diff.paragraph_changes.append(
                    Change("paragraph", "style_changed", left.id, right.id, left.style_id, right.style_id, "FORMAT")
                )
            left_props = compact_props(left.properties)
            right_props = compact_props(right.properties)
            if left_props != right_props:
                diff.paragraph_changes.append(
                    Change("paragraph", "properties_changed", left.id, right.id, left_props, right_props, "FORMAT")
                )
            if left.normalized_text != right.normalized_text:
                diff.paragraph_changes.append(
                    Change(
                        "paragraph",
                        "text_changed_at_same_index",
                        left.id,
                        right.id,
                        left.normalized_text[:300],
                        right.normalized_text[:300],
                        "CONTENT",
                    )
                )

    def _compare_tables(self, before: DocumentReport, after: DocumentReport, diff: DocumentDiff) -> None:
        for left, right in zip(before.tables, after.tables):
            before_shape = {"rows": left.row_count, "columns": left.logical_column_count, "merges": left.merge_cells}
            after_shape = {"rows": right.row_count, "columns": right.logical_column_count, "merges": right.merge_cells}
            if before_shape != after_shape:
                diff.table_changes.append(Change("table", "structure_changed", left.id, right.id, before_shape, after_shape, "CONTENT"))
            if left.tbl_properties != right.tbl_properties or left.grid != right.grid:
                diff.table_changes.append(
                    Change(
                        "table",
                        "format_or_width_changed",
                        left.id,
                        right.id,
                        {"grid": left.grid, "properties": left.tbl_properties},
                        {"grid": right.grid, "properties": right.tbl_properties},
                        "FORMAT",
                    )
                )
        self._append_count_change(diff.table_changes, "tables", len(before.tables), len(after.tables), "CONTENT")

    def _compare_drawings(self, before: DocumentReport, after: DocumentReport, diff: DocumentDiff) -> None:
        self._append_count_change(diff.drawing_changes, "drawings", len(before.drawings), len(after.drawings), "CONTENT")
        for left, right in zip(before.drawings, after.drawings):
            if left.media_sha256 != right.media_sha256:
                diff.drawing_changes.append(Change("drawing", "media_changed", left.id, right.id, left.media_sha256, right.media_sha256, "CONTENT"))
            if left.size != right.size or left.inline != right.inline or left.anchor != right.anchor:
                diff.drawing_changes.append(
                    Change(
                        "drawing",
                        "placement_or_size_changed",
                        left.id,
                        right.id,
                        {"size": left.size, "inline": left.inline, "anchor": left.anchor},
                        {"size": right.size, "inline": right.inline, "anchor": right.anchor},
                        "FORMAT",
                    )
                )

    def _compare_formulas(self, before: DocumentReport, after: DocumentReport, diff: DocumentDiff) -> None:
        self._append_count_change(diff.formula_changes, "formulas", len(before.formulas), len(after.formulas), "CONTENT")
        for left, right in zip(before.formulas, after.formulas):
            if left.xml_hash != right.xml_hash:
                diff.formula_changes.append(Change("formula", "xml_changed", left.id, right.id, left.xml_hash, right.xml_hash, "CONTENT"))

    def _compare_stories(self, before: list[Any], after: list[Any], diff: DocumentDiff, kind: str) -> None:
        target = diff.header_changes if kind == "header" else diff.footer_changes
        self._append_count_change(target, f"{kind}s", len(before), len(after), "LAYOUT")
        for left, right in zip(before, after):
            if left.text != right.text or left.field_count != right.field_count or left.paragraph_border_count != right.paragraph_border_count:
                target.append(
                    Change(
                        category=kind,
                        change_type="story_changed",
                        source=left.part,
                        target=right.part,
                        before={"text": left.text[:300], "fields": left.field_count, "borders": left.paragraph_border_count},
                        after={"text": right.text[:300], "fields": right.field_count, "borders": right.paragraph_border_count},
                        classification="LAYOUT",
                    )
                )

    @staticmethod
    def _append_count_change(changes: list[Change], category: str, before: int, after: int, classification: str) -> None:
        if before != after:
            changes.append(
                Change(
                    category=category,
                    change_type="count_changed",
                    source=category,
                    target=category,
                    before=before,
                    after=after,
                    classification=classification,
                )
            )
