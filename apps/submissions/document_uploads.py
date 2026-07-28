"""Prepare uploaded documents for checks and safe template-based editing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from django.core.files.base import ContentFile

from apps.submissions.document_analysis import read_file_bytes
from apps.submissions.document_conversion import convert_legacy_doc_to_docx


@dataclass
class PreparedDocumentUpload:
    working_file: object
    source_file: object | None = None
    converted_from_legacy_doc: bool = False


def prepare_document_upload(uploaded_file):
    """
    Keep a legacy DOC as the source and produce an editable DOCX working copy.

    Non-DOC uploads pass through untouched.  ContentFile instances are used for
    converted uploads so both files remain seekable after form processing.
    """

    if isinstance(uploaded_file, PreparedDocumentUpload):
        return uploaded_file
    suffix = Path(getattr(uploaded_file, "name", "") or "").suffix.casefold()
    if suffix != ".doc":
        return PreparedDocumentUpload(working_file=uploaded_file)

    source_bytes = read_file_bytes(uploaded_file)
    converted_bytes = convert_legacy_doc_to_docx(source_bytes)
    source_name = Path(uploaded_file.name).name
    working_name = f"{Path(source_name).stem}.docx"
    return PreparedDocumentUpload(
        working_file=ContentFile(converted_bytes, name=working_name),
        source_file=ContentFile(source_bytes, name=source_name),
        converted_from_legacy_doc=True,
    )
