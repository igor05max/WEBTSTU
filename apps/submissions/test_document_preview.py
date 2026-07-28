import io
from pathlib import Path
import tempfile
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from django.test import SimpleTestCase, override_settings

from apps.submissions.document_preview import (
    DocumentPreviewError,
    _build_formula_fallback_docx,
    _build_preview_safe_docx,
    _stable_docx_digest,
    build_docx_bytes_pdf,
)


DOCUMENT_XML = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document
 xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:o="urn:schemas-microsoft-com:office:office"
 xmlns:v="urn:schemas-microsoft-com:vml"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <w:body><w:p><w:r><w:object>
  <v:shape o:ole=""><v:imagedata r:id="rIdImage"/></v:shape>
  <o:OLEObject Type="Embed" ProgID="Equation.DSMT4" r:id="rIdOle"/>
 </w:object></w:r></w:p></w:body>
</w:document>"""

RELATIONSHIPS_XML = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rIdImage"
  Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
  Target="media/equation.wmf"/>
 <Relationship Id="rIdOle"
  Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject"
  Target="embeddings/equation.bin"/>
</Relationships>"""

CONTENT_TYPES_XML = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Override PartName="/word/document.xml"
  ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
 <Override PartName="/word/embeddings/equation.bin"
  ContentType="application/vnd.openxmlformats-officedocument.oleObject"/>
</Types>"""

FORMULA_XML = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document
 xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">
 <w:body>
  <w:p><m:oMath><m:r><m:t>x</m:t></m:r><m:sSup>
   <m:e><m:r><m:t>2</m:t></m:r></m:e>
   <m:sup><m:r><m:t>3</m:t></m:r></m:sup>
  </m:sSup></m:oMath></w:p>
 </w:body>
</w:document>"""


def _ole_docx():
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", DOCUMENT_XML)
        archive.writestr("word/_rels/document.xml.rels", RELATIONSHIPS_XML)
        archive.writestr("[Content_Types].xml", CONTENT_TYPES_XML)
        archive.writestr("word/media/equation.wmf", b"preview-image")
        archive.writestr("word/embeddings/equation.bin", b"ole-payload")
    return output.getvalue()


def _formula_docx():
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", FORMULA_XML)
    return output.getvalue()


class CorrectedDocumentPreviewTests(SimpleTestCase):
    def test_stable_digest_ignores_zip_timestamps(self):
        def build_archive(timestamp):
            output = io.BytesIO()
            with ZipFile(output, "w", ZIP_DEFLATED) as archive:
                info = ZipInfo("word/document.xml", date_time=timestamp)
                archive.writestr(info, FORMULA_XML)
            return output.getvalue()

        first = build_archive((2025, 1, 1, 0, 0, 0))
        second = build_archive((2026, 7, 27, 12, 0, 0))

        self.assertNotEqual(first, second)
        self.assertEqual(_stable_docx_digest(first), _stable_docx_digest(second))

    def test_preview_copy_keeps_equation_image_and_neutralizes_ole_payload(self):
        result = _build_preview_safe_docx(_ole_docx())

        with ZipFile(io.BytesIO(result)) as archive:
            names = set(archive.namelist())
            document_xml = archive.read("word/document.xml")
            relationships_xml = archive.read("word/_rels/document.xml.rels")
            content_types_xml = archive.read("[Content_Types].xml")
            ole_payload = archive.read("word/embeddings/equation.bin")

        self.assertIn("word/media/equation.wmf", names)
        self.assertIn("word/embeddings/equation.bin", names)
        self.assertEqual(ole_payload, b"")
        self.assertNotIn(b"OLEObject", document_xml)
        self.assertIn(b"rIdOle", relationships_xml)
        self.assertIn(b"rIdImage", relationships_xml)
        self.assertIn(b"word/embeddings/equation.bin", content_types_xml)

    def test_formula_fallback_keeps_equation_text(self):
        result = _build_formula_fallback_docx(_formula_docx())

        with ZipFile(io.BytesIO(result)) as archive:
            document_xml = archive.read("word/document.xml")

        self.assertNotIn(b"oMath", document_xml)
        self.assertIn(b"x23", document_xml)

    def test_pdf_conversion_uses_preview_safe_copy_and_caches_result(self):
        converted_sources = []

        def fake_conversion(source_path, output_path, **_kwargs):
            converted_sources.append(Path(source_path).read_bytes())
            Path(output_path).write_bytes(b"%PDF-1.4\n" + (b"0" * 120))

        with tempfile.TemporaryDirectory() as media_root, override_settings(
            MEDIA_ROOT=media_root
        ), patch(
            "apps.submissions.document_preview.convert_word_path_to_pdf",
            side_effect=fake_conversion,
        ):
            first = build_docx_bytes_pdf(_ole_docx())
            second = build_docx_bytes_pdf(_ole_docx())

        self.assertEqual(first, second)
        self.assertEqual(len(converted_sources), 1)
        with ZipFile(io.BytesIO(converted_sources[0])) as archive:
            self.assertEqual(
                archive.read("word/embeddings/equation.bin"),
                b"",
            )

    def test_pdf_conversion_retries_with_linear_formula_fallback(self):
        converted_sources = []

        def fake_conversion(source_path, output_path, **_kwargs):
            converted_sources.append(Path(source_path).read_bytes())
            if len(converted_sources) == 1:
                raise DocumentPreviewError("LibreOffice failed")
            Path(output_path).write_bytes(b"%PDF-1.4\n" + (b"0" * 120))

        with tempfile.TemporaryDirectory() as media_root, override_settings(
            MEDIA_ROOT=media_root
        ), patch(
            "apps.submissions.document_preview.convert_word_path_to_pdf",
            side_effect=fake_conversion,
        ):
            result = build_docx_bytes_pdf(_formula_docx())

        self.assertTrue(result.startswith(b"%PDF-"))
        self.assertEqual(len(converted_sources), 2)
        with ZipFile(io.BytesIO(converted_sources[1])) as archive:
            document_xml = archive.read("word/document.xml")
        self.assertNotIn(b"oMath", document_xml)
        self.assertIn(b"x23", document_xml)
