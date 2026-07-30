from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from paper_formatter.exceptions import PackageSecurityError
from paper_formatter.package_analyzer import PackageAnalyzer


def test_zip_selects_main_and_reports_missing_dependency(tmp_path: Path) -> None:
    archive_path = tmp_path / "article.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "main.tex",
            r"\documentclass{article}\begin{document}"
            r"\input{section}\includegraphics{missing.png}\end{document}",
        )
        archive.writestr("section.tex", "Текст")

    analysis = PackageAnalyzer().analyze(archive_path)

    assert analysis.main_document == "main.tex"
    assert "section.tex" in analysis.dependencies
    assert "missing.png" in analysis.missing_dependencies


def test_zip_rejects_path_traversal(tmp_path: Path) -> None:
    archive_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../escape.tex", "unsafe")

    with pytest.raises(PackageSecurityError):
        PackageAnalyzer().analyze(archive_path)
