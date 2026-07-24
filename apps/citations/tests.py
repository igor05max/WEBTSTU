from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from docx import Document

from apps.citations.analysis import analyze_claims, text_snapshot
from apps.citations.index import build_index, search_claim
from apps.citations.workspaces import apply_to_docx, create_workspace


class CitationSystemTests(TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.corpus_root = root / "corpus"
        self.corpus_root.mkdir()
        self.index_path = root / "citation.sqlite3"
        self.workspace_root = root / "workspaces"
        self.settings_override = override_settings(
            CITATION_CORPUS_ROOT=self.corpus_root,
            CITATION_INDEX_PATH=self.index_path,
            CITATION_WORKSPACE_ROOT=self.workspace_root,
            CITATION_INDEX_AUTO_BUILD=True,
            CITATION_LLM_ANALYSIS_ENABLED=False,
            CITATION_LLM_RERANK_ENABLED=False,
            CITATION_EMBEDDING_MODEL="",
        )
        self.settings_override.enable()
        self._build_corpus()
        build_index(corpus_root=self.corpus_root, index_path=self.index_path)
        self.user = get_user_model().objects.create_user(
            username="citation_author",
            password="1234",
        )

    def tearDown(self):
        self.settings_override.disable()
        self.temp_dir.cleanup()

    def _build_corpus(self):
        (self.corpus_root / "journal_articles_full_metadata.csv").write_text(
            "\n".join(
                [
                    "year;issue_display_name;section;article_id;title;authors;journal;article_year;pages;doi;edn;article_url;citation_elibrary",
                    "2024;Т. 1 № 1;Информатика;1001;НЕЙРОННЫЕ СЕТИ ДЛЯ АНАЛИЗА ИЗОБРАЖЕНИЙ;Иванов И.И.;Тестовый журнал;2024;1-9;10.1000/test.1;ABCDEF;https://example.test/1;Иванов И. И. Нейронные сети для анализа изображений // Тестовый журнал. 2024. № 1. С. 1-9.",
                    "2023;Т. 2 № 1;Химия;1002;ЭКСТРАКЦИЯ РАСТИТЕЛЬНОГО СЫРЬЯ;Петров П.П.;Тестовый журнал;2023;10-20;;;https://example.test/2;Петров П. П. Экстракция растительного сырья // Тестовый журнал. 2023. № 1. С. 10-20.",
                ]
            ),
            encoding="utf-8",
        )
        issue = self.corpus_root / "2024"
        issue.mkdir()
        (issue / "1001 - article.html").write_text(
            """
            <div id="abstract1">Свёрточные нейронные сети применяются для классификации
            медицинских изображений и повышают точность распознавания.</div>
            <p>Ключевые слова: нейронные сети; классификация; изображения</p>
            """,
            encoding="utf-8",
        )
        (issue / "1002 - article.html").write_text(
            '<div id="abstract1">Описан метод экстракции веществ из растений.</div>',
            encoding="utf-8",
        )

    def test_claim_analysis_and_hybrid_search(self):
        snapshot = text_snapshot(
            "Введение\nСвёрточные нейронные сети широко применяются для классификации "
            "медицинских изображений и обеспечивают высокую точность распознавания."
        )
        analysis = analyze_claims(snapshot, max_claims=3)

        self.assertEqual(len(analysis["claims"]), 1)
        self.assertEqual(analysis["claims"][0]["type"], "method")
        results = search_claim(analysis["claims"][0], limit=2)
        self.assertEqual(results[0]["article_id"], "1001")
        self.assertEqual(results[0]["doi"], "10.1000/test.1")
        self.assertTrue(results[0]["evidence"])

    def test_workspace_page_returns_claims_and_real_metadata(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("citations:workspace"),
            {
                "text": (
                    "Нейронные сети широко используются для классификации медицинских "
                    "изображений и позволяют повысить точность распознавания."
                ),
                "max_claims": 3,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "НЕЙРОННЫЕ СЕТИ ДЛЯ АНАЛИЗА ИЗОБРАЖЕНИЙ")
        self.assertContains(response, "10.1000/test.1")
        self.assertContains(response, "Почему рекомендуется")

    def test_apply_selected_source_to_docx(self):
        document = Document()
        document.add_paragraph(
            "Нейронные сети широко используются для анализа медицинских изображений. "
            "Следующее предложение должно остаться после маркера."
        )
        source = BytesIO()
        document.save(source)
        claim = {
            "id": "claim-1",
            "text": "Нейронные сети широко используются для анализа медицинских изображений.",
            "recommendations": [
                {
                    "article_id": "1001",
                    "title": "Нейронные сети для анализа изображений",
                    "citation": "Иванов И. И. Нейронные сети для анализа изображений. 2024.",
                }
            ],
        }
        payload = create_workspace(
            user_id=self.user.pk,
            file_bytes=source.getvalue(),
            file_name="article.docx",
            snapshot={"text": claim["text"]},
            claims=[claim],
            index_status={"ready": True},
        )

        output, name = apply_to_docx(
            user_id=self.user.pk,
            token=payload["token"],
            selections=[{"claim_id": "claim-1", "article_id": "1001"}],
        )
        result = Document(output)
        text = "\n".join(paragraph.text for paragraph in result.paragraphs)

        self.assertEqual(name, "article_with_citations.docx")
        self.assertIn(
            "медицинских изображений. [1] Следующее предложение",
            text,
        )
        self.assertIn("Список литературы", text)
        self.assertIn("Иванов И. И.", text)
