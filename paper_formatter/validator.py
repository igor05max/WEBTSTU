from __future__ import annotations

import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from lxml import etree
from docx.oxml.ns import qn

from paper_formatter.models import (
    ArticleIR,
    EquationBlock,
    FigureBlock,
    ListItemBlock,
    ParagraphBlock,
    SectionBlock,
    TableBlock,
    TemplateProfile,
)
from paper_formatter.renderers.docx_table_adapter import WideTableAdapter
from paper_formatter.semantic.models import SemanticAnalysis, SemanticBlock


class ConversionValidator:
    """Проверяет структуру, текст, объекты, ссылки, ресурсы и выходные файлы."""

    def validate(
        self,
        *,
        source_path: Path,
        article: ArticleIR,
        main_tex: Path,
        pdf_path: Path | None,
        semantic_blocks: list[SemanticBlock] | None = None,
        semantic_analysis: SemanticAnalysis | None = None,
        docx_path: Path | None = None,
        compile_log: Path | None = None,
        template_profile: TemplateProfile | None = None,
        package_analysis: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        semantic_blocks = semantic_blocks or []
        source_counts = self._source_counts(source_path)
        article_counts = self._article_counts(article)
        warnings: list[str] = []
        errors: list[str] = []

        if not article.metadata.titles:
            warnings.append("VALIDATION: не найдено название документа.")
        if not article.metadata.authors:
            warnings.append("VALIDATION: не найдены авторы документа.")
        self._compare_count(
            warnings, "таблиц", source_counts.get("tables"), article_counts["tables"]
        )
        self._compare_count(
            warnings,
            "формул",
            source_counts.get("formulas"),
            article_counts["equations"] + article_counts["formula_images"],
        )
        self._compare_count(
            warnings,
            "рисунков",
            source_counts.get("drawings"),
            article_counts["figures"] + article_counts["formula_images"],
            tolerance=1,
        )

        all_ids = [block.id for block in article.body]
        duplicate_ids = [
            value for value, count in Counter(all_ids).items() if count > 1
        ]
        if duplicate_ids:
            errors.append(
                f"VALIDATION: повторяются идентификаторы блоков: {duplicate_ids[:10]}."
            )

        unknown: list[str] = []
        low_confidence: list[str] = []
        if semantic_analysis:
            for decision in semantic_analysis.decisions:
                if decision.role == "unknown":
                    unknown.append(decision.block_id)
                if decision.confidence < 0.60:
                    low_confidence.append(decision.block_id)
        if unknown:
            warnings.append(f"VALIDATION: не определена роль {len(unknown)} блоков.")
        if low_confidence:
            warnings.append(
                f"VALIDATION: низкая уверенность семантики у {len(low_confidence)} блоков."
            )

        tex_exists = main_tex.exists() and main_tex.stat().st_size > 0
        tex_text = (
            main_tex.read_text(encoding="utf-8", errors="replace") if tex_exists else ""
        )
        if not tex_exists:
            errors.append("VALIDATION: main.tex отсутствует или пуст.")
        tex_integrity = self._tex_integrity(main_tex, article)
        warnings.extend(tex_integrity["warnings"])
        errors.extend(tex_integrity["errors"])

        asset_checks = self._asset_checks(main_tex.parent, article)
        warnings.extend(asset_checks["warnings"])
        errors.extend(asset_checks["errors"])

        text_coverage = self._text_coverage(source_path, article)
        if text_coverage is not None and text_coverage < 0.88:
            warnings.append(
                f"VALIDATION: покрытие исходной лексики только {text_coverage:.1%}."
            )
        warnings.extend(
            self._pandoc_disagreements(package_analysis, article_counts)
        )

        compile_warnings = self._compile_log_warnings(compile_log)
        warnings.extend(compile_warnings)
        pdf_info = self._pdf_info(pdf_path)
        if pdf_path is not None and not pdf_info["exists"]:
            errors.append("VALIDATION: заявленный PDF отсутствует.")
        docx_exists = bool(docx_path and docx_path.exists() and docx_path.stat().st_size > 0)
        if docx_path is not None and not docx_exists:
            errors.append("VALIDATION: заявленный DOCX отсутствует.")
        docx_style_audit = self._docx_style_audit(
            docx_path if docx_exists else None,
            template_profile,
            article,
        )
        warnings.extend(docx_style_audit.get("warnings", []))
        docx_object_audit = self._docx_object_audit(
            source_counts,
            docx_path if docx_exists else None,
        )
        warnings.extend(docx_object_audit.get("warnings", []))
        docx_content_audit = self._docx_content_audit(
            article,
            docx_path if docx_exists else None,
        )
        warnings.extend(docx_content_audit.get("warnings", []))
        docx_layout_audit = self._docx_layout_audit(
            article,
            docx_path if docx_exists else None,
            template_profile,
        )
        warnings.extend(docx_layout_audit.get("warnings", []))
        critical_errors = list(docx_object_audit.get("critical_errors", []))
        critical_errors.extend(docx_content_audit.get("critical_errors", []))
        errors.extend(critical_errors)

        structure_score = self._structure_score(source_counts, article_counts)
        asset_score = asset_checks["score"]
        reference_score = tex_integrity["reference_score"]
        warnings = list(dict.fromkeys(warnings))
        errors = list(dict.fromkeys(errors))
        manual_review = bool(
            errors
            or warnings
            or source_path.suffix.lower() == ".pdf"
            or (template_profile and template_profile.confidence < 0.75)
        )
        return {
            "source": str(source_path),
            "semantic_provider": semantic_analysis.provider if semantic_analysis else article.semantic_provider,
            "source_counts": source_counts,
            "article_ir_counts": article_counts,
            "semantic": {
                "candidate_blocks": len(semantic_blocks),
                "unknown_blocks": unknown,
                "low_confidence_blocks": low_confidence,
            },
            "integrity": {
                "text_coverage": text_coverage,
                "duplicate_block_ids": duplicate_ids,
                "assets": asset_checks,
                "latex": tex_integrity,
                "docx_styles": docx_style_audit,
                "docx_objects": docx_object_audit,
                "docx_content": docx_content_audit,
                "docx_layout": docx_layout_audit,
            },
            "outputs": {
                "main_tex_exists": tex_exists,
                "pdf_exists": pdf_info["exists"],
                "pdf_pages": pdf_info.get("pages"),
                "docx_exists": docx_exists,
            },
            "scores": {
                "structure_preservation": structure_score,
                "asset_preservation": asset_score,
                "reference_integrity": reference_score,
                "template_confidence": template_profile.confidence if template_profile else None,
            },
            "package": package_analysis,
            "manual_review_required": manual_review,
            "critical_errors": critical_errors,
            "warnings": warnings,
            "errors": errors,
        }

    @staticmethod
    def _docx_layout_audit(
        article: ArticleIR,
        docx_path: Path | None,
        template_profile: TemplateProfile | None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "enabled": False,
            "issues": [],
            "warnings": [],
        }
        if docx_path is None:
            return result
        article_tables = [block for block in article.body if isinstance(block, TableBlock)]
        if not article_tables:
            return result
        result["enabled"] = True
        try:
            with zipfile.ZipFile(docx_path) as archive:
                root = etree.fromstring(archive.read("word/document.xml"))
        except Exception as exc:
            result["warnings"].append(
                f"VALIDATION: не удалось проверить layout таблиц DOCX ({exc})."
            )
            return result

        tables = [
            table
            for table in root.xpath(".//*[local-name()='tbl']")
            if not table.xpath(".//*[local-name()='drawing']")
        ]
        base_font_size = (
            template_profile.typography.caption_size_pt
            if template_profile and template_profile.typography.caption_size_pt
            else (
                max(8.0, template_profile.typography.main_size_pt - 1.0)
                if template_profile
                else 8.0
            )
        )
        page_width = template_profile.page.width_mm if template_profile else None
        if template_profile and page_width:
            full_width = (
                page_width
                - template_profile.page.margin_left_mm
                - template_profile.page.margin_right_mm
            )
        else:
            full_width = None
        for index, block in enumerate(article_tables):
            if index >= len(tables):
                break
            table = tables[index]
            assigned = ConversionValidator._docx_table_grid_widths_mm(table)
            if not assigned:
                continue
            font_size = (
                ConversionValidator._docx_table_font_size_pt(table)
                or base_font_size
            )
            measures = WideTableAdapter.measure_columns(
                block,
                len(assigned),
                font_size,
            )
            score = WideTableAdapter.layout_score(
                block,
                assigned,
                measures,
                font_size,
            )
            for column, measure in enumerate(measures):
                if column >= len(assigned):
                    continue
                if assigned[column] + 0.2 < measure.min_width_mm:
                    result["issues"].append(
                        {
                            "object": f"Table {index + 1}",
                            "rule": "table.column.min_width",
                            "status": "warning",
                            "column": column + 1,
                            "assigned_mm": round(assigned[column], 2),
                            "required_mm": round(measure.min_width_mm, 2),
                        }
                    )
                numeric_required = (
                    measure.longest_numeric_chars
                    * WideTableAdapter._char_width_mm(font_size)
                    * 1.05
                )
                if measure.longest_numeric_chars and assigned[column] + 0.2 < numeric_required:
                    result["issues"].append(
                        {
                            "object": f"Table {index + 1}",
                            "rule": "table.column.numeric_break",
                            "status": "warning",
                            "column": column + 1,
                            "assigned_mm": round(assigned[column], 2),
                            "required_mm": round(numeric_required, 2),
                        }
                    )
            if score > 0:
                result["issues"].append(
                    {
                        "object": f"Table {index + 1}",
                        "rule": "table.layout.score",
                        "status": "warning",
                        "score": round(score, 2),
                    }
                )
            if full_width and len(assigned) >= 4 and sum(assigned) < full_width * 0.92:
                result["issues"].append(
                    {
                        "object": f"Table {index + 1}",
                        "rule": "table.wide.full_width",
                        "status": "warning",
                        "assigned_mm": round(sum(assigned), 2),
                        "required_mm": round(full_width, 2),
                    }
                )
        if result["issues"]:
            result["warnings"].append(
                "VALIDATION: DOCX table layout warnings: "
                + ", ".join(
                    str(issue.get("rule"))
                    for issue in result["issues"][:8]
                    if isinstance(issue, dict)
                )
                + "."
            )
        return result

    @staticmethod
    def _docx_content_audit(article: ArticleIR, docx_path: Path | None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "authors": {"source": len(article.metadata.authors), "result": None, "missing": []},
            "affiliations": {
                "source": len(article.metadata.affiliations),
                "result": None,
                "missing": [],
            },
            "critical_errors": [],
            "warnings": [],
        }
        if docx_path is None:
            return result
        text = ConversionValidator._docx_plain_text(docx_path)
        if not text:
            result["warnings"].append(
                "VALIDATION: не удалось извлечь текст итогового DOCX для проверки front matter."
            )
            return result

        author_candidates = ConversionValidator._identity_candidates(
            [author.name for author in article.metadata.authors]
            + [variant.text for variant in article.metadata.author_variants]
        )
        affiliation_candidates = ConversionValidator._identity_candidates(
            [affiliation.name for affiliation in article.metadata.affiliations]
        )
        author_missing = [
            candidate
            for candidate in author_candidates
            if not ConversionValidator._identity_in_text(candidate, text)
        ]
        affiliation_missing = [
            candidate
            for candidate in affiliation_candidates
            if not ConversionValidator._identity_in_text(candidate, text)
        ]
        result["authors"] = {
            "source": len(author_candidates),
            "result": len(author_candidates) - len(author_missing),
            "missing": author_missing[:12],
        }
        result["affiliations"] = {
            "source": len(affiliation_candidates),
            "result": len(affiliation_candidates) - len(affiliation_missing),
            "missing": affiliation_missing[:12],
        }
        if author_candidates and len(author_missing) == len(author_candidates):
            result["critical_errors"].append(
                "VALIDATION: итоговый DOCX потерял авторов: "
                f"0 из {len(author_candidates)} найденных записей присутствуют в документе."
            )
        elif author_missing:
            result["warnings"].append(
                "VALIDATION: часть авторов не найдена в итоговом DOCX: "
                + ", ".join(author_missing[:5])
                + "."
            )
        if affiliation_candidates and len(affiliation_missing) == len(affiliation_candidates):
            result["critical_errors"].append(
                "VALIDATION: итоговый DOCX потерял организации: "
                f"0 из {len(affiliation_candidates)} найденных записей присутствуют в документе."
            )
        elif affiliation_missing:
            result["warnings"].append(
                "VALIDATION: часть организаций не найдена в итоговом DOCX: "
                + ", ".join(affiliation_missing[:5])
                + "."
            )
        return result

    @staticmethod
    def _docx_table_grid_widths_mm(table) -> list[float]:
        values: list[float] = []
        for column in table.xpath("./*[local-name()='tblGrid']/*[local-name()='gridCol']"):
            raw = column.get(qn("w:w"))
            if not raw:
                continue
            try:
                values.append(int(raw) * 25.4 / 1440)
            except ValueError:
                continue
        return values

    @staticmethod
    def _docx_table_font_size_pt(table) -> float | None:
        for size in table.xpath(".//*[local-name()='rPr']/*[local-name()='sz']"):
            raw = size.get(qn("w:val"))
            if not raw:
                continue
            try:
                return int(raw) / 2
            except ValueError:
                continue
        return None

    @staticmethod
    def _docx_plain_text(docx_path: Path) -> str:
        try:
            with zipfile.ZipFile(docx_path) as archive:
                root = etree.fromstring(archive.read("word/document.xml"))
        except Exception:
            return ""
        return "\n".join(root.xpath(".//*[local-name()='t']/text()"))

    @staticmethod
    def _identity_candidates(values: list[str]) -> list[str]:
        candidates: list[str] = []
        for value in values:
            clean = re.sub(r"^\s*©\s*", "", value or "").strip()
            for part in re.split(r"\s*[;,]\s*", clean):
                part = re.sub(r"\s+", " ", part).strip()
                if len(part) >= 4 and not re.fullmatch(r"[\W\d_]+", part):
                    candidates.append(part)
        return list(dict.fromkeys(candidates))

    @staticmethod
    def _identity_in_text(candidate: str, text: str) -> bool:
        normalized_text = ConversionValidator._identity_normalize(text)
        normalized_candidate = ConversionValidator._identity_normalize(candidate)
        if normalized_candidate and normalized_candidate in normalized_text:
            return True
        tokens = [
            token
            for token in normalized_candidate.split()
            if len(token) >= 4 or re.search(r"[А-Яа-яЁё]", token)
        ]
        return bool(tokens) and all(token in normalized_text for token in tokens)

    @staticmethod
    def _identity_normalize(value: str) -> str:
        value = value.casefold()
        value = re.sub(r"©", " ", value)
        value = re.sub(r"(?<=[a-zа-яё])[a-d]\*?(?=\s|$)", "", value)
        value = re.sub(r"[^0-9a-zа-яё]+", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _docx_object_audit(
        source_counts: dict[str, int | None],
        docx_path: Path | None,
    ) -> dict[str, Any]:
        result_counts = (
            ConversionValidator._docx_counts(docx_path)
            if docx_path is not None
            else {}
        )
        warnings: list[str] = []
        critical_errors: list[str] = []
        preserved: dict[str, dict[str, int | None]] = {}
        labels = {
            "tables": "таблицы",
            "table_rows": "строки таблиц",
            "table_cells": "ячейки таблиц",
            "drawings": "рисунки",
            "formulas": "формулы",
            "media_files": "media-файлы",
        }
        for key, label in labels.items():
            source = source_counts.get(key)
            target = result_counts.get(key)
            preserved[key] = {"source": source, "result": target}
            if source is None or source <= 0 or target is None:
                continue
            if target < source:
                critical_errors.append(
                    "VALIDATION: итоговый DOCX потерял "
                    f"{label}: {target} < {source}."
                )
        source_shapes = source_counts.get("table_shapes")
        target_shapes = result_counts.get("table_shapes")
        if isinstance(source_shapes, list) and isinstance(target_shapes, list):
            for index, source_shape in enumerate(source_shapes):
                if index >= len(target_shapes):
                    continue
                target_shape = target_shapes[index]
                if not isinstance(source_shape, dict) or not isinstance(target_shape, dict):
                    continue
                if (
                    target_shape.get("rows", 0) < source_shape.get("rows", 0)
                    or target_shape.get("cells", 0) < source_shape.get("cells", 0)
                ):
                    critical_errors.append(
                        "VALIDATION: итоговый DOCX повредил структуру таблицы "
                        f"{index + 1}: rows/cells "
                        f"{target_shape.get('rows', 0)}/{target_shape.get('cells', 0)} "
                        f"< {source_shape.get('rows', 0)}/{source_shape.get('cells', 0)}."
                    )
        return {
            "source_counts": source_counts,
            "result_counts": result_counts,
            "preserved": preserved,
            "critical_errors": critical_errors,
            "warnings": warnings,
        }

    @staticmethod
    def _docx_style_audit(
        docx_path: Path | None,
        template_profile: TemplateProfile | None,
        article: ArticleIR,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "enabled": False,
            "expected": {},
            "used_counts": {},
            "matched_roles": [],
            "missing_roles": [],
            "table_style": None,
            "warnings": [],
        }
        if (
            docx_path is None
            or template_profile is None
            or template_profile.source_type != "docx"
        ):
            return result
        try:
            from docx import Document

            document = Document(docx_path)
        except Exception as exc:
            result["warnings"].append(
                f"VALIDATION: не удалось проверить стили итогового DOCX ({exc})."
            )
            return result

        result["enabled"] = True
        style_counts = Counter(
            paragraph.style.name
            for paragraph in document.paragraphs
            if paragraph.text.strip() and paragraph.style is not None
        )
        result["used_counts"] = dict(style_counts)
        evidence = template_profile.evidence or {}
        expected = {
            key.removeprefix("docx_style_"): value
            for key, value in evidence.items()
            if key.startswith("docx_style_") and key != "docx_style_table"
            and isinstance(value, str)
        }
        result["expected"] = expected

        required: set[str] = set()
        if article.metadata.titles:
            required.add("title")
        if article.metadata.authors:
            required.add("authors")
        if article.metadata.abstracts:
            required.add("abstract")
        if article.metadata.keywords:
            required.add("keywords")
        if any(isinstance(block, ParagraphBlock) for block in article.body):
            required.add("body")
        for level in range(1, 7):
            if any(
                isinstance(block, SectionBlock) and block.level == level
                for block in article.body
            ):
                required.add(f"heading{level}")
        if any(isinstance(block, ListItemBlock) and block.ordered for block in article.body):
            required.add("list_number")
        if any(isinstance(block, ListItemBlock) and not block.ordered for block in article.body):
            required.add("list_bullet")
        if any(isinstance(block, EquationBlock) for block in article.body):
            required.add("equation")
        if any(isinstance(block, TableBlock) and block.caption for block in article.body):
            required.add("table_caption")
        if any(isinstance(block, FigureBlock) and block.caption for block in article.body):
            required.add("figure_caption")
        if article.references:
            required.add("references")

        matched: list[str] = []
        missing: list[str] = []
        for role in sorted(required):
            style_name = expected.get(role)
            if not style_name:
                continue
            if style_counts.get(style_name, 0) > 0:
                matched.append(role)
            else:
                missing.append(role)
        result["matched_roles"] = matched
        result["missing_roles"] = missing
        if missing:
            result["warnings"].append(
                "VALIDATION: итоговый DOCX не использовал стили шаблона для ролей: "
                + ", ".join(missing)
                + "."
            )

        expected_table_style = evidence.get("docx_style_table")
        if isinstance(expected_table_style, str):
            table_styles = [
                table.style.name if table.style is not None else None
                for table in document.tables
            ]
            result["table_style"] = {
                "expected": expected_table_style,
                "used": table_styles,
                "matched": (
                    not document.tables
                    or all(name == expected_table_style for name in table_styles)
                ),
            }
            if document.tables and not result["table_style"]["matched"]:
                result["warnings"].append(
                    "VALIDATION: часть таблиц не получила табличный стиль DOCX-образца."
                )
        return result

    def _source_counts(self, source_path: Path) -> dict[str, int | None]:
        suffix = source_path.suffix.lower()
        if suffix == ".docx":
            return self._docx_counts(source_path)
        if suffix == ".tex":
            text = self._read_tex_tree(source_path)
            return {
                "paragraphs": None,
                "tables": len(re.findall(r"\\begin\s*\{table\*?\}", text)),
                "formulas": len(
                    re.findall(
                        r"\\begin\s*\{(?:equation|align|gather|multline)\*?\}|\$\$",
                        text,
                    )
                ),
                "drawings": len(re.findall(r"\\includegraphics", text)),
                "media_files": None,
            }
        if suffix == ".pdf":
            try:
                import pymupdf

                document = pymupdf.open(source_path)
                images = sum(len(page.get_images(full=True)) for page in document)
                pages = document.page_count
                document.close()
                return {
                    "paragraphs": None,
                    "tables": None,
                    "formulas": None,
                    "drawings": images,
                    "media_files": images,
                    "pages": pages,
                }
            except Exception:
                pass
        return {
            "paragraphs": None,
            "tables": None,
            "formulas": None,
            "drawings": None,
            "media_files": None,
        }

    @staticmethod
    def _docx_counts(source_path: Path) -> dict[str, int]:
        result = {
            "paragraphs": 0,
            "tables": 0,
            "table_rows": 0,
            "table_cells": 0,
            "table_shapes": [],
            "formulas": 0,
            "omml": 0,
            "ole": 0,
            "drawings": 0,
            "media_files": 0,
        }
        try:
            with zipfile.ZipFile(source_path) as archive:
                root = etree.fromstring(archive.read("word/document.xml"))
                result["paragraphs"] = len(root.xpath(".//*[local-name()='p']"))
                tables = root.xpath(".//*[local-name()='tbl']")
                layout_tables = 0
                table_shapes: list[dict[str, int]] = []
                for table in tables:
                    rows = table.xpath("./*[local-name()='tr']")
                    cells = table.xpath("./*[local-name()='tr']/*[local-name()='tc']")
                    has_math = bool(table.xpath(".//*[local-name()='oMath']"))
                    has_drawing = bool(table.xpath(".//*[local-name()='drawing']"))
                    if has_drawing or (
                        has_math and len(rows) == 1 and len(cells) <= 2
                    ):
                        layout_tables += 1
                        continue
                    table_shapes.append(
                        {
                            "rows": len(rows),
                            "cells": len(cells),
                            "columns": max(
                                (
                                    ConversionValidator._docx_row_column_count(row)
                                    for row in rows
                                ),
                                default=0,
                            ),
                        }
                    )
                result["tables"] = len(tables) - layout_tables
                result["table_rows"] = sum(item["rows"] for item in table_shapes)
                result["table_cells"] = sum(item["cells"] for item in table_shapes)
                result["table_shapes"] = table_shapes
                math_paragraphs = root.xpath(".//*[local-name()='oMathPara']")
                inline_math = root.xpath(
                    ".//*[local-name()='oMath' and not(ancestor::*[local-name()='oMathPara'])]"
                )
                result["omml"] = len(math_paragraphs) + len(inline_math)
                result["ole"] = len(root.xpath(".//*[local-name()='OLEObject']"))
                result["formulas"] = result["omml"] + result["ole"]
                result["drawings"] = len(root.xpath(".//*[local-name()='drawing']"))
                result["media_files"] = len(
                    [
                        name
                        for name in archive.namelist()
                        if name.startswith("word/media/") and not name.endswith("/")
                    ]
                )
        except Exception:
            pass
        return result

    @staticmethod
    def _docx_row_column_count(row) -> int:
        total = 0
        for cell in row.xpath("./*[local-name()='tc']"):
            span = 1
            span_nodes = cell.xpath("./*[local-name()='tcPr']/*[local-name()='gridSpan']")
            if span_nodes:
                try:
                    span = max(1, int(span_nodes[0].get(qn("w:val")) or "1"))
                except ValueError:
                    span = 1
            total += span
        return total

    @staticmethod
    def _article_counts(article: ArticleIR) -> dict[str, int]:
        return {
            "paragraphs": sum(isinstance(block, ParagraphBlock) for block in article.body),
            "sections": sum(isinstance(block, SectionBlock) for block in article.body),
            "list_items": sum(isinstance(block, ListItemBlock) for block in article.body),
            "equations": (
                sum(isinstance(block, EquationBlock) for block in article.body)
                + sum(
                    1
                    for block in article.body
                    if isinstance(block, (ParagraphBlock, ListItemBlock))
                    for run in block.runs
                    if run.math_latex is not None
                )
            ),
            "formula_images": sum(
                1
                for block in article.body
                if isinstance(block, (ParagraphBlock, ListItemBlock))
                for run in block.runs
                if run.asset_id and run.formula_image
            ),
            "figures": sum(isinstance(block, FigureBlock) for block in article.body),
            "tables": sum(isinstance(block, TableBlock) for block in article.body),
            "references": len(article.references),
            "citations": len(article.citations),
            "cross_references": len(article.cross_references),
            "notes": len(article.notes),
            "authors": len(article.metadata.authors),
            "titles": len(article.metadata.titles),
            "assets": len(article.assets),
        }

    def _tex_integrity(self, main_tex: Path, article: ArticleIR) -> dict[str, Any]:
        warnings: list[str] = []
        errors: list[str] = []
        texts: list[str] = []
        for path in (main_tex, main_tex.parent / "metadata.tex", main_tex.parent / "body.tex"):
            if path.exists():
                texts.append(path.read_text(encoding="utf-8", errors="replace"))
        text = "\n".join(texts)
        labels = re.findall(r"\\label\s*\{([^}]+)\}", text)
        refs = re.findall(r"\\(?:ref|eqref|autoref)\s*\{([^}]+)\}", text)
        citations = [
            key.strip()
            for group in re.findall(r"\\cite[a-zA-Z*]*(?:\[[^\]]*\])*\{([^}]+)\}", text)
            for key in group.split(",")
        ]
        bibitems = re.findall(r"\\bibitem(?:\[[^\]]*\])?\{([^}]+)\}", text)
        duplicate_labels = [
            value for value, count in Counter(labels).items() if count > 1
        ]
        unresolved_refs = sorted(set(refs) - set(labels))
        unresolved_citations = sorted(set(citations) - set(bibitems))
        if duplicate_labels:
            errors.append(
                f"VALIDATION: повторяются LaTeX labels: {duplicate_labels[:10]}."
            )
        if unresolved_refs:
            warnings.append(
                f"VALIDATION: не разрешено ссылок: {len(unresolved_refs)}."
            )
        if unresolved_citations:
            warnings.append(
                f"VALIDATION: не разрешено цитат: {len(unresolved_citations)}."
            )
        expected = len(refs) + len(citations)
        resolved = expected - len(unresolved_refs) - len(unresolved_citations)
        reference_score = 1.0 if expected == 0 else max(0.0, resolved / expected)
        return {
            "labels": len(labels),
            "references": len(refs),
            "citations": len(citations),
            "bibitems": len(bibitems),
            "duplicate_labels": duplicate_labels,
            "unresolved_references": unresolved_refs,
            "unresolved_citations": unresolved_citations,
            "reference_score": round(reference_score, 4),
            "warnings": warnings,
            "errors": errors,
        }

    @staticmethod
    def _asset_checks(project_dir: Path, article: ArticleIR) -> dict[str, Any]:
        warnings: list[str] = []
        errors: list[str] = []
        missing: list[str] = []
        mismatched_hashes: list[str] = []
        for asset in article.assets:
            path = Path(asset.path)
            if not path.is_absolute():
                path = project_dir / path
            if not path.exists():
                missing.append(asset.id)
                continue
            if asset.sha256:
                from paper_formatter.utils.files import sha256_file

                if sha256_file(path) != asset.sha256:
                    mismatched_hashes.append(asset.id)
        if missing:
            errors.append(f"VALIDATION: отсутствуют ресурсы: {missing[:10]}.")
        if mismatched_hashes:
            warnings.append(
                f"VALIDATION: изменились контрольные суммы ресурсов: {mismatched_hashes[:10]}."
            )
        total = len(article.assets)
        score = 1.0 if total == 0 else (total - len(missing)) / total
        return {
            "total": total,
            "missing": missing,
            "hash_mismatch": mismatched_hashes,
            "score": round(score, 4),
            "warnings": warnings,
            "errors": errors,
        }

    def _text_coverage(self, source_path: Path, article: ArticleIR) -> float | None:
        source_text = self._source_text(source_path)
        if not source_text:
            return None
        article_text_parts = [
            *(item.text for item in article.metadata.titles),
            *(item.text for item in article.metadata.subtitles),
            *(author.name for author in article.metadata.authors),
            *(item.name for item in article.metadata.affiliations),
            *(item.text for item in article.metadata.abstracts),
            *article.metadata.keywords,
        ]
        for block in article.body:
            if isinstance(block, SectionBlock):
                article_text_parts.append(block.title)
            elif isinstance(block, (ParagraphBlock, ListItemBlock)):
                article_text_parts.append("".join(run.text for run in block.runs))
            elif isinstance(block, TableBlock):
                article_text_parts.extend(cell for row in block.rows for cell in row)
        article_text_parts.extend(item.text for item in article.references)
        source_tokens = self._tokens(source_text)
        target_tokens = self._tokens("\n".join(article_text_parts))
        if not source_tokens:
            return None
        return round(len(source_tokens & target_tokens) / len(source_tokens), 4)

    @staticmethod
    def _source_text(source_path: Path) -> str:
        try:
            if source_path.suffix.lower() == ".docx":
                with zipfile.ZipFile(source_path) as archive:
                    root = etree.fromstring(archive.read("word/document.xml"))
                    return " ".join(root.xpath(".//*[local-name()='t']/text()"))
            if source_path.suffix.lower() == ".tex":
                text = ConversionValidator._read_tex_tree(source_path)
                text = re.sub(r"%.*$", "", text, flags=re.MULTILINE)
                text = re.sub(
                    r"(?m)^\s*\\(?:documentclass|usepackage|settopmatter|renewcommand|"
                    r"newcommand|providecommand|journal|date|pubyear|shorttitle|"
                    r"shortauthors|titlerunning|authorrunning|pagerange)\b[^\n]*$",
                    " ",
                    text,
                )
                text = re.sub(
                    r"\\includegraphics(?:\[[^\]]*\])?\s*\{[^}]*\}|"
                    r"\\(?:label|bibitem|bibliography|bibliographystyle)\s*\{[^}]*\}",
                    " ",
                    text,
                )
                text = re.sub(
                    r"\\begin\s*\{(?:equation|align|gather|multline|displaymath)\*?\}"
                    r".*?\\end\s*\{(?:equation|align|gather|multline|displaymath)\*?\}",
                    " ",
                    text,
                    flags=re.DOTALL,
                )
                text = re.sub(r"\$[^$]*\$", " ", text)
                text = re.sub(r"\\(?:begin|end)\s*\{[^}]+\}", " ", text)
                text = re.sub(r"\\[A-Za-z@]+\*?(?:\[[^\]]*\])?", " ", text)
                return re.sub(r"[{}$&_^~\\]", " ", text)
            if source_path.suffix.lower() == ".pdf":
                import pymupdf

                document = pymupdf.open(source_path)
                text = "\n".join(page.get_text() for page in document)
                document.close()
                return text
        except Exception:
            return ""
        return ""

    @staticmethod
    def _read_tex_tree(source_path: Path, seen: set[Path] | None = None) -> str:
        source_path = source_path.resolve()
        seen = seen or set()
        if source_path in seen or not source_path.exists():
            return ""
        seen.add(source_path)
        text = source_path.read_text(encoding="utf-8", errors="replace")
        text = re.sub(r"(?<!\\)%.*$", "", text, flags=re.MULTILINE)

        def replace(match: re.Match[str]) -> str:
            child = source_path.parent / match.group(1).strip()
            if not child.suffix:
                child = child.with_suffix(".tex")
            return ConversionValidator._read_tex_tree(child, seen)

        return re.sub(r"\\(?:input|include)\s*\{([^}]+)\}", replace, text)

    @staticmethod
    def _compile_log_warnings(log_path: Path | None) -> list[str]:
        if not log_path or not log_path.exists():
            return []
        text = log_path.read_text(encoding="utf-8", errors="replace")
        result: list[str] = []
        overfull = [
            float(value)
            for value in re.findall(
                r"Overfull \\[hv]box \(([0-9.]+)pt too wide\)",
                text,
            )
        ]
        if overfull and max(overfull) >= 5.0:
            result.append(
                "VALIDATION: обнаружено существенное переполнение "
                f"до {max(overfull):.2f} pt."
            )
        if re.search(r"Missing character:", text):
            result.append(
                "VALIDATION: журнал сообщает об отсутствующих глифах в PDF."
            )
        if re.search(r"undefined (?:references|citations)", text, re.IGNORECASE):
            result.append("VALIDATION: компилятор сообщил о неразрешённых ссылках.")
        if re.search(r"LaTeX Error:", text):
            result.append("VALIDATION: журнал содержит LaTeX Error.")
        return result

    @staticmethod
    def _pdf_info(pdf_path: Path | None) -> dict[str, Any]:
        if not pdf_path or not pdf_path.exists():
            return {"exists": False, "pages": None}
        try:
            import pymupdf

            document = pymupdf.open(pdf_path)
            pages = document.page_count
            sizes = [
                {
                    "width_pt": round(document[index].rect.width, 2),
                    "height_pt": round(document[index].rect.height, 2),
                }
                for index in range(min(3, pages))
            ]
            document.close()
            return {"exists": True, "pages": pages, "sample_page_sizes": sizes}
        except Exception:
            return {"exists": True, "pages": None}

    @staticmethod
    def _compare_count(
        warnings: list[str],
        label: str,
        source_count: int | None,
        target_count: int,
        tolerance: int = 0,
    ) -> None:
        if source_count is None or source_count <= 0:
            return
        if target_count + tolerance < source_count:
            warnings.append(
                f"VALIDATION: число {label} в ArticleIR меньше исходного: "
                f"{target_count} < {source_count}."
            )

    @staticmethod
    def _structure_score(
        source_counts: dict[str, int | None], article_counts: dict[str, int]
    ) -> float:
        ratios: list[float] = []
        mappings = [
            ("tables", "tables"),
            ("formulas", "equations"),
            ("drawings", "figures"),
        ]
        for source_key, target_key in mappings:
            source = source_counts.get(source_key)
            if source:
                target = article_counts[target_key]
                if source_key == "formulas":
                    target += article_counts["formula_images"]
                if source_key == "drawings":
                    target += article_counts["formula_images"]
                ratios.append(min(1.0, target / source))
        return round(sum(ratios) / len(ratios), 4) if ratios else 1.0

    @staticmethod
    def _pandoc_disagreements(
        package_analysis: dict[str, Any] | None,
        article_counts: dict[str, int],
    ) -> list[str]:
        if not package_analysis:
            return []
        audit = package_analysis.get("pandoc_audit") or {}
        if not audit.get("success"):
            return []
        counts = audit.get("counts") or {}
        warnings: list[str] = []
        mappings = [
            ("Table", "tables", "таблиц"),
            ("Image", "figures", "рисунков"),
            ("Math", "equations", "формул"),
        ]
        for pandoc_key, article_key, label in mappings:
            pandoc_count = int(counts.get(pandoc_key, 0))
            article_count = article_counts[article_key]
            if pandoc_count and abs(pandoc_count - article_count) > max(1, pandoc_count * 0.1):
                warnings.append(
                    "VALIDATION: собственный парсер и Pandoc расходятся по числу "
                    f"{label}: {article_count} против {pandoc_count}."
                )
        return warnings

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return set(re.findall(r"[0-9A-Za-zА-Яа-яЁё]{2,}", text.lower()))
