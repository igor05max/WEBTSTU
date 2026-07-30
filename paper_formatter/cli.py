from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.progress import track
from rich.table import Table

from paper_formatter.compiler import LatexCompiler
from paper_formatter.benchmark import TemplateBenchmarkRunner
from paper_formatter.config import load_semantic_settings
from paper_formatter.exceptions import PaperFormatterError
from paper_formatter.models import ArticleIR, TemplateProfile
from paper_formatter.pipeline import ConversionPipeline, DocxToLatexPipeline
from paper_formatter.template_analyzers import (
    DocxTemplateAnalyzer,
    LatexTemplateAnalyzer,
    PdfTemplateAnalyzer,
    RequirementsTemplateAnalyzer,
)
from paper_formatter.utils.files import write_json
from paper_formatter.validator import ConversionValidator


app = typer.Typer(
    name="paper-formatter",
    help="Проверяемое переоформление научных статей по документу-образцу.",
    no_args_is_help=True,
)
console = Console()


def _pipeline(use_ai: bool = True) -> ConversionPipeline:
    settings = replace(load_semantic_settings(), enabled=use_ai)
    return ConversionPipeline(semantic_settings=settings)


def _print_result(result) -> None:
    counts: dict[str, int] = {}
    for block in result.article_ir.body:
        counts[block.type] = counts.get(block.type, 0) + 1
        if hasattr(block, "runs"):
            counts["equation"] = counts.get("equation", 0) + sum(
                run.math_latex is not None for run in block.runs
            )
    table = Table(title="Источник / ArticleIR / шаблонные выходы")
    table.add_column("Объект")
    table.add_column("Количество", justify="right")
    for key, label in (
        ("section", "Разделы"),
        ("paragraph", "Абзацы"),
        ("list_item", "Пункты списков"),
        ("equation", "Формулы"),
        ("figure", "Рисунки"),
        ("table", "Таблицы"),
    ):
        table.add_row(label, str(counts.get(key, 0)))
    console.print(table)
    console.print(f"[green]ArticleIR:[/green] {result.run.article_ir_path}")
    console.print(f"[green]TemplateProfile:[/green] {result.run.template_profile_path}")
    console.print(f"[green]LaTeX:[/green] {result.main_tex}")
    console.print(f"[green]LaTeX ZIP:[/green] {result.latex_zip}")
    if result.docx:
        console.print(f"[green]DOCX:[/green] {result.docx}")
    if result.pdf:
        console.print(f"[green]PDF:[/green] {result.pdf}")
    if result.run.html_report_path:
        console.print(f"[green]Отчёт HTML:[/green] {result.run.html_report_path}")
    for warning in result.run.warnings:
        console.print(f"[yellow]Предупреждение:[/yellow] {warning}")
    for error in result.run.errors:
        console.print(f"[red]Ошибка проверки:[/red] {error}")


def _execute_conversion(
    source: Path,
    output: Path,
    *,
    example: Path | None,
    compile_pdf: bool,
    render_docx: bool,
    use_ai: bool,
) -> None:
    try:
        result = _pipeline(use_ai).run(
            source,
            output,
            example=example,
            compile_pdf=compile_pdf,
            render_docx=render_docx,
        )
    except PaperFormatterError as exc:
        console.print(f"[bold red]Ошибка:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        console.print(f"[bold red]Непредвиденная ошибка:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    _print_result(result)
    if result.run.status == "failed":
        raise typer.Exit(code=2)


@app.command()
def convert(
    source: Path = typer.Option(..., "--source", "-s", help="DOCX, TEX, PDF, ZIP или папка"),
    example: Optional[Path] = typer.Option(
        None,
        "--example",
        "-e",
        help="Образец DOCX, TEX, PDF, ZIP либо текст требований",
    ),
    output: Path = typer.Option(Path("runs/latest"), "--output", "-o"),
    compile_pdf: bool = typer.Option(True, "--compile/--no-compile"),
    render_docx: bool = typer.Option(True, "--docx/--no-docx"),
    use_ai: bool = typer.Option(True, "--ai/--no-ai"),
) -> None:
    """Преобразовать статью по образцу и создать LaTeX, DOCX, PDF и отчёт."""
    _execute_conversion(
        source,
        output,
        example=example,
        compile_pdf=compile_pdf,
        render_docx=render_docx,
        use_ai=use_ai,
    )


@app.command("docx-to-latex")
def docx_to_latex(
    source: Path = typer.Option(..., "--source", "-s"),
    output: Path = typer.Option(Path("runs/docx_demo"), "--output", "-o"),
    compile_pdf: bool = typer.Option(True, "--compile/--no-compile"),
    use_ai: bool = typer.Option(True, "--ai/--no-ai"),
) -> None:
    """Совместимый маршрут DOCX / ArticleIR / LaTeX."""
    _execute_conversion(
        source,
        output,
        example=None,
        compile_pdf=compile_pdf,
        render_docx=True,
        use_ai=use_ai,
    )


@app.command("inspect-source")
def inspect_source(
    input_file: Path = typer.Option(..., "--input", "-i"),
    output: Path = typer.Option(Path("runs/inspect_source"), "--output", "-o"),
    use_ai: bool = typer.Option(False, "--ai/--no-ai"),
) -> None:
    """Разобрать источник и сохранить все промежуточные структуры без PDF."""
    _execute_conversion(
        input_file,
        output,
        example=None,
        compile_pdf=False,
        render_docx=False,
        use_ai=use_ai,
    )


@app.command("inspect-template")
def inspect_template(
    input_file: Path = typer.Option(..., "--input", "-i"),
    output: Path = typer.Option(Path("template_profile.json"), "--output", "-o"),
) -> None:
    """Построить TemplateProfile по одному распакованному образцу."""
    if not input_file.exists():
        console.print("[red]Файл не найден.[/red]")
        raise typer.Exit(code=1)
    suffix = input_file.suffix.lower()
    analyzers = {
        ".docx": DocxTemplateAnalyzer,
        ".tex": LatexTemplateAnalyzer,
        ".pdf": PdfTemplateAnalyzer,
        ".txt": RequirementsTemplateAnalyzer,
        ".md": RequirementsTemplateAnalyzer,
    }
    analyzer = analyzers.get(suffix)
    if analyzer is None:
        console.print("[red]Для inspect-template укажите DOCX, TEX, PDF, TXT или MD.[/red]")
        raise typer.Exit(code=1)
    try:
        profile = analyzer().analyze(input_file)
    except Exception as exc:
        console.print(f"[red]Ошибка анализа образца:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    write_json(output, profile.model_dump(mode="json"))
    console.print(f"[green]TemplateProfile:[/green] {output.resolve()}")


@app.command("compile")
def compile_project(
    project: Path = typer.Option(..., "--project", "-p"),
    log: Optional[Path] = typer.Option(None, "--log"),
) -> None:
    """Скомпилировать готовый LaTeX-проект доступным движком."""
    log_path = log or project / "compile.log"
    pdf, warnings = LatexCompiler().compile(project, log_path)
    for warning in warnings:
        console.print(f"[yellow]{warning}[/yellow]")
    if not pdf:
        raise typer.Exit(code=1)
    console.print(f"[green]PDF:[/green] {pdf}")


@app.command("validate")
def validate_run(
    run: Path = typer.Option(..., "--run", "-r", help="Папка результата"),
) -> None:
    """Повторно выполнить статическую проверку существующего результата."""
    run_data = json.loads((run / "run.json").read_text(encoding="utf-8"))
    article = ArticleIR.model_validate_json(
        (run / "parsed" / "article_ir.json").read_text(encoding="utf-8")
    )
    profile_path = run / "parsed" / "template_profile.json"
    profile = (
        TemplateProfile.model_validate_json(profile_path.read_text(encoding="utf-8"))
        if profile_path.exists()
        else TemplateProfile()
    )
    report = ConversionValidator().validate(
        source_path=Path(run_data["source_path"]),
        article=article,
        main_tex=run / "generated" / "latex" / "main.tex",
        pdf_path=(
            run / "result" / "result.pdf"
            if (run / "result" / "result.pdf").exists()
            else None
        ),
        docx_path=(
            run / "result" / "result.docx"
            if (run / "result" / "result.docx").exists()
            else None
        ),
        compile_log=run / "validation" / "compile.log",
        template_profile=profile,
    )
    target = run / "validation" / "validation_report.json"
    write_json(target, report)
    console.print(f"[green]Отчёт:[/green] {target}")
    for error in report["errors"]:
        console.print(f"[red]{error}[/red]")
    for warning in report["warnings"]:
        console.print(f"[yellow]{warning}[/yellow]")


@app.command("test-corpus")
def test_corpus(
    corpus: Path = typer.Option(..., "--corpus", "-c"),
    output: Path = typer.Option(Path("runs/corpus"), "--output", "-o"),
    compile_pdf: bool = typer.Option(False, "--compile/--no-compile"),
) -> None:
    """Прогнать каталог реальных документов и собрать сводку."""
    inputs = sorted(
        path
        for path in corpus.rglob("*")
        if path.is_file() and path.suffix.lower() in {".docx", ".tex", ".pdf", ".zip"}
    )
    summary: list[dict] = []
    for source in track(inputs, description="Проверка корпуса"):
        target = output / source.stem
        try:
            result = _pipeline(False).run(
                source,
                target,
                compile_pdf=compile_pdf,
                render_docx=False,
            )
            summary.append(
                {
                    "source": str(source),
                    "status": result.run.status,
                    "warnings": len(result.run.warnings),
                    "errors": len(result.run.errors),
                }
            )
        except Exception as exc:
            summary.append(
                {"source": str(source), "status": "failed", "error": str(exc)}
            )
    write_json(output / "corpus_summary.json", summary)
    failed = sum(item["status"] == "failed" for item in summary)
    console.print(f"Обработано: {len(summary)}, ошибок: {failed}")
    if failed:
        raise typer.Exit(code=2)


@app.command("benchmark-templates")
def benchmark_templates(
    benchmark: Path = typer.Option(..., "--benchmark", "-b"),
    output: Path = typer.Option(Path("runs/benchmark"), "--output", "-o"),
    compile_pdf: bool = typer.Option(True, "--compile/--no-compile"),
    reuse_existing: bool = typer.Option(False, "--reuse/--no-reuse"),
) -> None:
    """Прогнать одну статью по всем шаблонам контролируемого benchmark."""
    runner = TemplateBenchmarkRunner()
    report = runner.run(
        benchmark,
        output,
        compile_pdf=compile_pdf,
        reuse_existing=reuse_existing,
        progress=lambda slug: console.print(f"[cyan]Benchmark:[/cyan] {slug}"),
    )
    console.print(
        f"[green]Пройдено:[/green] {report['passed']}/{report['total']}; "
        f"PDF: {report['compiled']}/{report['total']}; "
        f"классы: {report['class_matches']}/{report['total']}"
    )
    console.print(
        f"[green]Отчёт:[/green] {(output.resolve() / 'BENCHMARK_REPORT.md')}"
    )
    if report["passed"] != report["total"]:
        raise typer.Exit(code=2)
