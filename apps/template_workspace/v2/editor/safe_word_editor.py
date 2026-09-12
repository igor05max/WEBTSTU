from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree

from apps.template_workspace.v2.classification.roles import RoleClassifierV2
from apps.template_workspace.v2.inspector.document import DocumentInspector, element_text, normalize_text
from apps.template_workspace.v2.models.document_info import DocumentReport, TableInfo
from apps.template_workspace.v2.models.template_profile import ArticleStructure, MappingPreview, TemplateProfile
from apps.template_workspace.v2.ooxml.namespaces import NS, local_name, qn
from apps.template_workspace.v2.profile.template import TemplateProfileBuilder
from apps.template_workspace.v2.planning.qwen_like import QwenLikePlanningEngine, PlanningResult


PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
HEADER_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/header"
FOOTER_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer"

# OOXML unit conversions.
EMU_PER_TWIP = 635

# Roles whose paragraphs are content objects, not free prose.  If a template does
# not provide the exact role, these are preserved rather than silently falling
# back to BODY.
FRONT_ROLES = {
    "editorial_metadata", "article_type", "rubric", "title", "author",
    "affiliation", "email", "abstract", "keywords", "citation",
}
HEADING_ROLES = {"heading_1", "heading_2", "heading_3", "references_heading",
                 "funding_heading", "acknowledgements_heading", "conflict_heading"}
TEXT_ROLES = {"body", "reference_item", "funding_text", "acknowledgements_text", "conflict_text"}
CAPTION_ROLES = {"figure_caption", "table_caption"}
BACK_ROLES = {
    "funding_heading", "funding_text", "acknowledgements_heading",
    "acknowledgements_text", "conflict_heading", "conflict_text",
    "references_heading", "reference_item", "author_information",
    "received_metadata", "copyright_metadata",
}

# WordprocessingML complex types are sequences, not unordered property bags.
# Lxml happily appends a new ``w:b`` after ``w:sz`` or ``w:spacing`` before
# ``w:keepNext``; desktop Word then repairs (or rejects) the otherwise readable
# package.  Keep the canonical ECMA-376 order for property containers touched by
# this editor.  Unknown extension children retain their relative order at the end.
_PROPERTY_CHILD_ORDER: dict[str, tuple[str, ...]] = {
    "pPr": (
        "pStyle", "keepNext", "keepLines", "pageBreakBefore", "framePr",
        "widowControl", "numPr", "suppressLineNumbers", "pBdr", "shd",
        "tabs", "suppressAutoHyphens", "kinsoku", "wordWrap",
        "overflowPunct", "topLinePunct", "autoSpaceDE", "autoSpaceDN",
        "bidi", "adjustRightInd", "snapToGrid", "spacing", "ind",
        "contextualSpacing", "mirrorIndents", "suppressOverlap", "jc",
        "textDirection", "textAlignment", "textboxTightWrap", "outlineLvl",
        "divId", "cnfStyle", "rPr", "sectPr", "pPrChange",
    ),
    "rPr": (
        "rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps",
        "smallCaps", "strike", "dstrike", "outline", "shadow", "emboss",
        "imprint", "noProof", "snapToGrid", "vanish", "webHidden",
        "color", "spacing", "w", "kern", "position", "sz", "szCs",
        "highlight", "u", "effect", "bdr", "shd", "fitText",
        "vertAlign", "rtl", "cs", "em", "lang", "eastAsianLayout",
        "specVanish", "oMath", "rPrChange",
    ),
    "sectPr": (
        "headerReference", "footerReference", "footnotePr", "endnotePr",
        "type", "pgSz", "pgMar", "paperSrc", "pgBorders", "lnNumType",
        "pgNumType", "cols", "formProt", "vAlign", "noEndnote",
        "titlePg", "textDirection", "bidi", "rtlGutter", "docGrid",
        "printerSettings", "sectPrChange",
    ),
    "tblPr": (
        "tblStyle", "tblpPr", "tblOverlap", "bidiVisual",
        "tblStyleRowBandSize", "tblStyleColBandSize", "tblW", "jc",
        "tblCellSpacing", "tblInd", "tblBorders", "shd", "tblLayout",
        "tblCellMar", "tblLook", "tblCaption", "tblDescription",
        "tblPrChange",
    ),
    "trPr": (
        "cnfStyle", "divId", "gridBefore", "gridAfter", "wBefore",
        "wAfter", "cantSplit", "trHeight", "tblHeader", "tblCellSpacing",
        "jc", "hidden", "ins", "del", "trPrChange",
    ),
    "tcPr": (
        "cnfStyle", "tcW", "gridSpan", "hMerge", "vMerge", "tcBorders",
        "shd", "noWrap", "tcMar", "textDirection", "tcFitText", "vAlign",
        "hideMark", "headers", "cellIns", "cellDel", "cellMerge",
        "tcPrChange",
    ),
    "tblBorders": (
        "top", "left", "bottom", "right", "insideH", "insideV",
        "tl2br", "tr2bl",
    ),
    "tcBorders": (
        "top", "left", "bottom", "right", "insideH", "insideV",
        "tl2br", "tr2bl", "start", "end",
    ),
    "pBdr": ("top", "left", "bottom", "right", "between", "bar"),
    "tblCellMar": ("top", "left", "bottom", "right", "start", "end"),
    "settings": (
        "writeProtection", "view", "zoom", "removePersonalInformation",
        "removeDateAndTime", "doNotDisplayPageBoundaries",
        "displayBackgroundShape", "printPostScriptOverText",
        "printFractionalCharacterWidth", "printFormsData",
        "embedTrueTypeFonts", "embedSystemFonts", "saveSubsetFonts",
        "saveFormsData", "mirrorMargins", "alignBordersAndEdges",
        "bordersDoNotSurroundHeader", "bordersDoNotSurroundFooter",
        "gutterAtTop", "hideSpellingErrors", "hideGrammaticalErrors",
        "activeWritingStyle", "proofState", "formsDesign",
        "attachedTemplate", "linkStyles", "stylePaneFormatFilter",
        "stylePaneSortMethod", "documentType", "mailMerge", "revisionView",
        "trackRevisions", "doNotTrackMoves", "doNotTrackFormatting",
        "documentProtection", "autoFormatOverride", "styleLockTheme",
        "styleLockQFSet", "defaultTabStop", "autoHyphenation",
        "consecutiveHyphenLimit", "hyphenationZone", "doNotHyphenateCaps",
        "showEnvelope", "summaryLength", "clickAndTypeStyle",
        "defaultTableStyle", "evenAndOddHeaders", "bookFoldRevPrinting",
        "bookFoldPrinting", "bookFoldPrintingSheets",
        "drawingGridHorizontalSpacing", "drawingGridVerticalSpacing",
        "displayHorizontalDrawingGridEvery", "displayVerticalDrawingGridEvery",
        "doNotUseMarginsForDrawingGridOrigin", "drawingGridHorizontalOrigin",
        "drawingGridVerticalOrigin", "doNotShadeFormData",
        "noPunctuationKerning", "characterSpacingControl", "printTwoOnOne",
        "strictFirstAndLastChars", "noLineBreaksAfter", "noLineBreaksBefore",
        "savePreviewPicture", "doNotValidateAgainstSchema", "saveInvalidXml",
        "ignoreMixedContent", "alwaysShowPlaceholderText",
        "doNotDemarcateInvalidXml", "saveXmlDataOnly", "useXSLTWhenSaving",
        "saveThroughXslt", "showXmlTags", "alwaysMergeEmptyNamespace",
        "updateFields", "hdrShapeDefaults", "footnotePr", "endnotePr",
        "compat", "docVars", "rsids", "mathPr", "uiCompat97To2003",
        "attachedSchema", "themeFontLang", "clrSchemeMapping",
        "doNotIncludeSubdocsInStats", "doNotAutoCompressPictures",
        "forceUpgrade", "captions", "readModeInkLockDown", "smartTagType",
        "shapeDefaults", "doNotEmbedSmartTags", "decimalSymbol",
        "listSeparator", "docId", "discardImageEditingData",
        "defaultImageDpi", "conflictMode", "chartTrackingRefBased",
        "persistentDocumentId",
    ),
}


@dataclass
class SafeWordEditorResult:
    output_path: str
    changes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_path": self.output_path,
            "changes": self.changes,
            "warnings": self.warnings,
            "metrics": self.metrics,
        }


@dataclass
class _NodeMeta:
    block_id: str | None
    role: str | None
    zone: str | None = None
    language: str | None = None
    group_id: str | None = None
    subtype: str | None = None
    confidence: float = 0.0
    needs_review: bool = False


@dataclass
class _LayoutSpec:
    printable_width_twips: int
    column_width_twips: int
    column_gap_twips: int
    front_section: etree._Element
    body_section: etree._Element
    full_section: etree._Element


class SafeWordEditor:
    """Word-first editor that formats *a copy* of ARTICLE from TEMPLATE evidence.

    The editor deliberately avoids rebuilding ARTICLE from an IR.  It changes the
    existing ``word/document.xml`` in place, preserves native tables/drawings/
    formulas/relationships, applies explicit effective formatting, and only makes
    controlled FLOW changes that are supported by the TEMPLATE profile:

    * canonical front-matter order (including language order),
    * one-column front matter -> two-column body,
    * temporary one-column spans for demonstrably wide native objects,
    * journal header/footer shell from TEMPLATE (with article-specific footer name).

    No network/LLM is required.  ``RoleClassifierV2`` is deterministic by default.
    """

    def __init__(self, classifier: RoleClassifierV2 | None = None, planner: QwenLikePlanningEngine | None = None):
        self.classifier = classifier or RoleClassifierV2(use_ai=False)
        self.planner = planner or QwenLikePlanningEngine()

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
        copy_template_headers: bool = True,
    ) -> SafeWordEditorResult:
        article_path = Path(article_path)
        template_path = Path(template_path)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        article_report = article_report or DocumentInspector(article_path).inspect()
        template_report = template_report or DocumentInspector(template_path).inspect()
        article_structure = article_structure or self.classifier.article_structure(article_report)
        template_profile = template_profile or TemplateProfileBuilder(self.classifier).build(template_report)
        planning = self.planner.plan(
            article_report=article_report,
            template_report=template_report,
            article_structure=article_structure,
            template_profile=template_profile,
        )

        decisions = {item["id"]: item for item in article_structure.blocks}
        table_info = {table.id: table for table in article_report.tables}

        warnings: list[str] = list(planning.warnings)
        metrics: dict[str, Any] = {
            "formatted_paragraphs": 0,
            "front_blocks_reordered": 0,
            "front_paragraphs_merged": 0,
            "front_shells_installed": 0,
            "title_breaks_inserted": 0,
            "heading_numbers_materialized": 0,
            "reference_numbers_materialized": 0,
            "journal_symbols_installed": 0,
            "captions_normalized": 0,
            "subfigure_labels_normalized": 0,
            "figure_container_metadata_normalized": 0,
            "flow_paragraphs_relocated": 0,
            "compact_tables_floated": 0,
            "compact_tables_kept_together": 0,
            "section_markers_inserted": 0,
            "terminal_columns_balanced": False,
            "table_rows_guarded": 0,
            "wide_object_spans": 0,
            "tables_resized": 0,
            "drawings_resized": 0,
            "headers_footers_copied": False,
            "even_odd_headers_enabled": False,
            "unsafe_body_fallbacks": 0,
            "planning_provider": planning.provider,
            "placeholder_slots_reserved": 0,
            "template_placeholders_inserted": 0,
            "template_placeholder_fields": [],
            "front_spacing_adjustments": 0,
            "large_figure_page_breaks": 0,
            "atomic_figure_rows_guarded": 0,
            "inline_figure_captions_guarded": 0,
        }

        with ZipFile(article_path) as article_zip, ZipFile(template_path) as template_zip:
            document_root = etree.fromstring(article_zip.read("word/document.xml"))
            body = document_root.find("w:body", namespaces=NS)
            if body is None:
                raise ValueError("ARTICLE.docx has no word:body")

            # Attach semantic metadata to the original top-level nodes before any
            # reordering.  The metadata lives only in Python; we do not pollute OOXML.
            meta_by_node = _metadata_for_body(body, decisions)

            # Canonicalise only the *front-matter flow*.  Native body tables/images
            # remain the original OOXML nodes.
            front_stats = _normalise_front_matter(body, meta_by_node, template_profile)
            metrics.update(front_stats)

            # The first journal rubric in the reference files is not an ordinary
            # paragraph: it is a small VML text-box shell.  Reusing only that
            # *layout shell* (with ARTICLE text substituted) is both safer and much
            # closer to Word's native journal layout than approximating it with two
            # normal paragraphs.
            metrics["front_shells_installed"] += _install_template_front_shell(
                body=body,
                meta_by_node=meta_by_node,
                template_zip=template_zip,
            )

            # Editorial identifiers that are absent from ARTICLE cannot be
            # silently invented.  Reuse the corresponding TEMPLATE field as an
            # unmistakable yellow placeholder so an editor can replace it before
            # publication.  Real ARTICLE values always win.
            placeholder_fields = _install_missing_editorial_placeholders(
                body=body,
                meta_by_node=meta_by_node,
                template_zip=template_zip,
            )
            metrics["template_placeholder_fields"] = placeholder_fields
            metrics["template_placeholders_inserted"] += len(placeholder_fields)

            template_hints = _template_render_hints(template_zip, template_report, self.classifier)

            # Formatting is applied from effective role profiles directly, rather
            # than copying the TEMPLATE styles.xml wholesale.  That prevents source
            # table/style IDs from becoming dangling or changing meaning.
            for child in list(body):
                if local_name(child) != "p":
                    continue
                meta = meta_by_node.get(child)
                role = meta.role if meta else None
                if not normalize_text(element_text(child)):
                    continue
                profile = template_profile.roles.get(role or "")
                if profile is None:
                    # Safety: do not convert an unknown semantic role to BODY.
                    continue
                _apply_role_format(child, role or "body", profile.typical_paragraph_formatting, profile.typical_run_formatting)
                metrics["formatted_paragraphs"] += 1

                if role == "rubric":
                    metrics["captions_normalized"] += _normalise_rubric_translation(child, template_hints["rubric_has_cyrillic"])
                elif role == "keywords" and template_hints["keywords_use_semicolon"]:
                    metrics["captions_normalized"] += _normalise_keywords_delimiter(child)
                elif role == "title" and template_hints["title_uses_manual_breaks"]:
                    metrics["title_breaks_inserted"] += _balance_title_lines(child)
                elif role == "figure_caption" and template_hints["figure_caption_prefix"]:
                    metrics["captions_normalized"] += _normalise_figure_caption_prefix(
                        child, template_hints["figure_caption_prefix"]
                    )

            metrics["front_spacing_adjustments"] += _apply_front_matter_spacing(
                body=body,
                meta_by_node=meta_by_node,
                gap_twips=int(template_hints["front_role_gap_twips"]),
            )

            # Convert Word list numbering on semantic headings into visible text.
            # This removes the large list-tab gap that otherwise appears after we
            # centre a heading while retaining its manuscript ``w:numPr``.
            metrics["heading_numbers_materialized"] += _materialise_heading_numbering(
                body=body,
                meta_by_node=meta_by_node,
                report=article_report,
            )
            metrics["reference_numbers_materialized"] += _materialise_reference_numbering(
                body=body,
                meta_by_node=meta_by_node,
            )

            # Copy only the reusable glyph decoration pattern (the envelope symbol)
            # from TEMPLATE.  No template author/email text is copied.
            metrics["journal_symbols_installed"] += _install_corresponding_author_symbols(
                body=body,
                meta_by_node=meta_by_node,
                template_zip=template_zip,
            )

            # Preserve the TEMPLATE's front-matter *geometry* when ARTICLE still
            # contains an editorial citation placeholder.  This is a structure-plan
            # decision (the future Qwen planner can override it), not fabricated text.
            metrics["placeholder_slots_reserved"] += _reserve_placeholder_front_slots(
                body=body,
                meta_by_node=meta_by_node,
                planning=planning,
            )

            # Figure-container labels such as ``a`` / ``b`` are layout metadata.
            # The journal convention is ``(a)`` / ``(b)`` in italics.
            metrics["subfigure_labels_normalized"] += _normalise_subfigure_labels(body, table_info, meta_by_node)
            metrics["figure_container_metadata_normalized"] += _normalise_figure_container_metadata(
                body, table_info, meta_by_node
            )

            # Large multi-panel figures in the journal are frequently placed after
            # one short look-ahead prose paragraph so the preceding two-column area
            # is filled instead of leaving a large blank.  Move the *existing* XML
            # node; never reconstruct its contents.
            metrics["flow_paragraphs_relocated"] += _optimise_large_figure_flow_v2(
                body=body,
                meta_by_node=meta_by_node,
                table_info=table_info,
                planning=planning,
            )
            metrics["compact_tables_floated"] += _float_compact_tables_forward(
                body=body,
                meta_by_node=meta_by_node,
                table_info=table_info,
                planning=planning,
            )

            _add_front_language_separators(body, meta_by_node)

            layout = _layout_spec(template_path, template_profile)
            _remove_existing_inline_section_breaks(body)
            metrics["section_markers_inserted"] += _install_front_body_sections(body, meta_by_node, layout)

            wide_spans = _wide_object_spans(body, meta_by_node, table_info, layout)
            metrics["section_markers_inserted"] += _install_wide_section_spans(body, wide_spans, layout)
            metrics["large_figure_page_breaks"] += _apply_planned_wide_figure_breaks(
                body=body, spans=wide_spans, meta_by_node=meta_by_node, table_info=table_info, planning=planning
            )
            metrics["atomic_figure_rows_guarded"] += _guard_planned_figure_containers(
                body=body, meta_by_node=meta_by_node, table_info=table_info, planning=planning
            )
            metrics["inline_figure_captions_guarded"] += _guard_inline_figure_captions(
                body=body,
                meta_by_node=meta_by_node,
            )
            metrics["wide_object_spans"] = len(wide_spans)
            metrics["terminal_columns_balanced"] = _balance_terminal_two_column_section(
                body=body,
                meta_by_node=meta_by_node,
                layout=layout,
            )

            # Resize the original native tables and drawings to the actual column or
            # printable width.  Their XML is not reconstructed.
            full_width_nodes = {node for span in wide_spans for node in span}
            for child in list(body):
                if local_name(child) == "tbl":
                    meta = meta_by_node.get(child)
                    info = table_info.get(meta.block_id) if meta and meta.block_id else None
                    target = layout.printable_width_twips if child in full_width_nodes else layout.column_width_twips
                    if _resize_table_to_width(child, target, info):
                        metrics["tables_resized"] += 1
                        if info is not None and info.classification != "FIGURE_CONTAINER":
                            metrics["table_rows_guarded"] += _guard_table_rows(child)
                            if _keep_compact_table_together(child, info):
                                metrics["compact_tables_kept_together"] += 1
                elif local_name(child) == "p":
                    target = layout.printable_width_twips if child in full_width_nodes else layout.column_width_twips
                    metrics["drawings_resized"] += _resize_top_level_drawings(child, target)

            replacements: dict[str, bytes] = {"word/document.xml": _serialize_xml(document_root)}
            if copy_template_headers:
                author_short = _article_author_shortline(article_structure, article_report)
                story_replacements = _merge_template_header_footer(
                    article_zip=article_zip,
                    template_zip=template_zip,
                    document_root=document_root,
                    author_shortline=author_short,
                )
                replacements.update(story_replacements)
                # document.xml may have relationship IDs rewritten by the merge.
                replacements["word/document.xml"] = _serialize_xml(document_root)
                metrics["headers_footers_copied"] = bool(story_replacements)

                settings_payload, even_odd = _merge_template_document_settings(article_zip, template_zip)
                if settings_payload is not None:
                    replacements["word/settings.xml"] = settings_payload
                metrics["even_odd_headers_enabled"] = even_odd

            _write_package(article_zip, output_path, replacements)

        changes = [
            f"Applied deterministic role formatting to {metrics['formatted_paragraphs']} ARTICLE paragraphs.",
            f"Reordered {metrics['front_blocks_reordered']} front-matter blocks and merged {metrics['front_paragraphs_merged']} compatible front paragraphs.",
            f"Installed {metrics['front_shells_installed']} native TEMPLATE front-matter layout shell(s), {metrics['journal_symbols_installed']} journal glyph decoration(s), and {metrics['title_breaks_inserted']} balanced title line-break set(s).",
            f"Materialized numbering on {metrics['heading_numbers_materialized']} semantic headings and normalized {metrics['captions_normalized']} caption/metadata conventions plus {metrics['subfigure_labels_normalized']} subfigure labels and {metrics['figure_container_metadata_normalized']} figure-container metadata runs.",
            f"Relocated {metrics['flow_paragraphs_relocated']} intact prose paragraph(s) around large multi-panel figures to improve two-column balance.",
            f"Qwen-like planner: {planning.provider}; reserved {metrics['placeholder_slots_reserved']} placeholder front slots, floated {metrics['compact_tables_floated']} compact table(s), and forced {metrics['large_figure_page_breaks']} large-figure page starts.",
            f"Inserted {metrics['template_placeholders_inserted']} missing editorial field placeholder(s) from TEMPLATE with yellow highlighting and normalized {metrics['front_spacing_adjustments']} front-matter role gap(s).",
            f"Kept {metrics['inline_figure_captions_guarded']} inline figure/caption group(s) together across columns and pages.",
            f"Kept {metrics['compact_tables_kept_together']} compact data table(s) together instead of splitting them across columns/pages.",
            f"Created {metrics['wide_object_spans']} temporary full-width spans for wide ARTICLE objects.",
            f"Resized {metrics['tables_resized']} native tables and {metrics['drawings_resized']} native drawings without recreating them.",
            "Preserved ARTICLE tables, formulas, media, hyperlinks, numbering and document relationships by default.",
            "Did not copy TEMPLATE styles.xml/numbering.xml/theme wholesale; formatting is explicit and role-scoped.",
        ]
        if copy_template_headers:
            changes.append("Copied the TEMPLATE journal header/footer shell and replaced footer author text with ARTICLE authors; TEMPLATE page numbers were not copied.")
        else:
            changes.append("Kept ARTICLE headers/footers.")
        if not template_profile.front_language_order:
            warnings.append("TEMPLATE language order was not confidently detected; ARTICLE front-matter group order was preserved.")
        if metrics["template_placeholder_fields"]:
            fields = ", ".join(metrics["template_placeholder_fields"])
            warnings.append(
                f"Replace the yellow TEMPLATE-derived editorial placeholder(s) before publication: {fields}."
            )
        if mapping_preview and mapping_preview.summary.get("unsafe_body_fallback_count", 0):
            warnings.append("MappingPreview reports unsafe BODY fallbacks. SafeWordEditor ignored those fallbacks and only used exact/synthetic role profiles.")

        return SafeWordEditorResult(str(output_path), changes=changes, warnings=warnings, metrics=metrics)


# ---------------------------------------------------------------------------
# Front matter
# ---------------------------------------------------------------------------


def _metadata_for_body(body: etree._Element, decisions: dict[str, dict[str, Any]]) -> dict[etree._Element, _NodeMeta]:
    result: dict[etree._Element, _NodeMeta] = {}
    for index, child in enumerate(list(body), start=1):
        block_id = f"block_{index:04d}" if local_name(child) in {"p", "tbl"} else None
        d = decisions.get(block_id or "", {})
        result[child] = _NodeMeta(
            block_id=block_id,
            role=d.get("detected_role"),
            zone=d.get("zone"),
            language=d.get("language"),
            group_id=d.get("group_id"),
            subtype=d.get("subtype"),
            confidence=float(d.get("confidence") or 0.0),
            needs_review=bool(d.get("needs_review")),
        )
    return result


def _normalise_front_matter(body: etree._Element, meta_by_node: dict[etree._Element, _NodeMeta], profile: TemplateProfile) -> dict[str, int]:
    children = list(body)
    body_start = None
    for i, node in enumerate(children):
        meta = meta_by_node.get(node)
        if meta and meta.zone == "body":
            body_start = i
            break
    if body_start is None:
        return {"front_blocks_reordered": 0, "front_paragraphs_merged": 0}

    front_nodes = children[:body_start]
    # Only operate on front paragraphs; a front table/object is preserved in place.
    front_paragraphs = [n for n in front_nodes if local_name(n) == "p"]
    if not front_paragraphs:
        return {"front_blocks_reordered": 0, "front_paragraphs_merged": 0}

    metadata: list[etree._Element] = []
    groups: dict[str, list[etree._Element]] = {}
    loose: list[etree._Element] = []
    for node in front_paragraphs:
        text = normalize_text(element_text(node))
        meta = meta_by_node.get(node)
        if not text:
            continue
        if meta and meta.group_id:
            groups.setdefault(meta.group_id, []).append(node)
        elif meta and meta.role in {"editorial_metadata", "article_type", "rubric"}:
            metadata.append(node)
        else:
            loose.append(node)

    canonical: list[etree._Element] = []
    merged = 0

    # Journal metadata order is: article type/rubric -> bibliographic IDs.  Labels
    # such as "Рубрика журнала:" are UI-like metadata and need not occupy a line.
    article_type = [n for n in metadata if (meta_by_node[n].role == "article_type")]
    rubric = [n for n in metadata if (meta_by_node[n].role == "rubric")]
    bibliographic = [n for n in metadata if meta_by_node[n].subtype == "bibliographic_id"]
    other_metadata = [
        n for n in metadata
        if n not in article_type and n not in rubric and n not in bibliographic and meta_by_node[n].subtype != "rubric_label"
    ]
    for node in article_type:
        _normalise_article_type_text(node)
        canonical.append(node)
    canonical.extend(rubric)
    canonical.extend(other_metadata)
    canonical.extend(bibliographic)

    # Determine language order from TEMPLATE.  If not available, keep source group
    # order.  group language is inferred by the title, not by email/citation text.
    group_order = list(groups)
    group_lang: dict[str, str | None] = {}
    for group_id, nodes in groups.items():
        title_node = next((n for n in nodes if meta_by_node[n].role == "title"), None)
        group_lang[group_id] = meta_by_node[title_node].language if title_node is not None else None
    if profile.front_language_order:
        rank = {lang: i for i, lang in enumerate(profile.front_language_order)}
        group_order.sort(key=lambda gid: (rank.get(group_lang.get(gid) or "", 999), list(groups).index(gid)))

    role_sequence = profile.front_role_sequence or ["title", "author", "affiliation", "email", "abstract", "keywords", "citation"]
    for gi, group_id in enumerate(group_order):
        nodes = groups[group_id]
        by_role: dict[str, list[etree._Element]] = {}
        for node in nodes:
            role = meta_by_node[node].role or "unknown"
            by_role.setdefault(role, []).append(node)
        for role in role_sequence:
            role_nodes = by_role.pop(role, [])
            if not role_nodes:
                continue
            if role in {"affiliation", "abstract"} and len(role_nodes) > 1:
                combined = _merge_front_text_paragraphs(role_nodes, role, meta_by_node)
                canonical.append(combined)
                merged += len(role_nodes) - 1
            else:
                canonical.extend(role_nodes)
        # Keep any unusual front role rather than dropping content.
        for role_nodes in by_role.values():
            canonical.extend(role_nodes)
        if gi != len(group_order) - 1:
            spacer = _blank_paragraph()
            meta_by_node[spacer] = _NodeMeta(None, "paragraph", zone="front_matter")
            canonical.append(spacer)

    canonical.extend(loose)

    # Remove all original *front paragraph* nodes including excess blank separators,
    # then insert the canonical list immediately before the first body node.  Front
    # native objects (rare, but possible) are left untouched.
    first_body_node = children[body_start]
    old_nonblank = [n for n in front_paragraphs if normalize_text(element_text(n))]
    for node in front_paragraphs:
        if node.getparent() is body:
            body.remove(node)
    insert_at = body.index(first_body_node)
    for offset, node in enumerate(canonical):
        if node.getparent() is not None and node.getparent() is not body:
            node.getparent().remove(node)
        body.insert(insert_at + offset, node)

    # Reordering metric is intentionally conservative: count only nonblank front
    # elements whose relative position changed.
    old_ids = list(old_nonblank)
    new_ids = [n for n in canonical if normalize_text(element_text(n)) and n in old_ids]
    reordered = sum(1 for i, node in enumerate(new_ids) if i >= len(old_ids) or old_ids[i] is not node)
    return {"front_blocks_reordered": reordered, "front_paragraphs_merged": merged}


def _normalise_article_type_text(paragraph: etree._Element) -> None:
    text = normalize_text(element_text(paragraph))
    lowered = text.casefold()
    if "оригиналь" in lowered and re.search(r"тип статьи", lowered):
        _set_plain_text(paragraph, "Original papers")
    elif re.search(r"article\s*type", lowered) and "original" in lowered:
        _set_plain_text(paragraph, "Original papers")


def _install_template_front_shell(
    *,
    body: etree._Element,
    meta_by_node: dict[etree._Element, _NodeMeta],
    template_zip: ZipFile,
) -> int:
    """Reuse TEMPLATE's first rubric/article-type VML shell with ARTICLE text.

    Some journal files put the article type + rubric in a floating VML text box.
    Reconstructing that with normal paragraphs changes the first-page geometry by
    several lines.  The shell contains no article-specific relationships, so it is
    safe to clone while replacing *all* visible text with ARTICLE content.
    """

    article_type_nodes = [
        n for n in list(body)
        if local_name(n) == "p" and meta_by_node.get(n) and meta_by_node[n].role == "article_type"
        and normalize_text(element_text(n))
    ]
    rubric_nodes = [
        n for n in list(body)
        if local_name(n) == "p" and meta_by_node.get(n) and meta_by_node[n].role == "rubric"
        and normalize_text(element_text(n))
    ]
    if not article_type_nodes or not rubric_nodes:
        return 0

    template_root = etree.fromstring(template_zip.read("word/document.xml"))
    template_body = template_root.find("w:body", namespaces=NS)
    if template_body is None:
        return 0
    shell = next(
        (
            _clone(p)
            for p in template_body.findall("w:p", namespaces=NS)
            if len(p.xpath(".//w:txbxContent/w:p[normalize-space(string(.)) != '']", namespaces=NS)) >= 2
        ),
        None,
    )
    if shell is None:
        return 0
    inner = shell.xpath(".//w:txbxContent/w:p", namespaces=NS)
    text_inner = [p for p in inner if normalize_text(element_text(p))]
    if len(text_inner) < 2:
        return 0

    article_type_text = normalize_text(element_text(article_type_nodes[0]))
    rubric_text = normalize_text(element_text(rubric_nodes[0]))
    template_rubric = normalize_text(element_text(text_inner[1]))
    if not _contains_cyrillic(template_rubric):
        rubric_text = _strip_trailing_translation(rubric_text)

    _set_plain_text_preserve_ppr(text_inner[0], article_type_text)
    _set_plain_text_preserve_ppr(text_inner[1], rubric_text)
    # Preserve bold/italic shell styling if the source helper happened to clone a
    # run whose rPr is sparse.
    first_type_run = text_inner[0].find("w:r", namespaces=NS)
    if first_type_run is not None:
        _set_run_bool(first_type_run, "b", True)
    first_rubric_run = text_inner[1].find("w:r", namespaces=NS)
    if first_rubric_run is not None:
        _set_run_bool(first_rubric_run, "b", True)
        _set_run_bool(first_rubric_run, "i", True)

    ordered = list(body)
    source_nodes = article_type_nodes + rubric_nodes
    insert_at = min(ordered.index(n) for n in source_nodes)
    for node in source_nodes:
        if node.getparent() is body:
            body.remove(node)
        meta_by_node.pop(node, None)
    body.insert(insert_at, shell)
    meta_by_node[shell] = _NodeMeta(None, "front_shell", zone="front_matter", confidence=1.0)

    # The TEMPLATE places a real blank paragraph after the floating VML rubric
    # shell.  Without this spacer the next ARTICLE paragraph (typically UDK) is
    # laid under the absolutely-positioned textbox and becomes visually hidden.
    spacer = _blank_paragraph()
    body.insert(insert_at + 1, spacer)
    meta_by_node[spacer] = _NodeMeta(None, "paragraph", zone="front_matter", confidence=1.0)
    return 1


def _install_missing_editorial_placeholders(
    *,
    body: etree._Element,
    meta_by_node: dict[etree._Element, _NodeMeta],
    template_zip: ZipFile,
) -> list[str]:
    """Copy missing UDC/DOI fields from TEMPLATE as yellow placeholders.

    These are deliberately limited to the journal's bibliographic identifier row.
    Author names, titles, citation text, dates and other article content are never
    candidates.  A real ARTICLE identifier is preserved and suppresses its matching
    placeholder.
    """

    template_root = etree.fromstring(template_zip.read("word/document.xml"))
    template_body = template_root.find("w:body", namespaces=NS)
    if template_body is None:
        return []

    template_paragraph = next(
        (
            p for p in template_body.findall("w:p", namespaces=NS)
            if re.search(r"(?:DOI\s*:|\b(?:УДК|UDC)\b)", normalize_text(element_text(p)), re.I)
        ),
        None,
    )
    if template_paragraph is None:
        return []

    template_text = normalize_text(element_text(template_paragraph))
    template_fields = _extract_editorial_fields(template_text)
    if not template_fields:
        return []

    article_metadata = [
        p for p in body.findall("w:p", namespaces=NS)
        if (meta_by_node.get(p) and meta_by_node[p].role == "editorial_metadata")
    ]
    article_metadata_text = " ".join(normalize_text(element_text(p)) for p in article_metadata)
    missing = [
        name for name in ("udc", "doi")
        if name in template_fields and not _has_editorial_field(article_metadata_text, name)
    ]
    if not missing:
        return []

    target = next(
        (
            p for p in article_metadata
            if _has_editorial_field(normalize_text(element_text(p)), "udc")
            or _has_editorial_field(normalize_text(element_text(p)), "doi")
        ),
        None,
    )
    if target is None:
        target = etree.Element(qn("w:p"))
        anchor = next(
            (
                node for node in list(body)
                if meta_by_node.get(node)
                and meta_by_node[node].role in {"title", "author", "affiliation", "email", "abstract", "keywords", "citation"}
            ),
            None,
        )
        body.insert(body.index(anchor) if anchor is not None else 0, target)
        meta_by_node[target] = _NodeMeta(
            None,
            "editorial_metadata",
            zone="front_matter",
            subtype="bibliographic_id",
            confidence=1.0,
        )

    _copy_editorial_tabs(target, template_paragraph)
    target_has_text = bool(normalize_text(element_text(target)))
    inserted: list[str] = []

    # UDC belongs on the left.  When it is missing but DOI is already present,
    # prepend it without rewriting the ARTICLE DOI runs.
    if "udc" in missing:
        _prepend_highlighted_field(target, template_fields["udc"], add_tab=target_has_text)
        target_has_text = True
        inserted.append("UDC")

    # DOI belongs at the right tab stop copied from TEMPLATE.
    if "doi" in missing:
        _append_highlighted_field(target, template_fields["doi"], add_tab=target_has_text)
        inserted.append("DOI")

    return inserted


def _extract_editorial_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    udc = re.search(r"(?:\bУДК\b|\bUDC\b)\s*:?\s*.*?(?=\s*DOI\s*:|$)", text, re.I)
    # A Word tab is not textual content, so concatenated OOXML text can look like
    # ``620.3DOI: ...``.  Do not require a word boundary before DOI.
    doi = re.search(r"DOI\s*:\s*10\.\d{4,9}/[^\s]+", text, re.I)
    if udc and udc.group(0).strip():
        fields["udc"] = udc.group(0).strip()
    if doi and doi.group(0).strip():
        fields["doi"] = doi.group(0).strip()
    return fields


def _has_editorial_field(text: str, name: str) -> bool:
    if name == "udc":
        return bool(re.search(r"(?:\bУДК\b|\bUDC\b)", text, re.I))
    if name == "doi":
        return bool(re.search(r"DOI\s*:", text, re.I))
    return False


def _copy_editorial_tabs(target: etree._Element, template: etree._Element) -> None:
    source_ppr = template.find("w:pPr", namespaces=NS)
    source_tabs = source_ppr.find("w:tabs", namespaces=NS) if source_ppr is not None else None
    if source_tabs is None:
        return
    target_ppr = target.find("w:pPr", namespaces=NS)
    if target_ppr is None:
        target_ppr = etree.Element(qn("w:pPr")); target.insert(0, target_ppr)
    old_tabs = target_ppr.find("w:tabs", namespaces=NS)
    if old_tabs is None:
        target_ppr.append(_clone(source_tabs))
    else:
        target_ppr.replace(old_tabs, _clone(source_tabs))


def _highlighted_field_run(text: str) -> etree._Element:
    run = etree.Element(qn("w:r"))
    rpr = etree.SubElement(run, qn("w:rPr"))
    highlight = etree.SubElement(rpr, qn("w:highlight"))
    highlight.set(qn("w:val"), "yellow")
    node = etree.SubElement(run, qn("w:t"))
    node.text = text
    return run


def _tab_run() -> etree._Element:
    run = etree.Element(qn("w:r"))
    etree.SubElement(run, qn("w:tab"))
    return run


def _append_highlighted_field(paragraph: etree._Element, text: str, *, add_tab: bool) -> None:
    if add_tab:
        paragraph.append(_tab_run())
    paragraph.append(_highlighted_field_run(text))


def _prepend_highlighted_field(paragraph: etree._Element, text: str, *, add_tab: bool) -> None:
    ppr = paragraph.find("w:pPr", namespaces=NS)
    insert_at = 1 if ppr is not None else 0
    paragraph.insert(insert_at, _highlighted_field_run(text))
    if add_tab:
        paragraph.insert(insert_at + 1, _tab_run())


def _template_render_hints(
    template_zip: ZipFile,
    template_report: DocumentReport,
    classifier: RoleClassifierV2,
) -> dict[str, Any]:
    structure = classifier.article_structure(template_report)
    by_id = {item["id"]: item for item in structure.blocks}
    root = etree.fromstring(template_zip.read("word/document.xml"))
    body = root.find("w:body", namespaces=NS)
    children = list(body) if body is not None else []

    title_uses_breaks = False
    rubric_texts: list[str] = []
    keyword_texts: list[str] = []
    caption_prefix = None
    front_gap_candidates: list[int] = []
    for index, child in enumerate(children, start=1):
        bid = f"block_{index:04d}" if local_name(child) in {"p", "tbl"} else None
        role = by_id.get(bid or "", {}).get("detected_role")
        if role == "title" and child.xpath(".//w:br", namespaces=NS):
            title_uses_breaks = True
        elif role == "rubric":
            rubric_texts.append(normalize_text(element_text(child)))
        elif role == "keywords":
            keyword_texts.append(normalize_text(element_text(child)))
        elif role == "figure_caption" and caption_prefix is None:
            text = normalize_text(element_text(child))
            if re.match(r"^Fig\.\s*\d+", text, re.I):
                caption_prefix = "Fig."
            elif re.match(r"^Figure\s+\d+", text, re.I):
                caption_prefix = "Figure"

        if role in {"abstract", "keywords"}:
            child_index = children.index(child)
            next_index = child_index + 1
            if next_index < len(children) and local_name(children[next_index]) == "p" and not normalize_text(element_text(children[next_index])):
                front_gap_candidates.append(_blank_paragraph_line_twips(children[next_index], child))

    # Rubric may be inside a VML textbox and therefore absent from top-level
    # classifier output.  Inspect the shell text directly as a fallback.
    if not rubric_texts and body is not None:
        shell = next((p for p in body.findall("w:p", namespaces=NS) if p.xpath(".//w:txbxContent", namespaces=NS)), None)
        if shell is not None:
            inner = [normalize_text(element_text(p)) for p in shell.xpath(".//w:txbxContent/w:p", namespaces=NS)]
            rubric_texts.extend([x for x in inner[1:] if x])

    return {
        "title_uses_manual_breaks": title_uses_breaks,
        "rubric_has_cyrillic": any(_contains_cyrillic(x) for x in rubric_texts),
        "keywords_use_semicolon": any(";" in x for x in keyword_texts),
        "figure_caption_prefix": caption_prefix,
        "front_role_gap_twips": _median_int(front_gap_candidates, default=235),
    }


def _blank_paragraph_line_twips(blank: etree._Element, previous: etree._Element) -> int:
    for paragraph in (blank, previous):
        ppr = paragraph.find("w:pPr", namespaces=NS)
        spacing = ppr.find("w:spacing", namespaces=NS) if ppr is not None else None
        line = _safe_int(spacing.get(qn("w:line"))) if spacing is not None else 0
        if 160 <= line <= 400:
            return line
        rpr = ppr.find("w:rPr", namespaces=NS) if ppr is not None else None
        size = rpr.find("w:sz", namespaces=NS) if rpr is not None else None
        half_points = _safe_int(size.get(qn("w:val"))) if size is not None else 0
        if 12 <= half_points <= 32:
            return round(half_points * 11.5)
    return 235


def _median_int(values: list[int], *, default: int) -> int:
    if not values:
        return default
    ordered = sorted(values)
    return int(ordered[len(ordered) // 2])


def _normalise_rubric_translation(paragraph: etree._Element, template_has_cyrillic: bool) -> int:
    if template_has_cyrillic:
        return 0
    old = normalize_text(element_text(paragraph))
    new = _strip_trailing_translation(old)
    if new == old:
        return 0
    _replace_paragraph_text_preserve_rpr(paragraph, new)
    return 1


def _strip_trailing_translation(text: str) -> str:
    match = re.match(r"^(.*?)(?:\s*\(([^()]*)\))\s*$", text)
    if not match:
        return text
    base, parenthetical = match.group(1).strip(), match.group(2)
    if _contains_cyrillic(parenthetical) and not _contains_cyrillic(base):
        return base
    return text


def _contains_cyrillic(text: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", text or ""))


def _normalise_keywords_delimiter(paragraph: etree._Element) -> int:
    text = normalize_text(element_text(paragraph))
    if ":" not in text:
        return 0
    prefix, values = text.split(":", 1)
    if "," not in values:
        return 0
    # Keywords are a flat editorial list.  Do not touch commas before the marker
    # and keep terminal punctuation stable.
    values = re.sub(r",\s*", "; ", values.strip())
    new = f"{prefix}: {values}"
    _replace_paragraph_text_preserve_rpr(paragraph, new)
    _bold_prefix(paragraph, r"^(Keywords|Ключевые слова)\s*:")
    return 1


def _normalise_figure_caption_prefix(paragraph: etree._Element, preferred: str) -> int:
    text = normalize_text(element_text(paragraph))
    if preferred == "Fig." and re.match(r"^Figure\s+\d+", text, re.I):
        new = re.sub(r"^Figure\s+", "Fig. ", text, count=1, flags=re.I)
    elif preferred == "Figure" and re.match(r"^Fig\.\s*\d+", text, re.I):
        new = re.sub(r"^Fig\.\s*", "Figure ", text, count=1, flags=re.I)
    else:
        return 0
    _replace_paragraph_text_preserve_rpr(paragraph, new)
    _bold_prefix(paragraph, r"^(?:Fig(?:ure)?\.?)\s*\d+[A-Za-zА-Яа-я]?\s*[\.:]")
    return 1


def _balance_title_lines(paragraph: etree._Element) -> int:
    if paragraph.xpath(".//w:br", namespaces=NS):
        return 0
    text = normalize_text(element_text(paragraph))
    if len(text) < 65 or " " not in text:
        return 0
    line_count = 3 if len(text) >= 125 else 2
    lines = _balanced_word_lines(text, line_count)
    if len(lines) <= 1:
        return 0
    _replace_paragraph_with_lines_preserve_rpr(paragraph, lines)
    return 1


def _balanced_word_lines(text: str, line_count: int) -> list[str]:
    words = text.split()
    if len(words) < line_count * 2:
        return [text]
    total_chars = sum(len(w) for w in words) + len(words) - 1
    target = total_chars / line_count

    # Dynamic programming over word-boundary breaks.  Besides equal visual length,
    # mildly prefer a new line before short connective/prepositional words; that
    # approximates the editorial line-breaking visible in the journal templates.
    prefer_before = {"of", "and", "for", "in", "on", "with", "the", "на", "и", "в", "с", "для", "по", "из"}
    n = len(words)
    prefix = [0]
    for i, word in enumerate(words):
        prefix.append(prefix[-1] + len(word) + (1 if i else 0))

    from functools import lru_cache

    @lru_cache(None)
    def solve(start: int, remaining: int) -> tuple[float, tuple[int, ...]]:
        if remaining == 1:
            length = prefix[n] - prefix[start] - (1 if start else 0)
            return (length - target) ** 2, (n,)
        best = (float("inf"), ())
        min_end = start + 1
        max_end = n - (remaining - 1)
        for end in range(min_end, max_end + 1):
            length = prefix[end] - prefix[start] - (1 if start else 0)
            cost = (length - target) ** 2
            if end < n and words[end].casefold().strip(".,:;()") in prefer_before:
                cost *= 0.78
            rest_cost, rest = solve(end, remaining - 1)
            candidate = (cost + rest_cost, (end,) + rest)
            if candidate[0] < best[0]:
                best = candidate
        return best

    _, ends = solve(0, line_count)
    if not ends:
        return [text]
    result: list[str] = []
    start = 0
    for end in ends:
        result.append(" ".join(words[start:end]))
        start = end
    return [x for x in result if x]


def _replace_paragraph_with_lines_preserve_rpr(paragraph: etree._Element, lines: list[str]) -> None:
    ppr = paragraph.find("w:pPr", namespaces=NS)
    first_rpr = paragraph.find(".//w:r/w:rPr", namespaces=NS)
    for child in list(paragraph):
        if child is not ppr:
            paragraph.remove(child)
    run = etree.SubElement(paragraph, qn("w:r"))
    if first_rpr is not None:
        run.append(_clone(first_rpr))
    for i, line in enumerate(lines):
        if i:
            etree.SubElement(run, qn("w:br"))
        t = etree.SubElement(run, qn("w:t"))
        t.text = line


def _replace_paragraph_text_preserve_rpr(paragraph: etree._Element, text: str) -> None:
    ppr = paragraph.find("w:pPr", namespaces=NS)
    first_rpr = paragraph.find(".//w:r/w:rPr", namespaces=NS)
    for child in list(paragraph):
        if child is not ppr:
            paragraph.remove(child)
    run = etree.SubElement(paragraph, qn("w:r"))
    if first_rpr is not None:
        run.append(_clone(first_rpr))
    t = etree.SubElement(run, qn("w:t"))
    if text.startswith(" ") or text.endswith(" "):
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t.text = text


def _merge_front_text_paragraphs(nodes: list[etree._Element], role: str, meta_by_node: dict[etree._Element, _NodeMeta]) -> etree._Element:
    first = nodes[0]
    texts = [normalize_text(element_text(n)) for n in nodes if normalize_text(element_text(n))]
    if role == "abstract":
        # Convert a stand-alone marker + body paragraphs into the inline journal
        # form: "Abstract. ..." / "Аннотация. ...".
        marker = None
        rest = texts
        if texts and texts[0].casefold().strip(" .:") in {"abstract", "аннотация"}:
            marker = texts[0].strip(" .:")
            rest = texts[1:]
        if marker:
            text = marker + ". " + " ".join(rest)
        else:
            text = " ".join(texts)
    else:
        text = " ".join(texts)
    _set_plain_text(first, text)
    meta = meta_by_node[first]
    for node in nodes[1:]:
        if node.getparent() is not None:
            node.getparent().remove(node)
        meta_by_node.pop(node, None)
    meta_by_node[first] = _NodeMeta(meta.block_id, role, meta.zone, meta.language, meta.group_id, meta.subtype, max(meta.confidence, 0.94), False)
    return first


# ---------------------------------------------------------------------------
# Role formatting
# ---------------------------------------------------------------------------


def _apply_front_matter_spacing(
    *,
    body: etree._Element,
    meta_by_node: dict[etree._Element, _NodeMeta],
    gap_twips: int,
) -> int:
    """Recreate TEMPLATE's blank-line rhythm without adding content paragraphs."""

    gap = min(360, max(180, gap_twips))
    paragraphs = [
        p for p in body.findall("w:p", namespaces=NS)
        if normalize_text(element_text(p))
    ]
    changed = 0
    for index, paragraph in enumerate(paragraphs[:-1]):
        meta = meta_by_node.get(paragraph)
        if not meta or meta.role not in {"abstract", "keywords"} or not meta.group_id:
            continue
        following = paragraphs[index + 1]
        following_meta = meta_by_node.get(following)
        expected_next = "keywords" if meta.role == "abstract" else "citation"
        if not following_meta or following_meta.group_id != meta.group_id or following_meta.role != expected_next:
            continue
        ppr = paragraph.find("w:pPr", namespaces=NS)
        if ppr is None:
            ppr = etree.Element(qn("w:pPr")); paragraph.insert(0, ppr)
        spacing = ppr.find("w:spacing", namespaces=NS)
        if spacing is None:
            spacing = etree.SubElement(ppr, qn("w:spacing"))
        if _safe_int(spacing.get(qn("w:after"))) >= gap:
            continue
        spacing.set(qn("w:after"), str(gap))
        spacing.set(qn("w:afterAutospacing"), "0")
        changed += 1
    return changed


def _add_front_language_separators(body: etree._Element, meta_by_node: dict[etree._Element, _NodeMeta]) -> None:
    citations = [
        node for node in list(body)
        if local_name(node) == "p"
        and (meta_by_node.get(node) and meta_by_node[node].role == "citation" and meta_by_node[node].group_id)
    ]
    for paragraph in citations[:-1]:
        ppr = paragraph.find("w:pPr", namespaces=NS)
        if ppr is None:
            ppr = etree.Element(qn("w:pPr")); paragraph.insert(0, ppr)
        p_bdr = ppr.find("w:pBdr", namespaces=NS)
        if p_bdr is None:
            p_bdr = etree.SubElement(ppr, qn("w:pBdr"))
        bottom = p_bdr.find("w:bottom", namespaces=NS)
        if bottom is None:
            bottom = etree.SubElement(p_bdr, qn("w:bottom"))
        bottom.set(qn("w:val"), "single")
        bottom.set(qn("w:sz"), "4")
        bottom.set(qn("w:space"), "6")
        bottom.set(qn("w:color"), "808080")


def _apply_role_format(paragraph: etree._Element, role: str, paragraph_profile: dict[str, Any], run_profile: dict[str, Any]) -> None:
    _apply_paragraph_profile(paragraph, role, paragraph_profile)
    _apply_run_profile(paragraph, role, run_profile)
    if role == "abstract":
        _bold_prefix(paragraph, r"^(Abstract|Аннотация)\s*[\.:]?")
    elif role == "keywords":
        _bold_prefix(paragraph, r"^(Keywords|Ключевые слова)\s*:")
    elif role == "citation":
        _bold_prefix(paragraph, r"^(For citation|Для цитирования)\s*:")
    elif role == "figure_caption":
        _bold_prefix(paragraph, r"^(?:Fig(?:ure)?\.?|Рис(?:унок)?\.?)\s*\d+[A-Za-zА-Яа-я]?\s*[\.:]")
    elif role == "table_caption":
        _bold_prefix(paragraph, r"^(?:Table|Таблица)\s*\d+[A-Za-zА-Яа-я]?\s*[\.:]")


def _apply_paragraph_profile(paragraph: etree._Element, role: str, profile: dict[str, Any]) -> None:
    old = paragraph.find("w:pPr", namespaces=NS)
    keep: list[etree._Element] = []
    if old is not None:
        for child in old:
            if local_name(child) in {"numPr", "tabs", "sectPr", "bidi", "textDirection"}:
                keep.append(_clone(child))
    new = etree.Element(qn("w:pPr"))
    for child in keep:
        new.append(child)

    alignment = profile.get("alignment")
    if role in {"article_type", "rubric", "title", "author", "affiliation", "email", "heading_1", "heading_2", "heading_3", "figure_caption", "table_caption", "references_heading", "funding_heading", "acknowledgements_heading", "conflict_heading"}:
        alignment = "center"
    elif role in TEXT_ROLES | {"abstract", "keywords", "citation"}:
        alignment = alignment or "both"
    elif role == "editorial_metadata":
        # UDC/DOI line is left/justified in the journal shell.
        alignment = alignment or "left"
    if alignment:
        jc = etree.SubElement(new, qn("w:jc"))
        jc.set(qn("w:val"), str(alignment))

    spacing = dict(profile.get("spacing") or {})
    if role in TEXT_ROLES | {"abstract", "keywords", "citation", "figure_caption", "table_caption"} and not spacing:
        spacing = {qn("w:line"): "245", qn("w:lineRule"): "auto"}
    if spacing:
        el = etree.SubElement(new, qn("w:spacing"))
        _copy_attribute_dict(el, spacing)
        # Avoid source/template auto-spacing surprises.
        if qn("w:before") not in el.attrib:
            el.set(qn("w:before"), "0")
        if qn("w:after") not in el.attrib:
            el.set(qn("w:after"), "0")

    indentation = dict(profile.get("indentation") or {})
    if role in {"body", "reference_item", "funding_text", "acknowledgements_text", "conflict_text"} and not indentation:
        indentation = {qn("w:firstLine"): "426"}
    if role in FRONT_ROLES | HEADING_ROLES | CAPTION_ROLES:
        # Front/caption/heading profiles should not inherit the manuscript's large
        # first-line indent accidentally.
        indentation = {k: v for k, v in indentation.items() if _local_attr_name(k) not in {"firstLine", "hanging"}}
    if indentation:
        ind = etree.SubElement(new, qn("w:ind"))
        _copy_attribute_dict(ind, indentation)

    if role in HEADING_ROLES | CAPTION_ROLES:
        etree.SubElement(new, qn("w:keepNext"))
        etree.SubElement(new, qn("w:keepLines"))

    if old is None:
        paragraph.insert(0, new)
    else:
        paragraph.replace(old, new)


def _apply_run_profile(paragraph: etree._Element, role: str, profile: dict[str, Any]) -> None:
    defaults = _role_run_defaults(role, profile)
    for run in paragraph.xpath(".//w:r[not(ancestor::m:oMath) and not(ancestor::m:oMathPara)]", namespaces=NS):
        if run.xpath(".//w:drawing|.//w:pict|.//w:object|.//o:OLEObject", namespaces=NS):
            continue
        if not element_text(run):
            continue
        old = run.find("w:rPr", namespaces=NS)
        preserved = _preserve_semantic_run_properties(old, role)
        rpr = etree.Element(qn("w:rPr"))

        fonts = etree.SubElement(rpr, qn("w:rFonts"))
        font_name = defaults["font_name"]
        for key in ("ascii", "hAnsi", "cs", "eastAsia"):
            fonts.set(qn(f"w:{key}"), font_name)
        size = str(defaults["size"])
        sz = etree.SubElement(rpr, qn("w:sz")); sz.set(qn("w:val"), size)
        szcs = etree.SubElement(rpr, qn("w:szCs")); szcs.set(qn("w:val"), size)

        if defaults.get("bold") is True:
            etree.SubElement(rpr, qn("w:b"))
        elif defaults.get("bold") is False:
            b = etree.SubElement(rpr, qn("w:b")); b.set(qn("w:val"), "0")
        if defaults.get("italic") is True:
            etree.SubElement(rpr, qn("w:i"))
        elif defaults.get("italic") is False:
            i = etree.SubElement(rpr, qn("w:i")); i.set(qn("w:val"), "0")

        for child in preserved:
            _replace_child_by_local_name(rpr, child)
        if old is None:
            run.insert(0, rpr)
        else:
            run.replace(old, rpr)


def _role_run_defaults(role: str, profile: dict[str, Any]) -> dict[str, Any]:
    fonts = profile.get("fonts") or {}
    font_name = None
    for key, value in fonts.items():
        if _local_attr_name(key) in {"ascii", "hAnsi"} and value and str(value).casefold() != "calibri":
            font_name = str(value)
            break
    # The journal's default Normal style is Times New Roman.  Some effective
    # profiles expose only an eastAsia=Calibri override; do not let that replace
    # Latin/Cyrillic journal text.
    font_name = font_name or "Times New Roman"

    raw_size = profile.get("size") or profile.get("size_cs")
    try:
        size = int(raw_size) if raw_size else 22
    except (TypeError, ValueError):
        size = 22
    # Role-aware journal defaults, in half-points.
    if role == "title":
        size = max(size, 28)
    elif role in {"abstract", "keywords", "citation"}:
        size = 20
    elif role in {"reference_item", "table_caption", "figure_caption"}:
        # The supplied journal uses 10 pt for captions and references even when
        # only a 11/12 pt complex-script size is visible in the source profile.
        # Using that ``szCs`` value for Latin text made captions oversized and
        # stretched the bibliography by almost a full page.
        size = 20
    elif role in {"body", "heading_1", "heading_2", "heading_3", "funding_heading", "funding_text", "acknowledgements_heading", "acknowledgements_text", "conflict_heading", "conflict_text"}:
        size = 22

    bold: bool | None = None
    italic: bool | None = None
    if role in {"article_type", "title", "author", "heading_1", "references_heading", "funding_heading", "acknowledgements_heading", "conflict_heading"}:
        bold, italic = True, False
    elif role == "rubric":
        bold, italic = True, True
    elif role == "heading_2":
        bold, italic = True, True
    elif role == "heading_3":
        bold, italic = False, True
    elif role == "affiliation":
        bold, italic = False, True
    elif role in {"email", "abstract", "keywords", "citation", "body", "reference_item", "figure_caption", "table_caption", "funding_text", "acknowledgements_text", "conflict_text", "editorial_metadata", "article_type", "rubric"}:
        bold, italic = False, False
    return {"font_name": font_name, "size": size, "bold": bold, "italic": italic}


def _preserve_semantic_run_properties(old: etree._Element | None, role: str) -> list[etree._Element]:
    if old is None:
        return []
    preserve = {"vertAlign", "lang", "rtl", "strike", "dstrike", "caps", "smallCaps", "u", "color", "highlight"}
    # In flowing prose preserve author-supplied emphasis (variables, Latin names,
    # etc.).  In titles/headings/front roles typography is governed by TEMPLATE.
    if role in {"body", "reference_item"}:
        preserve |= {"b", "i"}
    result: list[etree._Element] = []
    for child in old:
        if local_name(child) in preserve:
            result.append(_clone(child))
    return result


def _bold_prefix(paragraph: etree._Element, pattern: str) -> None:
    text = "".join(t.text or "" for t in paragraph.xpath(".//w:t", namespaces=NS))
    match = re.match(pattern, text, flags=re.IGNORECASE)
    if not match:
        return
    end = match.end()
    cursor = 0
    for run in list(paragraph.xpath(".//w:r[not(ancestor::m:oMath) and not(ancestor::m:oMathPara)]", namespaces=NS)):
        ts = run.xpath("./w:t", namespaces=NS)
        if len(ts) != 1:
            cursor += len(element_text(run))
            continue
        node = ts[0]
        value = node.text or ""
        run_start, run_end = cursor, cursor + len(value)
        if run_end <= end:
            _set_run_bool(run, "b", True)
        elif run_start < end < run_end:
            cut = end - run_start
            before, after = value[:cut], value[cut:]
            node.text = before
            _set_run_bool(run, "b", True)
            clone = _clone(run)
            clone_t = clone.find("w:t", namespaces=NS)
            clone_t.text = after
            if after.startswith(" ") or after.endswith(" "):
                clone_t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            _set_run_bool(clone, "b", False)
            run.addnext(clone)
        cursor = run_end
        if cursor >= end:
            break


def _set_run_bool(run: etree._Element, name: str, value: bool) -> None:
    rpr = run.find("w:rPr", namespaces=NS)
    if rpr is None:
        rpr = etree.Element(qn("w:rPr")); run.insert(0, rpr)
    old = rpr.find(f"w:{name}", namespaces=NS)
    if old is None:
        old = etree.SubElement(rpr, qn(f"w:{name}"))
    if value:
        old.attrib.pop(qn("w:val"), None)
    else:
        old.set(qn("w:val"), "0")


def _materialise_heading_numbering(
    *,
    body: etree._Element,
    meta_by_node: dict[etree._Element, _NodeMeta],
    report: DocumentReport,
) -> int:
    """Turn source Word-list numbering into inline text for centred headings.

    Word list indents and centred journal headings interact badly: the list label is
    laid out at the old list tab while the heading text is centred.  For semantic
    headings only, resolve the list label once, prepend it to the existing text, and
    remove ``w:numPr``.  Other numbered content remains completely untouched.
    """

    num_to_abstract = {
        str(item.get("numId")): str(item.get("abstractNumId"))
        for item in report.numbering.nums
        if item.get("numId") is not None and item.get("abstractNumId") is not None
    }
    abstract_levels: dict[str, dict[int, dict[str, Any]]] = {}
    for item in report.numbering.abstract_nums:
        aid = str(item.get("abstractNumId"))
        levels: dict[int, dict[str, Any]] = {}
        for level in item.get("levels") or []:
            try:
                levels[int(level.get("ilvl") or 0)] = level
            except (TypeError, ValueError):
                continue
        abstract_levels[aid] = levels

    paragraph_by_id = {p.id: p for p in report.paragraphs}
    node_by_id = {
        meta.block_id: node
        for node, meta in meta_by_node.items()
        if meta.block_id and local_name(node) == "p"
    }
    role_by_id = {
        meta.block_id: meta.role
        for node, meta in meta_by_node.items()
        if meta.block_id and local_name(node) == "p"
    }
    counters: dict[str, dict[int, int]] = {}
    changed = 0

    # Simulate numbering in original ARTICLE paragraph order, not reordered output.
    for paragraph in report.paragraphs:
        numbering = paragraph.numbering or {}
        num_id = str(numbering.get("numId") or "")
        if not num_id:
            continue
        try:
            ilvl = int(numbering.get("ilvl") or 0)
        except (TypeError, ValueError):
            ilvl = 0
        levels = abstract_levels.get(num_to_abstract.get(num_id, ""), {})
        level = levels.get(ilvl)
        if not level:
            continue

        state = counters.setdefault(num_id, {})
        if ilvl not in state:
            state[ilvl] = _safe_int(level.get("start")) or 1
        else:
            state[ilvl] += 1
        # A higher-level increment restarts deeper levels at their declared starts.
        for deeper in [x for x in state if x > ilvl]:
            state.pop(deeper, None)

        role = role_by_id.get(paragraph.id)
        if role not in {"heading_1", "heading_2", "heading_3"}:
            continue
        node = node_by_id.get(paragraph.id)
        if node is None:
            continue
        text = normalize_text(element_text(node))
        if re.match(r"^\s*\d+(?:\.\d+){0,2}\.?(?:\s|\u00a0)", text):
            _remove_numpr(node)
            continue

        template = str(level.get("level_text") or "")
        if not template:
            continue
        values: dict[int, int] = {}
        for lev in range(0, ilvl + 1):
            lev_info = levels.get(lev) or {}
            values[lev] = state.get(lev, _safe_int(lev_info.get("start")) or 1)
        label = template
        for lev, value in values.items():
            label = label.replace(f"%{lev + 1}", str(value))
        label = re.sub(r"%\d+", "", label).strip()
        if not label:
            continue
        _prepend_text_run(node, label + "\u00a0")
        _remove_numpr(node)
        changed += 1
    return changed


def _prepend_text_run(paragraph: etree._Element, text: str) -> None:
    first_run = paragraph.find("w:r", namespaces=NS)
    run = etree.Element(qn("w:r"))
    if first_run is not None:
        rpr = first_run.find("w:rPr", namespaces=NS)
        if rpr is not None:
            run.append(_clone(rpr))
    t = etree.SubElement(run, qn("w:t"))
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t.text = text
    ppr = paragraph.find("w:pPr", namespaces=NS)
    insert_at = 1 if ppr is not None and len(paragraph) and paragraph[0] is ppr else 0
    paragraph.insert(insert_at, run)


def _remove_numpr(paragraph: etree._Element) -> None:
    ppr = paragraph.find("w:pPr", namespaces=NS)
    if ppr is None:
        return
    numpr = ppr.find("w:numPr", namespaces=NS)
    if numpr is not None:
        ppr.remove(numpr)


def _materialise_reference_numbering(
    *,
    body: etree._Element,
    meta_by_node: dict[etree._Element, _NodeMeta],
) -> int:
    """Make bibliography numbers ordinary text and remove list-tab geometry.

    Narrow journal columns amplify Word list hanging indents/tabs into huge word
    gaps.  References are already semantically identified, so a stable visible
    ``N. `` prefix is safer than retaining manuscript list machinery.  Existing
    explicit numbering is respected and never duplicated.
    """

    changed = 0
    sequence = 0
    for paragraph in list(body):
        if local_name(paragraph) != "p":
            continue
        meta = meta_by_node.get(paragraph)
        if not meta or meta.role != "reference_item":
            continue
        text = normalize_text(element_text(paragraph))
        if not text:
            continue
        sequence += 1
        if not re.match(r"^\s*\d+[.)]\s+", text):
            _prepend_text_run(paragraph, f"{sequence}.\u00a0")
            changed += 1
        _remove_numpr(paragraph)

        ppr = paragraph.find("w:pPr", namespaces=NS)
        if ppr is None:
            ppr = etree.Element(qn("w:pPr")); paragraph.insert(0, ppr)
        # Remove manuscript list tabs/hanging indents but keep journal first-line
        # indentation decisions made by the role formatter.
        tabs = ppr.find("w:tabs", namespaces=NS)
        if tabs is not None:
            ppr.remove(tabs)
        ind = ppr.find("w:ind", namespaces=NS)
        if ind is not None:
            ind.attrib.pop(qn("w:hanging"), None)
            ind.attrib.pop(qn("w:left"), None)
        jc = ppr.find("w:jc", namespaces=NS)
        if jc is None:
            jc = etree.SubElement(ppr, qn("w:jc"))
        jc.set(qn("w:val"), "both")
    return changed


def _install_corresponding_author_symbols(
    *,
    body: etree._Element,
    meta_by_node: dict[etree._Element, _NodeMeta],
    template_zip: ZipFile,
) -> int:
    symbol_run = _template_envelope_symbol_run(template_zip)
    if symbol_run is None:
        return 0
    changed = 0
    for node in list(body):
        if local_name(node) != "p":
            continue
        meta = meta_by_node.get(node)
        if not meta:
            continue
        if meta.role == "author":
            # The source manuscript uses '*' to mark the corresponding author.
            # Replace precisely that marker with the journal's Wingdings envelope.
            for run in node.xpath("./w:r", namespaces=NS):
                texts = run.xpath("./w:t", namespaces=NS)
                if not texts:
                    continue
                value = "".join(t.text or "" for t in texts)
                if "*" not in value:
                    continue
                # These source files isolate '*' in its own superscript run, but the
                # branch below remains safe when it is embedded in adjacent text.
                new_value = value.replace("*", "", 1)
                if len(texts) == 1:
                    texts[0].text = new_value
                else:
                    texts[0].text = new_value
                    for extra in texts[1:]:
                        extra.text = ""
                run.addnext(_clone(symbol_run))
                changed += 1
                break
        elif meta.role == "email":
            text = normalize_text(element_text(node))
            if not text or node.xpath(".//w:sym", namespaces=NS):
                continue
            # Remove only the editorial corresponding-author marker *in place*.
            # Do not rebuild the paragraph: e-mail addresses are often wrapped in
            # ``w:hyperlink`` and flattening them would destroy the relationship.
            if text.startswith("*"):
                _remove_leading_text_marker(node, "*")
            first_run = node.find("w:r", namespaces=NS)
            if first_run is None:
                first_run = node.find(".//w:r", namespaces=NS)
            sym = _clone(symbol_run)
            # ``w:r`` and ``w:hyperlink`` are both legal paragraph children.  Put
            # the icon at paragraph level before the first content child instead
            # of inserting it inside an existing hyperlink.
            ppr = node.find("w:pPr", namespaces=NS)
            content_children = [c for c in list(node) if c is not ppr]
            insert_at = node.index(content_children[0]) if content_children else len(node)
            node.insert(insert_at, sym)
            # Add a narrow non-breaking space after the icon.
            spacer = etree.Element(qn("w:r"))
            first_rpr = first_run.find("w:rPr", namespaces=NS) if first_run is not None else None
            if first_rpr is not None:
                spacer.append(_clone(first_rpr))
            t = etree.SubElement(spacer, qn("w:t"))
            t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            t.text = "\u00a0"
            sym.addnext(spacer)
            changed += 1
    return changed


def _remove_leading_text_marker(paragraph: etree._Element, marker: str) -> bool:
    for text_node in paragraph.xpath(".//w:t", namespaces=NS):
        value = text_node.text or ""
        stripped = value.lstrip()
        if not stripped:
            continue
        if stripped.startswith(marker):
            prefix_len = len(value) - len(stripped)
            remainder = stripped[len(marker):].lstrip()
            text_node.text = value[:prefix_len] + remainder
            if (text_node.text or "").startswith(" ") or (text_node.text or "").endswith(" "):
                text_node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            return True
        return False
    return False


def _template_envelope_symbol_run(template_zip: ZipFile) -> etree._Element | None:
    root = etree.fromstring(template_zip.read("word/document.xml"))
    for run in root.xpath("//w:r[w:sym]", namespaces=NS):
        sym = run.find("w:sym", namespaces=NS)
        if sym is None:
            continue
        font = (sym.get(qn("w:font")) or "").casefold()
        char = (sym.get(qn("w:char")) or "").upper()
        if "wingdings" in font and char:
            return _clone(run)
    return None


def _normalise_subfigure_labels(
    body: etree._Element,
    table_info: dict[str, TableInfo],
    meta_by_node: dict[etree._Element, _NodeMeta],
) -> int:
    changed = 0
    for table in body.findall("w:tbl", namespaces=NS):
        meta = meta_by_node.get(table)
        info = table_info.get(meta.block_id) if meta and meta.block_id else None
        if info is None or info.classification != "FIGURE_CONTAINER":
            continue
        for p in table.xpath(".//w:tc/w:p", namespaces=NS):
            text = normalize_text(element_text(p))
            if not re.fullmatch(r"[A-Za-zА-Яа-я]", text):
                continue
            _replace_paragraph_text_preserve_rpr(p, f"({text})")
            ppr = p.find("w:pPr", namespaces=NS)
            if ppr is None:
                ppr = etree.Element(qn("w:pPr")); p.insert(0, ppr)
            jc = ppr.find("w:jc", namespaces=NS)
            if jc is None:
                jc = etree.SubElement(ppr, qn("w:jc"))
            jc.set(qn("w:val"), "center")
            for run in p.xpath("./w:r", namespaces=NS):
                rpr = run.find("w:rPr", namespaces=NS)
                if rpr is None:
                    rpr = etree.Element(qn("w:rPr")); run.insert(0, rpr)
                _set_run_bool(run, "i", True)
                for tag in ("sz", "szCs"):
                    el = rpr.find(f"w:{tag}", namespaces=NS)
                    if el is None:
                        el = etree.SubElement(rpr, qn(f"w:{tag}"))
                    el.set(qn("w:val"), "22")
            changed += 1
    return changed


def _normalise_figure_container_metadata(
    body: etree._Element,
    table_info: dict[str, TableInfo],
    meta_by_node: dict[etree._Element, _NodeMeta],
) -> int:
    """Remove non-content layout placeholders and compact figure-table labels."""

    changed = 0
    for table in body.findall("w:tbl", namespaces=NS):
        meta = meta_by_node.get(table)
        info = table_info.get(meta.block_id) if meta and meta.block_id else None
        if info is None or info.classification != "FIGURE_CONTAINER":
            continue
        for paragraph in table.xpath(".//w:tc/w:p", namespaces=NS):
            text = normalize_text(element_text(paragraph))
            has_object = bool(paragraph.xpath(".//w:drawing|.//w:pict|.//w:object|.//m:oMath", namespaces=NS))
            if text in {"/", "\\", "|"} and not has_object:
                _set_plain_text(paragraph, "")
                changed += 1
                continue
            if not text:
                continue
            ppr = paragraph.find("w:pPr", namespaces=NS)
            if ppr is None:
                ppr = etree.Element(qn("w:pPr")); paragraph.insert(0, ppr)
            spacing = ppr.find("w:spacing", namespaces=NS)
            if spacing is None:
                spacing = etree.SubElement(ppr, qn("w:spacing"))
            spacing.set(qn("w:before"), "0"); spacing.set(qn("w:after"), "0")
            spacing.set(qn("w:line"), "220"); spacing.set(qn("w:lineRule"), "auto")
            for run in paragraph.xpath(".//w:r[not(.//w:drawing) and not(.//w:pict) and not(ancestor::m:oMath)]", namespaces=NS):
                if not element_text(run):
                    continue
                rpr = run.find("w:rPr", namespaces=NS)
                if rpr is None:
                    rpr = etree.Element(qn("w:rPr")); run.insert(0, rpr)
                fonts = rpr.find("w:rFonts", namespaces=NS)
                if fonts is None:
                    fonts = etree.SubElement(rpr, qn("w:rFonts"))
                for key in ("ascii", "hAnsi", "cs", "eastAsia"):
                    fonts.set(qn(f"w:{key}"), "Times New Roman")
                for tag in ("sz", "szCs"):
                    size = rpr.find(f"w:{tag}", namespaces=NS)
                    if size is None:
                        size = etree.SubElement(rpr, qn(f"w:{tag}"))
                    size.set(qn("w:val"), "20")
                changed += 1
    return changed



def _optimise_large_figure_flow_v2(
    *,
    body: etree._Element,
    meta_by_node: dict[etree._Element, _NodeMeta],
    table_info: dict[str, TableInfo],
    planning: PlanningResult,
) -> int:
    """Safely float selected figure lead-ins without rewriting prose.

    For a large figure Word often has insufficient vertical space because several
    prose paragraphs immediately precede it.  For planned two-panel figures the
    explicit ``Fig. N presents...`` lead-in itself follows the figure, matching the
    reference journal's float convention.  For 3+ panel figures we retain the
    earlier conservative relocation of up to two preceding prose paragraphs.  All
    operations move intact native ``w:p`` nodes; text is never split or rewritten.
    """

    if not planning.flow.get("allow_safe_prose_relocation"):
        return 0
    moved = 0
    for table in list(body):
        if local_name(table) != "tbl":
            continue
        meta = meta_by_node.get(table)
        info = table_info.get(meta.block_id) if meta and meta.block_id else None
        if info is None or info.classification != "FIGURE_CONTAINER":
            continue
        drawing_count = len(table.xpath(".//w:drawing|.//w:pict", namespaces=NS))
        float_lead_ids = set(planning.flow.get("float_lead_after_figure_block_ids") or [])
        float_lead = bool(meta and meta.block_id in float_lead_ids)
        if drawing_count < 3 and not float_lead:
            continue
        children = list(body)
        try:
            ti = children.index(table)
        except ValueError:
            continue

        # Caption after the table.
        caption = None
        caption_end = None
        for j in range(ti + 1, min(len(children), ti + 8)):
            n = children[j]
            if local_name(n) == "p" and not normalize_text(element_text(n)):
                continue
            nm = meta_by_node.get(n)
            if local_name(n) == "p" and nm and nm.role == "figure_caption":
                caption = n; caption_end = n
                group = nm.group_id
                k = j + 1
                while group and k < len(children):
                    km = meta_by_node.get(children[k])
                    if local_name(children[k]) == "p" and km and km.role == "figure_caption" and km.group_id == group:
                        caption_end = children[k]; k += 1
                    else:
                        break
            break
        if caption is None or caption_end is None:
            continue
        cap_text = normalize_text(element_text(caption))
        m = re.match(r"^(?:Fig\.|Figure)\s*(\d+)", cap_text, re.I)
        figure_no = m.group(1) if m else None

        # Find explicit lead-in before the table (e.g. "Fig. 7 presents...").
        lead = None
        for j in range(ti - 1, max(-1, ti - 10), -1):
            n = children[j]
            if local_name(n) != "p":
                continue
            txt = normalize_text(element_text(n))
            if not txt:
                continue
            nm = meta_by_node.get(n)
            if not nm or nm.role != "body":
                break
            if figure_no and re.search(rf"\bFig(?:ure)?\.?\s*{re.escape(figure_no)}\b", txt, re.I):
                lead = n
                break
        if lead is None:
            continue

        if float_lead:
            if lead.getparent() is body:
                body.remove(lead)
            caption_end.addnext(lead)
            moved += 1
            continue

        # Up to two ordinary prose paragraphs immediately before the lead-in.
        children = list(body)
        li = children.index(lead)
        candidates: list[etree._Element] = []
        j = li - 1
        while j >= 0 and len(candidates) < 2:
            n = children[j]
            if local_name(n) == "p" and not normalize_text(element_text(n)):
                j -= 1; continue
            nm = meta_by_node.get(n)
            if local_name(n) == "p" and nm and nm.role == "body":
                txt = normalize_text(element_text(n))
                if 100 <= len(txt) <= int(planning.flow.get("max_float_chars") or 1800):
                    candidates.append(n); j -= 1; continue
            break
        if not candidates:
            continue
        # Preserve original order after the caption.
        candidates.reverse()
        anchor = caption_end
        for node in candidates:
            if node.getparent() is body:
                body.remove(node)
            anchor.addnext(node)
            anchor = node
            moved += 1
    return moved


def _optimise_large_figure_flow(
    *,
    body: etree._Element,
    meta_by_node: dict[etree._Element, _NodeMeta],
    table_info: dict[str, TableInfo],
) -> int:
    """Relocate one existing prose paragraph around a very large figure.

    The reference journal often pulls the first paragraph *after* a large
    three-panel figure ahead of that figure so the preceding two-column region is
    filled.  This is deliberately conservative: only FIGURE_CONTAINER tables with
    at least three drawings qualify, and we move a single ordinary BODY paragraph
    as a native ``w:p`` node.  Text/runs/relationships are never reconstructed.
    """

    moved = 0
    for table in list(body):
        if local_name(table) != "tbl":
            continue
        meta = meta_by_node.get(table)
        info = table_info.get(meta.block_id) if meta and meta.block_id else None
        if info is None or info.classification != "FIGURE_CONTAINER":
            continue
        drawing_count = len(table.xpath(".//w:drawing|.//w:pict", namespaces=NS))
        if drawing_count < 3:
            continue

        children = list(body)
        try:
            ti = children.index(table)
        except ValueError:
            continue

        # Require an ordinary prose lead-in immediately before the figure area.
        pre_meaningful: etree._Element | None = None
        for j in range(ti - 1, -1, -1):
            n = children[j]
            if local_name(n) == "p" and normalize_text(element_text(n)):
                pre_meaningful = n
                break
            if local_name(n) == "tbl":
                break
        pre_meta = meta_by_node.get(pre_meaningful) if pre_meaningful is not None else None
        if not pre_meta or pre_meta.role != "body":
            continue

        # Find the caption after the figure, including multi-paragraph captions.
        caption_start: etree._Element | None = None
        caption_end_index = ti
        j = ti + 1
        while j < len(children):
            n = children[j]
            if local_name(n) == "p" and not normalize_text(element_text(n)):
                j += 1
                continue
            nm = meta_by_node.get(n)
            if local_name(n) == "p" and nm and nm.role == "figure_caption":
                caption_start = n
                caption_end_index = j
                group = nm.group_id
                k = j + 1
                while k < len(children):
                    km = meta_by_node.get(children[k])
                    if local_name(children[k]) == "p" and km and km.role == "figure_caption" and group and km.group_id == group:
                        caption_end_index = k
                        k += 1
                        continue
                    break
            break
        if caption_start is None:
            continue

        # First meaningful paragraph after the complete caption group.
        candidate: etree._Element | None = None
        for k in range(caption_end_index + 1, len(children)):
            n = children[k]
            if local_name(n) == "p" and not normalize_text(element_text(n)):
                continue
            nm = meta_by_node.get(n)
            if local_name(n) == "p" and nm and nm.role == "body":
                candidate = n
            break
        if candidate is None:
            continue
        text = normalize_text(element_text(candidate))
        # Avoid pulling a tiny bridge sentence or an enormous paragraph around a
        # figure.  The range covers the journal's normal look-ahead prose blocks.
        if len(text) < 120 or len(text) > 1100:
            continue

        # Insert before the run of blank paragraphs that precedes the table so the
        # retained blank remains a clean separator: prose -> blank -> figure.
        insertion_anchor = table
        prev = table.getprevious()
        while prev is not None and local_name(prev) == "p" and not normalize_text(element_text(prev)):
            insertion_anchor = prev
            prev = prev.getprevious()
        parent = candidate.getparent()
        if parent is body:
            body.remove(candidate)
            insertion_anchor.addprevious(candidate)
            moved += 1
    return moved





def _float_compact_tables_forward(
    *,
    body: etree._Element,
    meta_by_node: dict[etree._Element, _NodeMeta],
    table_info: dict[str, TableInfo],
    planning: PlanningResult,
) -> int:
    """Float a small data table a few prose blocks upward like a journal editor.

    This is deliberately conservative: only compact data tables (not layout/figure
    containers), at most two body paragraphs are crossed, and the destination is
    immediately after a nearby figure/caption group.  Existing w:p/w:tbl nodes are
    moved intact, so formulas, hyperlinks and relationships remain untouched.
    """

    if not planning.flow.get("float_compact_tables_forward"):
        return 0
    max_body = int(planning.flow.get("max_float_body_blocks") or 2)
    max_chars = int(planning.flow.get("max_float_chars") or 1800)
    moved = 0
    # Snapshot candidates because we mutate body order.
    candidates = []
    children = list(body)
    for i, node in enumerate(children):
        if local_name(node) != "tbl":
            continue
        meta = meta_by_node.get(node)
        info = table_info.get(meta.block_id) if meta and meta.block_id else None
        if info is None or info.classification == "FIGURE_CONTAINER":
            continue
        if info.row_count > 6 or info.logical_column_count > 4:
            continue
        # Caption immediately before table (ignoring blanks).
        j = i - 1
        while j >= 0 and local_name(children[j]) == "p" and not normalize_text(element_text(children[j])):
            j -= 1
        if j < 0 or local_name(children[j]) != "p":
            continue
        cap = children[j]
        cm = meta_by_node.get(cap)
        if not cm or cm.role != "table_caption":
            continue
        candidates.append((cap, node))

    for caption, table in candidates:
        if caption.getparent() is not body or table.getparent() is not body:
            continue
        children = list(body)
        ci = children.index(caption)
        # Walk backwards over up to N body paragraphs/blanks looking for a figure
        # caption; this describes the common journal pattern Fig -> Table -> prose.
        body_blocks = 0
        chars = 0
        anchor = None
        k = ci - 1
        while k >= 0:
            n = children[k]
            txt = normalize_text(element_text(n)) if local_name(n) == "p" else ""
            nm = meta_by_node.get(n)
            if local_name(n) == "p" and not txt:
                k -= 1
                continue
            if local_name(n) == "p" and nm and nm.role == "body":
                body_blocks += 1; chars += len(txt)
                if body_blocks > max_body or chars > max_chars:
                    break
                k -= 1
                continue
            if local_name(n) == "p" and nm and nm.role == "figure_caption" and body_blocks >= 1:
                anchor = n
            break
        if anchor is None:
            continue

        # Include blank paragraphs directly between caption and table so we don't
        # leave a large hole at the old location.
        group = [caption]
        n = caption.getnext()
        while n is not None and n is not table:
            nxt = n.getnext()
            if local_name(n) == "p" and not normalize_text(element_text(n)):
                group.append(n)
            n = nxt
        group.append(table)
        # Preserve one clean separator after the destination anchor.
        insert_after = anchor
        while insert_after.getnext() is not None and local_name(insert_after.getnext()) == "p" and not normalize_text(element_text(insert_after.getnext())):
            insert_after = insert_after.getnext()
        for node in group:
            if node.getparent() is body:
                body.remove(node)
        pos = body.index(insert_after) + 1
        for offset, node in enumerate(group):
            body.insert(pos + offset, node)
        moved += 1
    return moved


def _reserve_placeholder_front_slots(
    *,
    body: etree._Element,
    meta_by_node: dict[etree._Element, _NodeMeta],
    planning: PlanningResult,
) -> int:
    """Reserve template-like vertical space without inventing missing metadata.

    Editorial manuscripts often contain ``For citation: provided by editors`` while
    the formatted journal template has a 2-3 line final citation.  If we collapse the
    placeholder to one line, the second language block/body jumps upward and every
    later page drifts.  We reserve only the *geometry* with paragraph after-spacing.
    """

    if not planning.front.get("reserve_placeholder_citation_slot"):
        return 0
    expected = int(planning.front.get("citation_expected_lines") or 0)
    if expected <= 1:
        return 0
    changed = 0
    for p in body.findall("w:p", namespaces=NS):
        meta = meta_by_node.get(p)
        if not meta or meta.role != "citation":
            continue
        text = normalize_text(element_text(p)).casefold()
        if not any(x in text for x in ("данные предоставляются редакцией", "provided by editorial", "provided by the editorial office", "to be provided by")):
            continue
        # One reserved line is roughly the journal's 230-245 twip automatic line.
        # Cap at three missing lines so unusual templates cannot create huge gaps.
        missing = min(2, max(1, expected - 2))
        ppr = p.find("w:pPr", namespaces=NS)
        if ppr is None:
            ppr = etree.Element(qn("w:pPr")); p.insert(0, ppr)
        spacing = ppr.find("w:spacing", namespaces=NS)
        if spacing is None:
            spacing = etree.SubElement(ppr, qn("w:spacing"))
        old_after = _safe_int(spacing.get(qn("w:after")))
        reserve = max(old_after, missing * 235)
        spacing.set(qn("w:after"), str(reserve))
        spacing.set(qn("w:afterAutospacing"), "0")
        changed += 1
    return changed


def _apply_planned_wide_figure_breaks(
    *,
    body: etree._Element,
    spans: list[list[etree._Element]],
    meta_by_node: dict[etree._Element, _NodeMeta],
    table_info: dict[str, TableInfo],
    planning: PlanningResult,
) -> int:
    """Start very large full-width figure bands cleanly, based on planner intent.

    The future Qwen planner can turn this policy off/on per document.  The page break
    is attached to the dedicated invisible section marker, never to caption text.
    """

    if not planning.flow.get("page_break_before_large_full_width_figures"):
        return 0
    planned = set(planning.flow.get("large_figure_block_ids") or [])
    changed = 0
    for span in spans:
        table = next((n for n in span if local_name(n) == "tbl"), None)
        if table is None:
            continue
        meta = meta_by_node.get(table)
        info = table_info.get(meta.block_id) if meta and meta.block_id else None
        if info is None or info.classification != "FIGURE_CONTAINER" or (planned and info.id not in planned):
            continue
        previous = span[0].getprevious()
        if previous is None or local_name(previous) != "p":
            continue
        ppr = previous.find("w:pPr", namespaces=NS)
        if ppr is None:
            ppr = etree.Element(qn("w:pPr")); previous.insert(0, ppr)
        if ppr.find("w:pageBreakBefore", namespaces=NS) is None:
            etree.SubElement(ppr, qn("w:pageBreakBefore"))
            changed += 1
    return changed


def _guard_planned_figure_containers(
    *,
    body: etree._Element,
    meta_by_node: dict[etree._Element, _NodeMeta],
    table_info: dict[str, TableInfo],
    planning: PlanningResult,
) -> int:
    """Prevent multi-panel figure rows/captions from visually tearing apart.

    This is intentionally limited to FIGURE_CONTAINER tables.  Data tables are
    allowed to paginate naturally.
    """

    if not planning.flow.get("keep_figure_containers_atomic"):
        return 0
    changed = 0
    for table in body.findall("w:tbl", namespaces=NS):
        meta = meta_by_node.get(table)
        info = table_info.get(meta.block_id) if meta and meta.block_id else None
        if info is None or info.classification != "FIGURE_CONTAINER":
            continue
        rows = table.findall("w:tr", namespaces=NS)
        for ri, row in enumerate(rows):
            trpr = row.find("w:trPr", namespaces=NS)
            if trpr is None:
                trpr = etree.Element(qn("w:trPr")); row.insert(0, trpr)
            if trpr.find("w:cantSplit", namespaces=NS) is None:
                etree.SubElement(trpr, qn("w:cantSplit")); changed += 1
            # Keep each row connected to the next one when the whole figure can fit.
            if ri < len(rows) - 1:
                for p in row.xpath("./w:tc/w:p", namespaces=NS):
                    ppr = p.find("w:pPr", namespaces=NS)
                    if ppr is None:
                        ppr = etree.Element(qn("w:pPr")); p.insert(0, ppr)
                    if ppr.find("w:keepNext", namespaces=NS) is None:
                        etree.SubElement(ppr, qn("w:keepNext")); changed += 1
    return changed


def _guard_inline_figure_captions(
    *,
    body: etree._Element,
    meta_by_node: dict[etree._Element, _NodeMeta],
) -> int:
    """Keep an inline drawing with the caption that immediately follows it.

    Word otherwise treats the drawing paragraph and caption as unrelated blocks.
    In a two-column section this can leave the figure at the bottom of one column
    and move its caption to the top of the next column.  We change only pagination
    properties; drawing content, size and relationships stay untouched.
    """

    changed = 0
    children = list(body)
    for index, paragraph in enumerate(children):
        if local_name(paragraph) != "p":
            continue
        if not paragraph.xpath(".//w:drawing|.//w:pict", namespaces=NS):
            continue

        caption_index = index + 1
        while caption_index < len(children):
            candidate = children[caption_index]
            if local_name(candidate) == "p" and not normalize_text(element_text(candidate)):
                caption_index += 1
                continue
            break
        if caption_index >= len(children):
            continue
        caption = children[caption_index]
        caption_meta = meta_by_node.get(caption)
        if local_name(caption) != "p" or not caption_meta or caption_meta.role != "figure_caption":
            continue

        drawing_ppr = paragraph.find("w:pPr", namespaces=NS)
        if drawing_ppr is None:
            drawing_ppr = etree.Element(qn("w:pPr")); paragraph.insert(0, drawing_ppr)
        if drawing_ppr.find("w:keepNext", namespaces=NS) is None:
            etree.SubElement(drawing_ppr, qn("w:keepNext"))
            changed += 1

        group_id = caption_meta.group_id
        group_index = caption_index
        while group_index < len(children):
            item = children[group_index]
            item_meta = meta_by_node.get(item)
            if local_name(item) != "p" or not item_meta or item_meta.role != "figure_caption":
                break
            if group_id and item_meta.group_id != group_id:
                break
            ppr = item.find("w:pPr", namespaces=NS)
            if ppr is None:
                ppr = etree.Element(qn("w:pPr")); item.insert(0, ppr)
            if ppr.find("w:keepLines", namespaces=NS) is None:
                etree.SubElement(ppr, qn("w:keepLines"))
                changed += 1
            next_index = group_index + 1
            if next_index < len(children):
                next_meta = meta_by_node.get(children[next_index])
                same_group = (
                    local_name(children[next_index]) == "p"
                    and next_meta is not None
                    and next_meta.role == "figure_caption"
                    and bool(group_id)
                    and next_meta.group_id == group_id
                )
                if same_group and ppr.find("w:keepNext", namespaces=NS) is None:
                    etree.SubElement(ppr, qn("w:keepNext"))
                    changed += 1
            group_index += 1
            if not group_id:
                break
    return changed

# ---------------------------------------------------------------------------
# Sections and object sizing
# ---------------------------------------------------------------------------


def _layout_spec(template_path: Path, profile: TemplateProfile) -> _LayoutSpec:
    sections = _section_properties(template_path)
    if not sections:
        raise ValueError("TEMPLATE.docx contains no section properties")
    base = _clone(sections[0])
    _strip_page_numbering(base)
    _set_section_columns(base, 1)
    _set_section_type(base, "continuous")

    body_template = next((_clone(s) for s in sections if _section_column_count(s) == max(2, profile.layout.default_body_column_count)), _clone(sections[-1]))
    _strip_story_refs_and_page_numbering(body_template)
    _set_section_columns(body_template, max(2, profile.layout.default_body_column_count))
    _set_section_type(body_template, "continuous")

    full = _clone(body_template)
    _set_section_columns(full, 1)
    _set_section_type(full, "continuous")

    pg = base.find("w:pgSz", namespaces=NS)
    mar = base.find("w:pgMar", namespaces=NS)
    width = int(pg.get(qn("w:w"), "11907")) if pg is not None else 11907
    left = int(mar.get(qn("w:left"), "1021")) if mar is not None else 1021
    right = int(mar.get(qn("w:right"), "1021")) if mar is not None else 1021
    printable = max(1000, width - left - right)
    cols = body_template.find("w:cols", namespaces=NS)
    gap = int(cols.get(qn("w:space"), "340")) if cols is not None else 340
    num = max(2, _section_column_count(body_template))
    column = int((printable - gap * (num - 1)) / num)
    return _LayoutSpec(printable, column, gap, base, body_template, full)


def _remove_existing_inline_section_breaks(body: etree._Element) -> None:
    # Keep the terminal body/sectPr for replacement later, remove legacy paragraph
    # sectPr so the V2 layout is deterministic.
    for ppr in body.xpath("./w:p/w:pPr", namespaces=NS):
        sect = ppr.find("w:sectPr", namespaces=NS)
        if sect is not None:
            ppr.remove(sect)


def _install_front_body_sections(body: etree._Element, meta_by_node: dict[etree._Element, _NodeMeta], layout: _LayoutSpec) -> int:
    first_body = next((n for n in list(body) if (meta_by_node.get(n) and meta_by_node[n].zone == "body")), None)
    inserted = 0
    if first_body is not None:
        marker = _section_marker_paragraph(layout.front_section)
        first_body.addprevious(marker)
        meta_by_node[marker] = _NodeMeta(None, "paragraph", zone="front_matter", confidence=1.0)
        inserted += 1
    terminal = body.find("w:sectPr", namespaces=NS)
    final = _clone(layout.body_section)
    # Terminal section inherits header/footer from previous section; do not copy
    # content-specific page start from TEMPLATE.
    if terminal is None:
        body.append(final)
    else:
        body.replace(terminal, final)
    return inserted


def _wide_object_spans(
    body: etree._Element,
    meta_by_node: dict[etree._Element, _NodeMeta],
    tables: dict[str, TableInfo],
    layout: _LayoutSpec,
) -> list[list[etree._Element]]:
    children = list(body)
    spans: list[list[etree._Element]] = []
    for i, node in enumerate(children):
        if local_name(node) != "tbl":
            continue
        meta = meta_by_node.get(node)
        info = tables.get(meta.block_id) if meta and meta.block_id else None
        if info is None or not _table_needs_full_width(node, info, layout):
            continue
        start, end = i, i
        if info.classification != "FIGURE_CONTAINER":
            # data-table caption normally precedes the table
            j = i - 1
            while j >= 0 and local_name(children[j]) == "p" and not normalize_text(element_text(children[j])):
                j -= 1
            if j >= 0:
                m = meta_by_node.get(children[j])
                if m and m.role == "table_caption":
                    start = j
        else:
            # figure captions normally follow the container; include all members of
            # a multi-paragraph caption group.
            j = i + 1
            while j < len(children) and local_name(children[j]) == "p" and not normalize_text(element_text(children[j])):
                j += 1
            if j < len(children):
                m = meta_by_node.get(children[j])
                if m and m.role == "figure_caption":
                    end = j
                    group = m.group_id
                    k = j + 1
                    while group and k < len(children):
                        mk = meta_by_node.get(children[k])
                        if mk and mk.role == "figure_caption" and mk.group_id == group:
                            end = k; k += 1
                        else:
                            break
        span = [n for n in children[start:end + 1] if local_name(n) != "sectPr"]
        if span:
            spans.append(span)
    return _dedupe_overlapping_spans(spans)


def _table_needs_full_width(table: etree._Element, info: TableInfo, layout: _LayoutSpec) -> bool:
    source_width = sum(_safe_int(value) for value in info.grid if value)
    drawing_count = len(table.xpath(".//w:drawing|.//w:pict", namespaces=NS))
    if info.logical_column_count >= 5:
        return True
    if info.classification == "FIGURE_CONTAINER":
        if drawing_count >= 3:
            return True
        # Fig.1 in the regression fixture is ~1.16 columns and can be safely scaled;
        # the 2-panel thermal-analysis figure is ~2 columns and should stay wide.
        return source_width > int(layout.column_width_twips * 1.55)
    return False


def _install_wide_section_spans(body: etree._Element, spans: list[list[etree._Element]], layout: _LayoutSpec) -> int:
    inserted = 0
    for span in spans:
        first, last = span[0], span[-1]
        # Never attach sectPr to visible text/captions.  Word starts the next
        # section at that paragraph's baseline and can make two independent text
        # flows overlap.  Dedicated near-zero-height marker paragraphs match the
        # journal reference and keep the content geometry clean.
        before = _section_marker_paragraph(layout.body_section)
        first.addprevious(before)
        inserted += 1

        after = _section_marker_paragraph(layout.full_section)
        last.addnext(after)
        inserted += 1
    return inserted


def _balance_terminal_two_column_section(
    *,
    body: etree._Element,
    meta_by_node: dict[etree._Element, _NodeMeta],
    layout: _LayoutSpec,
) -> bool:
    """Close a final bibliography two-column section to make Word balance it.

    A terminal multi-column section is intentionally left unbalanced by Word/LO;
    content simply fills the left column and stops.  A tiny final one-column
    section forces the preceding continuous section to balance across both
    columns, without inventing any article text.
    """

    last_semantic: _NodeMeta | None = None
    for node in reversed(list(body)):
        if local_name(node) != "p" or not normalize_text(element_text(node)):
            continue
        last_semantic = meta_by_node.get(node)
        break
    if not last_semantic or last_semantic.role != "reference_item":
        return False

    terminal = body.find("w:sectPr", namespaces=NS)
    if terminal is None or _section_column_count(terminal) < 2:
        return False
    marker = _section_marker_paragraph(layout.body_section)
    terminal.addprevious(marker)
    body.replace(terminal, _clone(layout.full_section))
    return True


def _resize_table_to_width(table: etree._Element, target_twips: int, info: TableInfo | None) -> bool:
    grid_cols = table.xpath("./w:tblGrid/w:gridCol", namespaces=NS)
    widths = [_safe_int(col.get(qn("w:w"))) for col in grid_cols]
    source = sum(w for w in widths if w > 0)
    if source <= 0:
        source = sum(_safe_int(v) for v in (info.grid if info else []) if v)
    if source <= 0:
        return False
    factor = target_twips / source
    # Avoid pathological enlargement of small intentionally narrow tables.
    factor = min(factor, 1.35)
    new_widths = [max(120, int(width * factor)) for width in widths]
    if info is not None and info.classification != "FIGURE_CONTAINER":
        new_widths = _rebalance_data_column_widths(new_widths, int(sum(new_widths)))
    new_total = int(sum(new_widths))
    for col, nw in zip(grid_cols, new_widths):
        col.set(qn("w:w"), str(nw))

    tblpr = table.find("w:tblPr", namespaces=NS)
    if tblpr is None:
        tblpr = etree.Element(qn("w:tblPr")); table.insert(0, tblpr)
    tblw = tblpr.find("w:tblW", namespaces=NS)
    if tblw is None:
        tblw = etree.SubElement(tblpr, qn("w:tblW"))
    tblw.set(qn("w:type"), "dxa"); tblw.set(qn("w:w"), str(new_total))
    jc = tblpr.find("w:jc", namespaces=NS)
    if jc is None:
        jc = etree.SubElement(tblpr, qn("w:jc"))
    jc.set(qn("w:val"), "center")
    layout = tblpr.find("w:tblLayout", namespaces=NS)
    if layout is None:
        layout = etree.SubElement(tblpr, qn("w:tblLayout"))
    layout.set(qn("w:type"), "fixed")

    # Scale explicit cell widths using the same factor, preserving spans/merges.
    for tcw in table.xpath(".//w:tcPr/w:tcW", namespaces=NS):
        if tcw.get(qn("w:type"), "dxa") == "dxa":
            old = _safe_int(tcw.get(qn("w:w")))
            if old > 0:
                tcw.set(qn("w:w"), str(max(120, int(old * factor))))

    if info is not None and info.classification != "FIGURE_CONTAINER":
        _apply_journal_data_table_style(table, info)

    _scale_drawings(table, factor=factor, max_width_twips=target_twips)
    return True


def _rebalance_data_column_widths(widths: list[int], total: int) -> list[int]:
    if not widths or total <= 0:
        return widths
    n = len(widths)
    if n == 4:
        min_width = int(total * 0.155)
    elif n == 3:
        min_width = int(total * 0.19)
    else:
        min_width = int(total * 0.075)
    result = list(widths)
    deficit = sum(max(0, min_width - w) for w in result)
    if deficit <= 0:
        return result
    donors = sorted(range(n), key=lambda i: result[i], reverse=True)
    for i in range(n):
        if result[i] < min_width:
            result[i] = min_width
    excess = sum(result) - total
    for i in donors:
        if excess <= 0:
            break
        can_give = max(0, result[i] - min_width)
        take = min(can_give, excess)
        result[i] -= take; excess -= take
    if excess > 0:
        # Last-resort proportional normalization; keeps the table inside the column.
        scale = total / sum(result)
        result = [max(120, int(w * scale)) for w in result]
    # Correct rounding on the widest column.
    delta = total - sum(result)
    if result:
        result[max(range(len(result)), key=lambda i: result[i])] += delta
    return result


def _apply_journal_data_table_style(table: etree._Element, info: TableInfo) -> None:
    tblpr = table.find("w:tblPr", namespaces=NS)
    if tblpr is None:
        tblpr = etree.Element(qn("w:tblPr")); table.insert(0, tblpr)
    borders = tblpr.find("w:tblBorders", namespaces=NS)
    if borders is None:
        borders = etree.SubElement(tblpr, qn("w:tblBorders"))
    for name in ("left", "right", "insideH", "insideV"):
        node = borders.find(f"w:{name}", namespaces=NS)
        if node is None:
            node = etree.SubElement(borders, qn(f"w:{name}"))
        node.set(qn("w:val"), "none"); node.set(qn("w:sz"), "0"); node.set(qn("w:space"), "0")
    for name in ("top", "bottom"):
        node = borders.find(f"w:{name}", namespaces=NS)
        if node is None:
            node = etree.SubElement(borders, qn(f"w:{name}"))
        node.set(qn("w:val"), "single"); node.set(qn("w:sz"), "12"); node.set(qn("w:space"), "0"); node.set(qn("w:color"), "auto")

    # Small, explicit cell margins are much closer to the journal tables than the
    # manuscript defaults and keep numeric columns readable without inflating the
    # overall width.
    cell_mar = tblpr.find("w:tblCellMar", namespaces=NS)
    if cell_mar is None:
        cell_mar = etree.SubElement(tblpr, qn("w:tblCellMar"))
    for side, width in (("top", 20), ("bottom", 20), ("left", 55), ("right", 55)):
        node = cell_mar.find(f"w:{side}", namespaces=NS)
        if node is None:
            node = etree.SubElement(cell_mar, qn(f"w:{side}"))
        node.set(qn("w:w"), str(width)); node.set(qn("w:type"), "dxa")

    # Thin rule below the first header row.  This mirrors the house style while
    # retaining all original cells/merges.
    first_row = table.find("w:tr", namespaces=NS)
    if first_row is not None:
        trpr = first_row.find("w:trPr", namespaces=NS)
        if trpr is None:
            trpr = etree.Element(qn("w:trPr")); first_row.insert(0, trpr)
        # Repeating headers help genuinely long tables, but on short tables a page
        # break right before the final row looks much worse if Word repeats a large
        # multi-line header on the next page.
        if info.row_count >= 7 and trpr.find("w:tblHeader", namespaces=NS) is None:
            etree.SubElement(trpr, qn("w:tblHeader"))
        for tcpr in first_row.xpath("./w:tc/w:tcPr", namespaces=NS):
            cb = tcpr.find("w:tcBorders", namespaces=NS)
            if cb is None:
                cb = etree.SubElement(tcpr, qn("w:tcBorders"))
            bottom = cb.find("w:bottom", namespaces=NS)
            if bottom is None:
                bottom = etree.SubElement(cb, qn("w:bottom"))
            bottom.set(qn("w:val"), "single"); bottom.set(qn("w:sz"), "8"); bottom.set(qn("w:space"), "0")

    # The house-style reference uses approximately 12 pt table text.  The previous
    # V2 pass compressed tables to 10–10.5 pt, making them visually unlike the
    # reference and causing wide data tables to occupy too little vertical space.
    font_size = 20
    line_height = 220
    for p in table.xpath(".//w:tc/w:p", namespaces=NS):
        ppr = p.find("w:pPr", namespaces=NS)
        if ppr is None:
            ppr = etree.Element(qn("w:pPr")); p.insert(0, ppr)
        spacing = ppr.find("w:spacing", namespaces=NS)
        if spacing is None:
            spacing = etree.SubElement(ppr, qn("w:spacing"))
        spacing.set(qn("w:before"), "0"); spacing.set(qn("w:after"), "0")
        spacing.set(qn("w:line"), str(line_height)); spacing.set(qn("w:lineRule"), "auto")
        ind = ppr.find("w:ind", namespaces=NS)
        if ind is not None:
            ind.attrib.pop(qn("w:firstLine"), None); ind.attrib.pop(qn("w:hanging"), None)
        for run in p.xpath(".//w:r[not(ancestor::m:oMath) and not(ancestor::m:oMathPara)]", namespaces=NS):
            if run.xpath(".//w:drawing|.//w:pict|.//w:object", namespaces=NS):
                continue
            rpr = run.find("w:rPr", namespaces=NS)
            if rpr is None:
                rpr = etree.Element(qn("w:rPr")); run.insert(0, rpr)
            fonts = rpr.find("w:rFonts", namespaces=NS)
            if fonts is None:
                fonts = etree.SubElement(rpr, qn("w:rFonts"))
            for key in ("ascii", "hAnsi", "cs", "eastAsia"):
                fonts.set(qn(f"w:{key}"), "Times New Roman")
            for tag in ("sz", "szCs"):
                el = rpr.find(f"w:{tag}", namespaces=NS)
                if el is None:
                    el = etree.SubElement(rpr, qn(f"w:{tag}"))
                el.set(qn("w:val"), str(font_size))


def _guard_table_rows(table: etree._Element) -> int:
    """Remove fixed-height traps and keep native table rows intact on a page.

    Source manuscripts frequently carry exact row heights suitable only for their
    original font/width.  After journal resizing those heights can clip/overlay
    text.  ``atLeast`` lets Word grow the row naturally; ``cantSplit`` prevents a
    single row from being torn across pages.  Cell content/merges are untouched.
    """

    changed = 0
    for row in table.findall("w:tr", namespaces=NS):
        trpr = row.find("w:trPr", namespaces=NS)
        if trpr is None:
            trpr = etree.Element(qn("w:trPr")); row.insert(0, trpr)
        for height in trpr.findall("w:trHeight", namespaces=NS):
            if height.get(qn("w:hRule")) != "atLeast":
                height.set(qn("w:hRule"), "atLeast")
                changed += 1
        if trpr.find("w:cantSplit", namespaces=NS) is None:
            etree.SubElement(trpr, qn("w:cantSplit"))
            changed += 1
        for tcpr in row.xpath("./w:tc/w:tcPr", namespaces=NS):
            # Fit-text is particularly dangerous after column resizing because it
            # can compress glyphs until they appear to overlap.
            fit = tcpr.find("w:tcFitText", namespaces=NS)
            if fit is not None:
                tcpr.remove(fit)
                changed += 1
    return changed


def _keep_compact_table_together(table: etree._Element, info: TableInfo) -> bool:
    """Prevent a short table from being split at a page or column boundary."""

    rows = table.findall("w:tr", namespaces=NS)
    if not 1 < len(rows) <= 6:
        return False
    if len(normalize_text(element_text(table))) > 1800:
        return False
    changed = False
    for row_index, row in enumerate(rows):
        paragraphs = row.xpath("./w:tc/w:p", namespaces=NS)
        for paragraph in paragraphs:
            ppr = paragraph.find("w:pPr", namespaces=NS)
            if ppr is None:
                ppr = etree.Element(qn("w:pPr")); paragraph.insert(0, ppr)
            if ppr.find("w:keepLines", namespaces=NS) is None:
                etree.SubElement(ppr, qn("w:keepLines"))
                changed = True
            if row_index < len(rows) - 1 and ppr.find("w:keepNext", namespaces=NS) is None:
                etree.SubElement(ppr, qn("w:keepNext"))
                changed = True
    return changed


def _resize_top_level_drawings(paragraph: etree._Element, target_twips: int) -> int:
    count = 0
    max_emu = int(target_twips * EMU_PER_TWIP * 0.98)
    for holder in paragraph.xpath(".//wp:inline|.//wp:anchor", namespaces=NS):
        extent = holder.find("wp:extent", namespaces=NS)
        if extent is None:
            continue
        cx = _safe_int(extent.get("cx")); cy = _safe_int(extent.get("cy"))
        if cx <= 0 or cy <= 0 or cx <= max_emu:
            continue
        factor = max_emu / cx
        _scale_drawing_holder(holder, factor)
        count += 1
    return count


def _scale_drawings(node: etree._Element, *, factor: float, max_width_twips: int) -> None:
    max_emu = int(max_width_twips * EMU_PER_TWIP * 0.98)
    for holder in node.xpath(".//wp:inline|.//wp:anchor", namespaces=NS):
        extent = holder.find("wp:extent", namespaces=NS)
        if extent is None:
            continue
        cx = _safe_int(extent.get("cx"))
        local_factor = factor
        if cx > 0 and cx * local_factor > max_emu:
            local_factor = max_emu / cx
        if abs(local_factor - 1.0) > 0.015:
            _scale_drawing_holder(holder, local_factor)


def _scale_drawing_holder(holder: etree._Element, factor: float) -> None:
    extent = holder.find("wp:extent", namespaces=NS)
    if extent is not None:
        for attr in ("cx", "cy"):
            value = _safe_int(extent.get(attr))
            if value > 0:
                extent.set(attr, str(max(1, int(value * factor))))
    # Keep DrawingML transform size in sync with wp:extent.
    for ext in holder.xpath(".//a:xfrm/a:ext", namespaces=NS):
        for attr in ("cx", "cy"):
            value = _safe_int(ext.get(attr))
            if value > 0:
                ext.set(attr, str(max(1, int(value * factor))))


# ---------------------------------------------------------------------------
# Header / footer package merge
# ---------------------------------------------------------------------------


def _merge_template_header_footer(
    *,
    article_zip: ZipFile,
    template_zip: ZipFile,
    document_root: etree._Element,
    author_shortline: str,
) -> dict[str, bytes]:
    # Without an ARTICLE author line the template footer cannot be sanitised
    # safely.  Keep the ARTICLE stories rather than leaking another paper's name.
    if not author_shortline:
        return {}
    template_sections = _section_properties_from_zip(template_zip)
    if not template_sections:
        return {}
    first_template_section = template_sections[0]
    template_rels = _read_relationships(template_zip, "word/_rels/document.xml.rels")
    valid_refs: list[tuple[etree._Element, dict[str, str], str]] = []
    for node in first_template_section:
        if local_name(node) not in {"headerReference", "footerReference"}:
            continue
        template_id = node.get(qn("r:id"))
        rel = template_rels.get(template_id or "")
        if not rel or rel.get("Type") not in {HEADER_REL_TYPE, FOOTER_REL_TYPE}:
            continue
        story_part = _relationship_target_part("word/document.xml", rel.get("Target", ""))
        if story_part not in template_zip.namelist():
            continue
        valid_refs.append((_clone(node), rel, story_part))
    if not valid_refs:
        return {}

    # Attach TEMPLATE refs to the first section in the result.  Other sections have
    # no explicit refs and therefore inherit the journal shell.
    result_sections = document_root.xpath("//w:sectPr", namespaces=NS)
    if not result_sections:
        return {}
    first_result = result_sections[0]

    article_rels_root = _relationships_root(article_zip)
    # All ARTICLE section story references were removed when the journal section
    # system was installed.  Remove their now-orphaned package relationships before
    # adding the TEMPLATE shell.  Keeping an old footer relationship and adding a
    # second relationship to the same ``footer1.xml`` makes desktop Word repair the
    # otherwise well-formed package on open.
    for item in list(article_rels_root):
        if item.get("Type") in {HEADER_REL_TYPE, FOOTER_REL_TYPE}:
            article_rels_root.remove(item)

    replacements: dict[str, bytes] = {}
    copied_relationships: dict[tuple[str, str], str] = {}
    mapped_refs: list[etree._Element] = []
    next_id = _next_relationship_id(article_rels_root)

    for ref, rel, story_part in valid_refs:
        relationship_key = (rel["Type"], rel["Target"])
        new_id = copied_relationships.get(relationship_key)
        if new_id is None:
            new_id = f"rId{next_id}"; next_id += 1
            copied_relationships[relationship_key] = new_id
            relationship = etree.Element(f"{{{PACKAGE_REL_NS}}}Relationship")
            relationship.set("Id", new_id)
            relationship.set("Type", rel["Type"])
            relationship.set("Target", rel["Target"])
            article_rels_root.append(relationship)
        ref.set(qn("r:id"), new_id)
        mapped_refs.append(ref)

        payload = template_zip.read(story_part)
        if story_part.startswith("word/header"):
            payload = _materialise_header_format(payload)
        elif story_part.startswith("word/footer"):
            payload = _sanitise_footer_author(payload, author_shortline)
        replacements[story_part] = payload
        story_rels = _rels_part_name(story_part)
        if story_rels in template_zip.namelist():
            replacements[story_rels] = template_zip.read(story_rels)

    for child in list(first_result):
        if local_name(child) in {"headerReference", "footerReference"}:
            first_result.remove(child)
    for index, ref in enumerate(mapped_refs):
        first_result.insert(index, ref)

    replacements["word/_rels/document.xml.rels"] = _serialize_xml(article_rels_root)
    content_types = _merge_content_types(article_zip, template_zip, replacements)
    if content_types is not None:
        replacements["[Content_Types].xml"] = content_types
    return replacements


def _merge_template_document_settings(article_zip: ZipFile, template_zip: ZipFile) -> tuple[bytes | None, bool]:
    """Copy only document-level switches required by copied journal stories.

    In particular, even-page headers are ignored unless ``w:evenAndOddHeaders``
    exists in ``word/settings.xml``.  Copying the header relationships alone is
    therefore insufficient.  We deliberately *do not* replace the whole settings
    part because that could import unrelated compatibility/protection settings.
    """

    part = "word/settings.xml"
    if part not in article_zip.namelist() or part not in template_zip.namelist():
        return None, False
    article_root = etree.fromstring(article_zip.read(part))
    template_root = etree.fromstring(template_zip.read(part))
    template_even = template_root.find("w:evenAndOddHeaders", namespaces=NS)
    article_even = article_root.find("w:evenAndOddHeaders", namespaces=NS)
    enabled = template_even is not None
    changed = False
    if enabled and article_even is None:
        # Order inside CT_Settings is permissive enough for Word/LibreOffice, but
        # placing this near the front follows common producer output.
        article_root.insert(0, _clone(template_even))
        changed = True
    elif not enabled and article_even is not None:
        article_root.remove(article_even)
        changed = True
    return (_serialize_xml(article_root) if changed else article_zip.read(part), enabled)


def _materialise_header_format(payload: bytes) -> bytes:
    """Make a copied journal header independent from TEMPLATE style IDs."""

    root = etree.fromstring(payload)
    for paragraph in root.xpath(".//w:p", namespaces=NS):
        if not normalize_text(element_text(paragraph)):
            continue
        ppr = paragraph.find("w:pPr", namespaces=NS)
        if ppr is None:
            ppr = etree.Element(qn("w:pPr")); paragraph.insert(0, ppr)
        style = ppr.find("w:pStyle", namespaces=NS)
        if style is not None:
            ppr.remove(style)
        # The publication line sits above the rule and is left-aligned on every
        # page.  Some retained files carry an inherited even-page right alignment;
        # materialise the current journal rule instead of propagating that residue.
        alignment = ppr.find("w:jc", namespaces=NS)
        if alignment is None:
            alignment = etree.SubElement(ppr, qn("w:jc"))
        alignment.set(qn("w:val"), "left")
        spacing = ppr.find("w:spacing", namespaces=NS)
        if spacing is None:
            spacing = etree.SubElement(ppr, qn("w:spacing"))
        spacing.set(qn("w:before"), "0"); spacing.set(qn("w:after"), "0")
        for run in paragraph.xpath(".//w:r", namespaces=NS):
            if not element_text(run):
                continue
            rpr = run.find("w:rPr", namespaces=NS)
            if rpr is None:
                rpr = etree.Element(qn("w:rPr")); run.insert(0, rpr)
            rstyle = rpr.find("w:rStyle", namespaces=NS)
            if rstyle is not None:
                rpr.remove(rstyle)
            fonts = rpr.find("w:rFonts", namespaces=NS)
            if fonts is None:
                fonts = etree.SubElement(rpr, qn("w:rFonts"))
            for key in ("ascii", "hAnsi", "cs", "eastAsia"):
                fonts.set(qn(f"w:{key}"), "Times New Roman")
            for tag in ("sz", "szCs"):
                size = rpr.find(f"w:{tag}", namespaces=NS)
                if size is None:
                    size = etree.SubElement(rpr, qn(f"w:{tag}"))
                size.set(qn("w:val"), "20")
            _set_run_bool(run, "b", True)
            _set_run_bool(run, "i", True)
    return _serialize_xml(root)


def _sanitise_footer_author(payload: bytes, author_shortline: str) -> bytes:
    root = etree.fromstring(payload)
    if not author_shortline:
        return payload
    paragraphs = root.xpath(".//w:p", namespaces=NS)
    # Do not touch the PAGE field paragraph.  Replace a later text-bearing footer
    # line, preserving its border/alignment/italics from TEMPLATE.
    for p in paragraphs[1:]:
        if p.xpath(".//w:fldChar|.//w:instrText", namespaces=NS):
            continue
        if normalize_text(element_text(p)):
            _set_plain_text_preserve_ppr(p, author_shortline)
            break
    return _serialize_xml(root)


def _article_author_shortline(structure: ArticleStructure, report: DocumentReport) -> str:
    paragraph_by_id = {p.id: p for p in report.paragraphs}
    candidates: list[tuple[int, str]] = []
    for b in structure.blocks:
        if b.get("detected_role") != "author":
            continue
        paragraph = paragraph_by_id.get(b["id"])
        if paragraph is None:
            text = b.get("text_preview", "")
        else:
            # Affiliation markers and the corresponding-author asterisk are
            # normally separate superscript runs.  Removing those runs is both
            # more accurate and safer than trimming the final letter of every
            # surname (which turned ``Author`` into ``Autho``).
            text = "".join(
                run.text
                for run in paragraph.runs
                if (
                    run.effective_formatting.get("vertical_align")
                    or run.font.get("vertical_align")
                ) != "superscript"
            )
            text = normalize_text(text)
        # Prefer the genuinely Latin author line.  Cyrillic source lines often
        # contain Latin affiliation markers (a/b), so a boolean test is not enough.
        latin_count = len(re.findall(r"[A-Za-z]", text))
        cyr_count = len(re.findall(r"[А-Яа-яЁё]", text))
        latin_score = latin_count / max(1, latin_count + cyr_count)
        candidates.append((latin_score, text))
    if not candidates:
        return ""
    raw = max(candidates, key=lambda x: x[0])[1]
    raw = raw.replace("©", "").replace("*", "").strip()
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    result: list[str] = []
    for part in parts:
        tokens = re.findall(r"[A-Za-zА-Яа-яЁё-]+|[A-ZА-Я]\.", part)
        if not tokens:
            continue
        dotted = [t for t in tokens if t.endswith(".")]
        words = [t for t in tokens if not t.endswith(".")]
        if len(words) >= 2 and not dotted:
            surname = words[-1]
            initials = "".join(w[0].upper() + "." for w in words[:-1] if w)
        elif words:
            surname = words[-1]
            initials = "".join(dotted)
            if not initials and len(words) > 1:
                initials = "".join(w[0].upper() + "." for w in words[:-1])
        else:
            continue
        result.append(f"{surname} {initials}".strip())
    return ", ".join(result) if result else raw


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------


def _write_package(article_zip: ZipFile, output_path: Path, replacements: dict[str, bytes]) -> None:
    with ZipFile(output_path, "w", ZIP_DEFLATED) as out:
        article_names = set(article_zip.namelist())
        for info in article_zip.infolist():
            out.writestr(info, replacements.get(info.filename, article_zip.read(info.filename)))
        for name, payload in replacements.items():
            if name not in article_names:
                out.writestr(name, payload)


def _section_properties(path: Path) -> list[etree._Element]:
    with ZipFile(path) as archive:
        return _section_properties_from_zip(archive)


def _section_properties_from_zip(archive: ZipFile) -> list[etree._Element]:
    root = etree.fromstring(archive.read("word/document.xml"))
    return list(root.xpath("//w:sectPr", namespaces=NS))


def _section_column_count(section: etree._Element) -> int:
    cols = section.find("w:cols", namespaces=NS)
    if cols is None:
        return 1
    return max(1, _safe_int(cols.get(qn("w:num"))) or 1)


def _set_section_columns(section: etree._Element, count: int) -> None:
    cols = section.find("w:cols", namespaces=NS)
    if cols is None:
        cols = etree.SubElement(section, qn("w:cols"))
    if count <= 1:
        cols.attrib.pop(qn("w:num"), None)
    else:
        cols.set(qn("w:num"), str(count))


def _set_section_type(section: etree._Element, value: str) -> None:
    node = section.find("w:type", namespaces=NS)
    if node is None:
        node = etree.Element(qn("w:type")); section.insert(0, node)
    node.set(qn("w:val"), value)


def _strip_page_numbering(section: etree._Element) -> None:
    for child in list(section):
        if local_name(child) == "pgNumType":
            section.remove(child)


def _strip_story_refs_and_page_numbering(section: etree._Element) -> None:
    for child in list(section):
        if local_name(child) in {"headerReference", "footerReference", "pgNumType"}:
            section.remove(child)


def _set_paragraph_section(paragraph: etree._Element, section: etree._Element) -> None:
    ppr = paragraph.find("w:pPr", namespaces=NS)
    if ppr is None:
        ppr = etree.Element(qn("w:pPr")); paragraph.insert(0, ppr)
    old = ppr.find("w:sectPr", namespaces=NS)
    if old is not None:
        ppr.remove(old)
    ppr.append(_clone(section))


def _previous_paragraph(body: etree._Element, node: etree._Element) -> etree._Element | None:
    children = list(body)
    try:
        i = children.index(node)
    except ValueError:
        return None
    for j in range(i - 1, -1, -1):
        if local_name(children[j]) == "p":
            return children[j]
    return None


def _blank_paragraph() -> etree._Element:
    return etree.Element(qn("w:p"))


def _section_marker_paragraph(section: etree._Element) -> etree._Element:
    """Create an almost-zero-height blank paragraph carrying ``w:sectPr``."""

    p = etree.Element(qn("w:p"))
    ppr = etree.SubElement(p, qn("w:pPr"))
    spacing = etree.SubElement(ppr, qn("w:spacing"))
    spacing.set(qn("w:before"), "0")
    spacing.set(qn("w:after"), "0")
    spacing.set(qn("w:line"), "1")
    spacing.set(qn("w:lineRule"), "exact")
    ppr.append(_clone(section))
    run = etree.SubElement(p, qn("w:r"))
    rpr = etree.SubElement(run, qn("w:rPr"))
    sz = etree.SubElement(rpr, qn("w:sz")); sz.set(qn("w:val"), "2")
    szcs = etree.SubElement(rpr, qn("w:szCs")); szcs.set(qn("w:val"), "2")
    t = etree.SubElement(run, qn("w:t"))
    t.text = ""
    return p


def _set_plain_text(paragraph: etree._Element, text: str) -> None:
    ppr = paragraph.find("w:pPr", namespaces=NS)
    for child in list(paragraph):
        if child is not ppr:
            paragraph.remove(child)
    run = etree.SubElement(paragraph, qn("w:r"))
    t = etree.SubElement(run, qn("w:t"))
    if text.startswith(" ") or text.endswith(" "):
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t.text = text


def _set_plain_text_preserve_ppr(paragraph: etree._Element, text: str) -> None:
    ppr = paragraph.find("w:pPr", namespaces=NS)
    first_rpr = paragraph.find(".//w:r/w:rPr", namespaces=NS)
    for child in list(paragraph):
        if child is not ppr:
            paragraph.remove(child)
    run = etree.SubElement(paragraph, qn("w:r"))
    if first_rpr is not None:
        run.append(_clone(first_rpr))
    t = etree.SubElement(run, qn("w:t")); t.text = text


def _copy_attribute_dict(element: etree._Element, attrs: dict[str, Any]) -> None:
    for key, value in attrs.items():
        if value is None:
            continue
        local = _local_attr_name(key)
        if local in {"beforeAutospacing", "afterAutospacing"}:
            continue
        element.set(qn(f"w:{local}"), str(value))


def _local_attr_name(value: str) -> str:
    if value.startswith("{"):
        return value.rsplit("}", 1)[-1]
    if ":" in value:
        return value.split(":", 1)[-1]
    return value


def _replace_child_by_local_name(parent: etree._Element, child: etree._Element) -> None:
    name = local_name(child)
    for old in list(parent):
        if local_name(old) == name:
            parent.replace(old, child)
            return
    parent.append(child)


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _clone(element: etree._Element | None) -> etree._Element | None:
    if element is None:
        return None
    return etree.fromstring(etree.tostring(element, with_tail=False))


def _serialize_xml(root: etree._Element) -> bytes:
    _normalise_property_order(root)
    return etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True)


def _normalise_property_order(root: etree._Element) -> None:
    """Restore schema order after conservative in-place OOXML mutations.

    Do not call ``cleanup_namespaces`` here.  Word documents commonly declare
    compatibility prefixes only through ``mc:Ignorable``.  Lxml regards those
    declarations as unused and removes them, leaving an invalid Ignorable value
    that makes desktop Word report the generated DOCX as corrupt.
    """

    for parent in root.iter():
        order = _PROPERTY_CHILD_ORDER.get(local_name(parent))
        if not order or len(parent) < 2:
            continue
        ranks = {name: index for index, name in enumerate(order)}
        children = list(parent)
        sorted_children = sorted(
            enumerate(children),
            key=lambda item: (ranks.get(local_name(item[1]), len(order)), item[0]),
        )
        if [child for _, child in sorted_children] == children:
            continue
        for child in children:
            parent.remove(child)
        for _, child in sorted_children:
            parent.append(child)


def _dedupe_overlapping_spans(spans: list[list[etree._Element]]) -> list[list[etree._Element]]:
    result: list[list[etree._Element]] = []
    used: set[etree._Element] = set()
    for span in spans:
        ids = set(span)
        if ids & used:
            continue
        used |= ids
        result.append(span)
    return result


def _relationships_root(archive: ZipFile) -> etree._Element:
    if "word/_rels/document.xml.rels" in archive.namelist():
        return etree.fromstring(archive.read("word/_rels/document.xml.rels"))
    return etree.Element(f"{{{PACKAGE_REL_NS}}}Relationships")


def _read_relationships(archive: ZipFile, part: str) -> dict[str, dict[str, str]]:
    if part not in archive.namelist():
        return {}
    root = etree.fromstring(archive.read(part))
    return {item.get("Id"): dict(item.attrib) for item in root.findall(f"{{{PACKAGE_REL_NS}}}Relationship") if item.get("Id")}


def _next_relationship_id(root: etree._Element) -> int:
    highest = 0
    for item in root.findall(f"{{{PACKAGE_REL_NS}}}Relationship"):
        value = item.get("Id", "")
        if value.startswith("rId"):
            try:
                highest = max(highest, int(value[3:]))
            except ValueError:
                pass
    return highest + 1


def _relationship_target_part(source_part: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    directory = source_part.rsplit("/", 1)[0] if "/" in source_part else ""
    parts: list[str] = []
    for part in f"{directory}/{target}".split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
        else:
            parts.append(part)
    return "/".join(parts)


def _rels_part_name(source_part: str) -> str:
    directory = source_part.rsplit("/", 1)[0] if "/" in source_part else ""
    name = source_part.rsplit("/", 1)[-1]
    return f"{directory}/_rels/{name}.rels" if directory else f"_rels/{name}.rels"


def _merge_content_types(article_zip: ZipFile, template_zip: ZipFile, replacements: dict[str, bytes]) -> bytes | None:
    if "[Content_Types].xml" not in article_zip.namelist() or "[Content_Types].xml" not in template_zip.namelist():
        return None
    article_root = etree.fromstring(article_zip.read("[Content_Types].xml"))
    template_root = etree.fromstring(template_zip.read("[Content_Types].xml"))
    existing_overrides = {item.get("PartName") for item in article_root.findall(f"{{{CONTENT_TYPES_NS}}}Override")}
    existing_defaults = {item.get("Extension") for item in article_root.findall(f"{{{CONTENT_TYPES_NS}}}Default")}
    copied = {f"/{name}" for name in replacements if name.startswith("word/header") or name.startswith("word/footer")}
    for item in template_root.findall(f"{{{CONTENT_TYPES_NS}}}Override"):
        part = item.get("PartName")
        if part in copied and part not in existing_overrides:
            article_root.append(_clone(item)); existing_overrides.add(part)
    for item in template_root.findall(f"{{{CONTENT_TYPES_NS}}}Default"):
        ext = item.get("Extension")
        if ext and ext not in existing_defaults:
            article_root.append(_clone(item)); existing_defaults.add(ext)
    return _serialize_xml(article_root)
