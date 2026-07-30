from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from paper_formatter.compiler import LatexCompiler
from paper_formatter.config import SemanticSettings, load_semantic_settings
from paper_formatter.exceptions import UnsupportedInputError
from paper_formatter.models import (
    ArticleIR,
    ConversionResult,
    ConversionRun,
    PackageAnalysis,
    TemplateProfile,
)
from paper_formatter.package_analyzer import PackageAnalyzer
from paper_formatter.parsers.docx_parser import DocxParser
from paper_formatter.parsers.latex_parser import LatexParser
from paper_formatter.parsers.pdf_parser import PdfParser
from paper_formatter.parsers.pandoc_adapter import PandocAdapter
from paper_formatter.renderers.docx_renderer import DocxRenderer
from paper_formatter.renderers.latex_renderer import LatexRenderer
from paper_formatter.report import ReportBuilder
from paper_formatter.semantic.classifier import HybridSemanticClassifier
from paper_formatter.semantic.models import SemanticAnalysis, SemanticBlock
from paper_formatter.template_analyzers import (
    DocxTemplateAnalyzer,
    LatexTemplateAnalyzer,
    PdfTemplateAnalyzer,
    RequirementsTemplateAnalyzer,
)
from paper_formatter.utils.files import prepare_run_directory, write_json
from paper_formatter.validator import ConversionValidator


class ConversionPipeline:
    def __init__(self, semantic_settings: SemanticSettings | None = None) -> None:
        self.semantic_settings = semantic_settings or load_semantic_settings()
        self.package_analyzer = PackageAnalyzer()

    def run(
        self,
        source: Path,
        output: Path,
        *,
        example: Path | None = None,
        compile_pdf: bool = True,
        render_docx: bool = True,
    ) -> ConversionResult:
        source = Path(source).resolve()
        output = Path(output).resolve()
        example = Path(example).resolve() if example else None
        prepare_run_directory(output)
        paths = self._directories(output)
        now = datetime.now().astimezone()
        run = ConversionRun(
            run_id=output.name,
            status="running",
            current_stage="analyze_source",
            source_path=str(source),
            source_type=source.suffix.lower().lstrip(".") or "package",
            output_path=str(output),
            created_at=now,
            updated_at=now,
        )
        run_path = output / "run.json"
        self._save_run(run_path, run)

        try:
            source_analysis, working_source = self._prepare_input(
                source,
                paths["input"] / "source",
                paths["input"] / "source_package",
            )
            write_json(
                paths["parsed"] / "source_package.json",
                source_analysis.model_dump(mode="json"),
            )
            run.source_type = source_analysis.document_type or run.source_type
            run.current_stage = "parse_source"
            self._touch_run(run_path, run)

            article, semantic_blocks, semantic_analysis = self._parse_source(
                working_source,
                paths["project"] / "assets",
                paths["semantic"],
            )
            pandoc_audit: dict | None = None
            if PandocAdapter.available() and working_source.suffix.lower() in {".docx", ".tex"}:
                pandoc_audit = PandocAdapter().analyze(
                    working_source,
                    paths["parsed"] / "pandoc_media",
                )
                write_json(paths["parsed"] / "pandoc_audit.json", pandoc_audit)
            article_path = paths["parsed"] / "article_ir.json"
            write_json(article_path, article.model_dump(mode="json"))
            write_json(
                paths["semantic"] / "block_candidates.json",
                [block.model_dump(mode="json") for block in semantic_blocks],
            )
            if semantic_analysis is not None:
                write_json(
                    paths["semantic"] / "semantic_analysis.json",
                    semantic_analysis.model_dump(mode="json"),
                )
            run.article_ir_path = str(article_path)
            run.warnings.extend(article.warnings)

            run.current_stage = "analyze_template"
            self._touch_run(run_path, run)
            profile, example_analysis = self._template_profile(
                example,
                paths["input"] / "example",
                paths["input"] / "example_package",
            )
            profile_path = paths["parsed"] / "template_profile.json"
            write_json(profile_path, profile.model_dump(mode="json"))
            run.template_profile_path = str(profile_path)
            run.warnings.extend(profile.warnings)
            if example_analysis:
                write_json(
                    paths["parsed"] / "example_package.json",
                    example_analysis.model_dump(mode="json"),
                )

            run.current_stage = "render_outputs"
            self._touch_run(run_path, run)
            main_tex = LatexRenderer().render(article, paths["project"], profile)
            run.latex_main_path = str(main_tex)
            docx_result: Path | None = None
            if render_docx:
                docx_renderer = DocxRenderer()
                docx_result = docx_renderer.render(
                    article,
                    paths["result"] / "result.docx",
                    profile=profile,
                    asset_root=paths["project"],
                )
                run.warnings.extend(docx_renderer.warnings)
                run.docx_path = str(docx_result)

            latex_zip_base = paths["result"] / "latex_project"
            latex_zip = Path(
                shutil.make_archive(
                    str(latex_zip_base),
                    "zip",
                    root_dir=paths["project"],
                )
            )
            run.latex_zip_path = str(latex_zip)

            pdf_result: Path | None = None
            compile_log = paths["validation"] / "compile.log"
            if compile_pdf:
                run.current_stage = "compile_pdf"
                self._touch_run(run_path, run)
                compiled_pdf, compile_warnings = LatexCompiler().compile(
                    paths["project"],
                    compile_log,
                )
                run.warnings.extend(compile_warnings)
                if compiled_pdf is not None:
                    pdf_result = paths["result"] / "result.pdf"
                    shutil.copy2(compiled_pdf, pdf_result)
                    run.pdf_path = str(pdf_result)

            run.current_stage = "validate"
            self._touch_run(run_path, run)
            package_payload = source_analysis.model_dump(mode="json")
            if pandoc_audit is not None:
                package_payload["pandoc_audit"] = pandoc_audit
            validation_report = ConversionValidator().validate(
                source_path=working_source,
                article=article,
                main_tex=main_tex,
                pdf_path=pdf_result,
                docx_path=docx_result,
                compile_log=compile_log if compile_log.exists() else None,
                semantic_blocks=semantic_blocks,
                semantic_analysis=semantic_analysis,
                template_profile=profile,
                package_analysis=package_payload,
            )
            validation_path = paths["validation"] / "validation_report.json"
            write_json(validation_path, validation_report)
            run.validation_report_path = str(validation_path)
            run.warnings.extend(validation_report.get("warnings", []))
            run.errors.extend(validation_report.get("errors", []))

            report_json_path = paths["result"] / "conversion_report.json"
            write_json(report_json_path, validation_report)
            html_path = ReportBuilder().render_html(
                validation_report,
                paths["result"] / "conversion_report.html",
                article=article,
                profile=profile,
                semantic_blocks=semantic_blocks,
                semantic_analysis=semantic_analysis,
            )
            run.html_report_path = str(html_path)

            run.current_stage = "completed"
            if run.errors:
                run.status = "failed"
            elif run.warnings:
                run.status = "completed_with_warnings"
            else:
                run.status = "completed"
            run.updated_at = datetime.now().astimezone()
            run.warnings = list(dict.fromkeys(run.warnings))
            run.errors = list(dict.fromkeys(run.errors))
            self._save_run(run_path, run)
            write_json(paths["result"] / "warnings.json", run.warnings)
            return ConversionResult(
                run=run,
                article_ir=article,
                template_profile=profile,
                main_tex=main_tex,
                latex_zip=latex_zip,
                docx=docx_result,
                pdf=pdf_result,
            )
        except Exception as exc:
            run.status = "failed"
            run.current_stage = "failed"
            run.updated_at = datetime.now().astimezone()
            run.errors.append(f"{type(exc).__name__}: {exc}")
            self._save_run(run_path, run)
            write_json(paths["result"] / "warnings.json", run.warnings)
            raise

    @staticmethod
    def _directories(output: Path) -> dict[str, Path]:
        result = {
            "input": output / "input",
            "parsed": output / "parsed",
            "semantic": output / "parsed" / "semantic",
            "project": output / "generated" / "latex",
            "validation": output / "validation",
            "result": output / "result",
        }
        for directory in result.values():
            directory.mkdir(parents=True, exist_ok=True)
        return result

    def _prepare_input(
        self,
        source: Path,
        file_dir: Path,
        package_dir: Path,
    ) -> tuple[PackageAnalysis, Path]:
        analysis = self.package_analyzer.analyze(source)
        if analysis.source_type == "zip":
            self.package_analyzer.extract(source, package_dir)
            main = self.package_analyzer.resolve_main_path(analysis, package_dir)
            if main is None or not main.exists():
                raise UnsupportedInputError("В ZIP не найден поддерживаемый главный документ.")
            return analysis, main
        if analysis.source_type == "directory":
            main = self.package_analyzer.resolve_main_path(analysis, source)
            if main is None or not main.exists():
                raise UnsupportedInputError("В папке не найден поддерживаемый главный документ.")
            return analysis, main
        file_dir.mkdir(parents=True, exist_ok=True)
        copied = file_dir / source.name
        shutil.copy2(source, copied)
        if source.suffix.lower() == ".tex":
            # TEX разбирается по исходному пути, чтобы сохранить доступ к соседним input/assets.
            return analysis, source
        return analysis, copied

    def _parse_source(
        self,
        source: Path,
        assets_dir: Path,
        semantic_dir: Path,
    ) -> tuple[ArticleIR, list[SemanticBlock], SemanticAnalysis | None]:
        suffix = source.suffix.lower()
        if suffix == ".docx":
            classifier = HybridSemanticClassifier(
                self.semantic_settings,
                cache_dir=semantic_dir / "cache",
            )
            parser = DocxParser(
                source,
                assets_dir,
                semantic_classifier=classifier,
            )
            article = parser.parse()
            return article, parser.semantic_blocks, parser.semantic_analysis
        if suffix == ".tex":
            return LatexParser(source, assets_dir).parse(), [], None
        if suffix == ".pdf":
            return PdfParser(source, assets_dir).parse(), [], None
        raise UnsupportedInputError(
            f"Формат {suffix or 'без расширения'} пока не поддерживается."
        )

    def _template_profile(
        self,
        example: Path | None,
        file_dir: Path,
        package_dir: Path,
    ) -> tuple[TemplateProfile, PackageAnalysis | None]:
        if example is None:
            return TemplateProfile(), None
        analysis, working = self._prepare_input(example, file_dir, package_dir)
        suffix = working.suffix.lower()
        if suffix == ".docx":
            profile = DocxTemplateAnalyzer().analyze(working)
        elif suffix == ".tex":
            profile = LatexTemplateAnalyzer().analyze(working)
        elif suffix == ".pdf":
            profile = PdfTemplateAnalyzer().analyze(working)
        elif suffix in {".txt", ".md"}:
            profile = RequirementsTemplateAnalyzer().analyze(working)
        else:
            raise UnsupportedInputError(
                f"Формат образца {suffix or 'без расширения'} не поддерживается."
            )
        return profile, analysis

    def _touch_run(self, path: Path, run: ConversionRun) -> None:
        run.updated_at = datetime.now().astimezone()
        self._save_run(path, run)

    @staticmethod
    def _save_run(path: Path, run: ConversionRun) -> None:
        write_json(path, run.model_dump(mode="json"))


class DocxToLatexPipeline(ConversionPipeline):
    """Совместимая оболочка прежнего DOCX-маршрута."""

    def run(
        self,
        source: Path,
        output: Path,
        compile_pdf: bool = True,
    ) -> ConversionResult:
        source = Path(source)
        if source.suffix.lower() != ".docx":
            raise UnsupportedInputError("DocxToLatexPipeline принимает только DOCX.")
        return super().run(
            source,
            output,
            example=None,
            compile_pdf=compile_pdf,
            render_docx=True,
        )
