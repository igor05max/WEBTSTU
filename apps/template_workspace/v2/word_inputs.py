"""Word package preparation shared by the web worker and diagnostic CLI."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from apps.submissions.document_conversion import convert_legacy_doc_to_docx


def prepare_word_file(source: Path, output: Path) -> Path:
    suffix = source.suffix.casefold()
    if suffix == '.docx':
        return source
    if suffix not in {'.doc', '.dotx'}:
        raise ValueError('V2 accepts DOCX, DOC or DOTX files.')
    output.parent.mkdir(parents=True, exist_ok=True)
    if suffix == '.doc':
        output.write_bytes(convert_legacy_doc_to_docx(source.read_bytes()))
    else:
        # DOTX is already OOXML: do not round-trip it through a renderer.
        with ZipFile(source) as src, ZipFile(output, 'w', ZIP_DEFLATED) as dst:
            for item in src.infolist():
                payload = src.read(item.filename)
                if item.filename == '[Content_Types].xml':
                    payload = payload.replace(
                        b'application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml',
                        b'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml')
                dst.writestr(item, payload)
    return output
