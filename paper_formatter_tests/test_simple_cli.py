from pathlib import Path

import typer
from typer.testing import CliRunner

from paper_formatter.simple_cli import main


def _test_app() -> typer.Typer:
    app = typer.Typer(add_completion=False)
    app.command()(main)
    return app


def test_help() -> None:
    result = CliRunner().invoke(_test_app(), ["--help"])
    assert result.exit_code == 0
    assert "Путь к исходному файлу" in result.stdout


def test_missing_file() -> None:
    result = CliRunner().invoke(_test_app(), [str(Path("missing.docx"))])
    assert result.exit_code == 1
    assert "файл не найден" in result.stdout.lower()
