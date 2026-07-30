from typer.testing import CliRunner

from paper_formatter.cli import app


def test_help() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "docx-to-latex" in result.stdout
