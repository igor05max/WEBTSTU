from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from paper_formatter.config import load_semantic_settings
from paper_formatter.models import (
    EquationBlock,
    FigureBlock,
    ListItemBlock,
    ParagraphBlock,
    TableBlock,
)
from paper_formatter.pipeline import ConversionPipeline
from paper_formatter.utils.files import write_json


class TemplateBenchmarkRunner:
    """Прогоняет один логический TEX-документ по каталогу целевых шаблонов."""

    def __init__(self) -> None:
        settings = replace(load_semantic_settings(), enabled=False)
        self.pipeline = ConversionPipeline(semantic_settings=settings)

    def run(
        self,
        benchmark_dir: Path,
        output_dir: Path,
        *,
        compile_pdf: bool = True,
        reuse_existing: bool = False,
        progress: Callable[[str], None] | None = None,
    ) -> dict:
        benchmark_dir = Path(benchmark_dir).resolve()
        output_dir = Path(output_dir).resolve()
        manifest_path = benchmark_dir / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"manifest.json не найден: {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        source = benchmark_dir / "19_standard_article_control" / "article.tex"
        if not source.exists():
            raise FileNotFoundError(f"Контрольная статья не найдена: {source}")

        rows: list[dict] = []
        for item in manifest:
            slug = item["slug"]
            target = benchmark_dir / slug / "article.tex"
            run_dir = output_dir / slug
            if progress:
                progress(slug)
            try:
                if not (
                    reuse_existing
                    and (run_dir / "run.json").exists()
                    and (run_dir / "parsed" / "article_ir.json").exists()
                ):
                    result = self.pipeline.run(
                        source,
                        run_dir,
                        example=target,
                        compile_pdf=compile_pdf,
                        render_docx=False,
                    )
                    article = result.article_ir
                    status = result.run.status
                else:
                    from paper_formatter.models import ArticleIR

                    article = ArticleIR.model_validate_json(
                        (run_dir / "parsed" / "article_ir.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    run_data = json.loads(
                        (run_dir / "run.json").read_text(encoding="utf-8")
                    )
                    status = run_data["status"]
                rows.append(
                    self._row(
                        item=item,
                        benchmark_dir=benchmark_dir,
                        run_dir=run_dir,
                        article=article,
                        status=status,
                        compile_pdf=compile_pdf,
                    )
                )
            except Exception as exc:
                rows.append(
                    {
                        "slug": slug,
                        "name": item.get("name", slug),
                        "expected_class": item.get("document_class"),
                        "passed": False,
                        "error": str(exc),
                    }
                )

        report = {
            "generated_at": datetime.now().astimezone().isoformat(),
            "benchmark": str(benchmark_dir),
            "source": str(source),
            "output": str(output_dir),
            "total": len(rows),
            "passed": sum(bool(row.get("passed")) for row in rows),
            "compiled": sum(bool(row.get("pdf_exists")) for row in rows),
            "class_matches": sum(bool(row.get("class_match")) for row in rows),
            "strict_typography_passed": sum(
                bool(row.get("strict_typography_pass")) for row in rows
            ),
            "missing_glyphs": sum(int(row.get("missing_glyphs", 0)) for row in rows),
            "examples": rows,
        }
        write_json(output_dir / "benchmark_summary.json", report)
        (output_dir / "BENCHMARK_REPORT.md").write_text(
            self._markdown(report), encoding="utf-8"
        )
        return report

    def _row(
        self,
        *,
        item: dict,
        benchmark_dir: Path,
        run_dir: Path,
        article,
        status: str,
        compile_pdf: bool,
    ) -> dict:
        main_tex = run_dir / "generated" / "latex" / "main.tex"
        main_text = (
            main_tex.read_text(encoding="utf-8", errors="replace")
            if main_tex.exists()
            else ""
        )
        class_match = re.search(
            r"\\documentclass(?:\[[^\]]*\])?\{([^}]+)\}", main_text
        )
        actual_class = class_match.group(1) if class_match else None
        validation_path = run_dir / "validation" / "validation_report.json"
        validation = (
            json.loads(validation_path.read_text(encoding="utf-8"))
            if validation_path.exists()
            else {}
        )
        generated = self._log_diagnostics(
            run_dir / "generated" / "latex" / "main.log"
        )
        baseline = self._log_diagnostics(
            benchmark_dir / item["slug"] / "compile.log"
        )
        pdf_exists = (run_dir / "result" / "result.pdf").exists()
        expected_class = item.get("document_class")
        class_ok = actual_class == expected_class
        validation_errors = len(validation.get("errors", []))
        overfull_regression = max(
            0.0,
            generated["max_overfull_pt"] - baseline["max_overfull_pt"],
        )
        passed = bool(
            class_ok
            and (pdf_exists or not compile_pdf)
            and validation_errors == 0
            and generated["missing_glyphs"] == 0
            and overfull_regression < 5.0
        )
        return {
            "slug": item["slug"],
            "name": item.get("name", item["slug"]),
            "family": item.get("family"),
            "expected_class": expected_class,
            "actual_class": actual_class,
            "class_match": class_ok,
            "status": status,
            "pdf_exists": pdf_exists,
            "pages": validation.get("outputs", {}).get("pdf_pages"),
            "text_coverage": validation.get("integrity", {}).get("text_coverage"),
            "validation_errors": validation_errors,
            "structure": {
                "sections": sum(block.type == "section" for block in article.body),
                "equations": sum(
                    isinstance(block, EquationBlock) for block in article.body
                )
                + sum(
                    1
                    for block in article.body
                    if isinstance(block, (ParagraphBlock, ListItemBlock))
                    for run in block.runs
                    if run.math_latex is not None
                ),
                "figures": sum(
                    isinstance(block, FigureBlock) for block in article.body
                ),
                "tables": sum(
                    isinstance(block, TableBlock) for block in article.body
                ),
                "references": len(article.references),
            },
            "missing_glyphs": generated["missing_glyphs"],
            "max_overfull_pt": generated["max_overfull_pt"],
            "baseline_max_overfull_pt": baseline["max_overfull_pt"],
            "overfull_regression_pt": round(overfull_regression, 3),
            "underfull_boxes": generated["underfull_boxes"],
            "strict_typography_pass": bool(
                generated["missing_glyphs"] == 0
                and generated["max_overfull_pt"] < 5.0
            ),
            "passed": passed,
        }

    @staticmethod
    def _log_diagnostics(path: Path) -> dict[str, float | int]:
        text = (
            path.read_text(encoding="utf-8", errors="replace")
            if path.exists()
            else ""
        )
        values = [
            float(value)
            for value in re.findall(
                r"Overfull \\[hv]box \(([0-9.]+)pt too wide\)", text
            )
        ]
        return {
            "max_overfull_pt": round(max(values, default=0.0), 3),
            "underfull_boxes": len(re.findall(r"Underfull \\[hv]box", text)),
            "missing_glyphs": len(re.findall(r"Missing character:", text)),
        }

    @staticmethod
    def _markdown(report: dict) -> str:
        lines = [
            "# Paper Formatter template benchmark",
            "",
            f"- Passed: {report['passed']}/{report['total']}",
            f"- Compiled PDFs: {report['compiled']}/{report['total']}",
            f"- Document class matches: {report['class_matches']}/{report['total']}",
            (
                "- Strict typography (<5 pt overfull, no missing glyphs): "
                f"{report['strict_typography_passed']}/{report['total']}"
            ),
            f"- Missing glyphs: {report['missing_glyphs']}",
            "",
            "| Template | Class | PDF | Pages | Coverage | Max overfull | Baseline | Result |",
            "|---|---:|:---:|---:|---:|---:|---:|:---:|",
        ]
        for row in report["examples"]:
            coverage = row.get("text_coverage")
            coverage_text = f"{coverage:.1%}" if isinstance(coverage, float) else "-"
            lines.append(
                "| {slug} | {actual_class} | {pdf} | {pages} | {coverage} | "
                "{overfull:.2f} pt | {baseline:.2f} pt | {result} |".format(
                    slug=row["slug"],
                    actual_class=row.get("actual_class") or "-",
                    pdf="yes" if row.get("pdf_exists") else "no",
                    pages=row.get("pages") or "-",
                    coverage=coverage_text,
                    overfull=float(row.get("max_overfull_pt", 0.0)),
                    baseline=float(row.get("baseline_max_overfull_pt", 0.0)),
                    result="pass" if row.get("passed") else "fail",
                )
            )
        lines.append("")
        lines.append(
            "CAS overfull warnings are present in the supplied reference logs too; "
            "they are reported as baseline issues rather than formatter regressions."
        )
        return "\n".join(lines) + "\n"
