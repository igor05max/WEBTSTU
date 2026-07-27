"""Isolated conversions required before semantic document parsing."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

from django.conf import settings


LEGACY_DOC_SIGNATURE = bytes.fromhex("d0cf11e0a1b11ae1")


class LegacyDocConversionError(ValueError):
    pass


def _libreoffice_executable():
    configured = str(getattr(settings, "LIBREOFFICE_BINARY", "") or "").strip()
    if configured and Path(configured).is_file():
        return configured
    return shutil.which("libreoffice") or shutil.which("soffice")


def _is_valid_docx(path):
    try:
        if path.stat().st_size < 100:
            return False
        with zipfile.ZipFile(path) as archive:
            return "word/document.xml" in archive.namelist()
    except (OSError, zipfile.BadZipFile):
        return False


def convert_legacy_doc_to_docx(data):
    """Convert trusted in-memory DOC bytes to DOCX in an isolated temp profile."""

    if not isinstance(data, bytes) or not data.startswith(LEGACY_DOC_SIGNATURE):
        raise LegacyDocConversionError(
            "Сигнатура файла не соответствует бинарному формату DOC."
        )
    executable = _libreoffice_executable()
    word_script = Path(__file__).with_name("convert_doc_to_docx.ps1")
    use_word = os.name == "nt" and not executable and word_script.is_file()
    if not executable and not use_word:
        raise LegacyDocConversionError(
            "Не найден LibreOffice или Microsoft Word для конвертации DOC в DOCX."
        )

    with tempfile.TemporaryDirectory(prefix="legacy-doc-analysis-") as directory:
        temporary_directory = Path(directory)
        source_path = temporary_directory / "source.doc"
        profile_directory = temporary_directory / "libreoffice-profile"
        source_path.write_bytes(data)
        converted_path = temporary_directory / "source.docx"
        if use_word:
            command = [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(word_script),
                "-SourcePath",
                str(source_path),
                "-OutputPath",
                str(converted_path),
            ]
        else:
            command = [
                executable,
                f"-env:UserInstallation={profile_directory.as_uri()}",
                "--headless",
                "--convert-to",
                "docx:Office Open XML Text",
                "--outdir",
                str(temporary_directory),
                str(source_path),
            ]
        environment = os.environ.copy()
        environment["TMP"] = str(temporary_directory)
        environment["TEMP"] = str(temporary_directory)
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                check=False,
                timeout=120,
                env=environment,
                cwd=temporary_directory,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            raise LegacyDocConversionError(
                "Конвертация DOC превысила лимит времени."
            ) from exc
        except OSError as exc:
            raise LegacyDocConversionError(
                "Не удалось запустить LibreOffice для конвертации DOC."
            ) from exc

        if result.returncode != 0 or not _is_valid_docx(converted_path):
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            detail = f" Причина: {stderr[:300]}" if stderr else ""
            raise LegacyDocConversionError(
                f"Конвертер не смог преобразовать DOC в DOCX.{detail}"
            )
        return converted_path.read_bytes()
