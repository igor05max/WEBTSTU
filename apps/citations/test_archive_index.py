import hashlib
import json
import sqlite3
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import reverse
from reportlab.pdfgen import canvas

from apps.citations.index import _fts_candidates, build_index, get_index_status, search_claim
from apps.citations.views import _attach_public_source_links


class TgtuArchiveIndexTests(SimpleTestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.archives = root / "archives"
        self.archives.mkdir()
        self.manifest = root / "manifest.jsonl"
        self.index = root / "citations.sqlite3"
        self.settings_context = override_settings(
            CITATION_ARCHIVE_ROOT=self.archives,
            CITATION_INDEX_PATH=self.index,
            CITATION_EMBEDDING_MODEL="",
        )
        self.settings_context.enable()
        self.addCleanup(self.settings_context.disable)

        output = BytesIO()
        pdf = canvas.Canvas(output)
        for position in range(8):
            pdf.drawString(
                30, 750 - position * 24,
                "Graph neural networks detect industrial equipment faults using sensor data.",
            )
        pdf.save()
        self.pdf_bytes = output.getvalue()
        self.pdf_path = "Вестник_ТГТУ/2025/Выпуск_01/001_test.pdf"
        member = "Статьи_ТГТУ_2022-2026/" + self.pdf_path
        with ZipFile(self.archives / "Vestnik_2025.zip", "w") as archive:
            archive.writestr(member, self.pdf_bytes)
        self.manifest.write_text(
            json.dumps(
                {
                    "record_id": "tgtu:test",
                    "journal": "Вестник_ТГТУ",
                    "year": 2025,
                    "issue": 1,
                    "title": "Graph neural networks for industrial fault detection",
                    "authors": "A. Researcher",
                    "doi": "10.17277/test.2025.01.pp.001-005",
                    "pages": "001-005",
                    "pdf_path": self.pdf_path,
                    "pdf_sha256": hashlib.sha256(self.pdf_bytes).hexdigest(),
                    "pdf_bytes": len(self.pdf_bytes),
                    "pdf_pages": 1,
                    "decision": "candidate",
                    "link_verification": "verified_alternate",
                    "verified_source_url": "https://example.org/test.pdf",
                    "legacy_fallback_url": "",
                },
                ensure_ascii=False,
            ) + "\n",
            encoding="utf-8",
        )
        (root / "legacy_abstracts.jsonl").write_text(
            json.dumps(
                {
                    "record_id": "tgtu:test",
                    "abstracts": [{
                        "kind": "english_abstract",
                        "text": "Нейронные сети анализируют данные датчиков для выявления отказов промышленного оборудования и прогнозирования его состояния.",
                    }],
                },
                ensure_ascii=False,
            ) + "\n",
            encoding="utf-8",
        )

    def test_archive_article_is_searchable_and_pdf_matches_indexed_file(self):
        built = build_index(
            manifest_path=self.manifest,
            archive_root=self.archives,
            index_path=self.index,
        )
        self.assertEqual(built["article_count"], "1")
        results = search_claim(
            {"text": "Нейронные сети выявляют отказы промышленного оборудования по данным датчиков", "type": "method"},
            index_path=self.index,
        )
        self.assertEqual(results[0]["article_id"], "tgtu:test")
        self.assertEqual(results[0]["chunk_kind"], "catalog_abstract")
        self.assertEqual(results[0]["url"], "/citations/sources/tgtu:test/pdf/")
        linked = _attach_public_source_links(
            RequestFactory().get("/citations/", HTTP_HOST="testserver"), results
        )
        self.assertEqual(
            linked[0]["url"], "http://testserver/citations/sources/tgtu:test/pdf/"
        )
        self.assertIn(linked[0]["url"], linked[0]["citation"])

        response = self.client.get(reverse("citations:source_pdf", args=["tgtu:test"]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), self.pdf_bytes)
        cached = self.client.get(
            reverse("citations:source_pdf", args=["tgtu:test"]),
            HTTP_IF_NONE_MATCH=response["ETag"],
        )
        self.assertEqual(cached.status_code, 304)
        with override_settings(CITATION_ARCHIVE_ROOT=self.archives / "missing"):
            self.assertFalse(get_index_status(self.index)["ready"])

    def test_fts_strength_follows_bm25_order(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute(
            "CREATE VIRTUAL TABLE chunk_fts USING fts5(chunk_id UNINDEXED, text, title, keywords, section)"
        )
        connection.executemany(
            "INSERT INTO chunk_fts VALUES(?,?,?,?,?)",
            [
                (1, "neural network neural network", "neural network methods", "", ""),
                (2, "network analysis of industrial machinery", "", "", ""),
            ],
        )
        scores = _fts_candidates(connection, "neural network", 2)
        self.assertEqual(set(scores), {1, 2})
        self.assertGreater(scores[1], scores[2])
        connection.close()
