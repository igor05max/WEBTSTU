from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import typer
from rich.console import Console

from paper_formatter.config import load_semantic_settings
from paper_formatter.exceptions import PaperFormatterError, UnsupportedInputError
from paper_formatter.pipeline import DocxToLatexPipeline

console = Console()


def _default_output(source: Path) -> Path:
    """Создаёт понятную папку результата рядом с исходным документом."""
    return source.parent / f"{source.stem}_latex_result"


def _convert(source: Path, compile_pdf: bool = True, use_ai: bool = True) -> None:
    source = source.expanduser().resolve()

    if not source.exists():
        console.print(f"[bold red]Ошибка:[/bold red] файл не найден: {source}")
        raise typer.Exit(code=1)
    if not source.is_file():
        console.print(f"[bold red]Ошибка:[/bold red] указан не файл: {source}")
        raise typer.Exit(code=1)

    suffix = source.suffix.lower()
    output = _default_output(source)

    try:
        if suffix == ".docx":
            settings = replace(load_semantic_settings(), enabled=use_ai)
            result = DocxToLatexPipeline(semantic_settings=settings).run(
                source=source,
                output=output,
                compile_pdf=compile_pdf,
            )
        else:
            raise UnsupportedInputError(
                f"Формат {suffix or 'без расширения'} пока не поддерживается. "
                "Сейчас доступно преобразование DOCX в LaTeX."
            )
    except PaperFormatterError as exc:
        console.print(f"[bold red]Ошибка:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        console.print(f"[bold red]Непредвиденная ошибка:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print("[bold green]Преобразование завершено.[/bold green]")
    console.print(f"[green]Папка результата:[/green] {output}")
    console.print(f"[green]LaTeX:[/green] {result.main_tex}")
    console.print(f"[green]LaTeX ZIP:[/green] {result.latex_zip}")
    if result.pdf is not None:
        console.print(f"[green]PDF:[/green] {result.pdf}")
    else:
        console.print(
            "[yellow]PDF не создан, но LaTeX-проект готов. "
            "Проверьте наличие latexmk и XeLaTeX.[/yellow]"
        )
    for warning in result.run.warnings:
        console.print(f"[yellow]Предупреждение:[/yellow] {warning}")


def main(
    source: Path = typer.Argument(
        ...,
        help="Путь к исходному файлу. Формат определяется автоматически.",
    ),
    no_compile: bool = typer.Option(
        False,
        "--no-compile",
        help="Не собирать PDF, создать только LaTeX-проект.",
    ),
    no_ai: bool = typer.Option(
        False,
        "--no-ai",
        help="Выполнить полностью локальное детерминированное преобразование.",
    ),
) -> None:
    """Преобразовать файл в LaTeX, указав только путь к нему."""
    _convert(source, compile_pdf=not no_compile, use_ai=not no_ai)


def app() -> None:
    typer.run(main)
