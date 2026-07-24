from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm

from apps.citations.analysis import analyze_claims, text_snapshot
from apps.citations.checks import build_citation_coverage_report
from apps.citations.index import build_index, search_claim
from apps.citations.rerank import _remove_weak_results
from apps.citations.workspaces import apply_to_docx, create_workspace
from apps.directory.models import ArticleType, Journal
from apps.submissions.models import SubmissionStatus
from apps.submissions.services import create_submission_with_initial_version


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
            CITATION_CHECK_MIN_TEXT_LENGTH=80,
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

    def test_submission_check_contains_exact_citation_locations(self):
        submission = SimpleNamespace(
            title="Анализ медицинских изображений",
            abstract="Классификация изображений нейронными сетями.",
        )
        snapshot = text_snapshot(
            "Введение\nСвёрточные нейронные сети широко применяются для классификации "
            "медицинских изображений и обеспечивают высокую точность распознавания."
        )

        passed, payload = build_citation_coverage_report(
            submission,
            None,
            snapshot=snapshot,
            max_claims=3,
            results_per_claim=2,
        )

        self.assertTrue(passed)
        self.assertEqual(payload["check_code"], "article_recommendations")
        self.assertEqual(payload["metrics"]["claims_needing_citation"], 1)
        self.assertEqual(payload["issues"][0]["location"], "Введение, абзац 2")
        self.assertIn("Свёрточные нейронные сети", payload["issues"][0]["context_highlight"])
        self.assertTrue(payload["citation_claims"][0]["recommendations"])

    def test_zero_percent_and_rejected_sources_are_removed(self):
        claims = [
            {
                "recommendations": [
                    {"title": "Нулевой", "score_percent": 0, "verdict": "partial"},
                    {"title": "Отклонённый", "score_percent": 87, "verdict": "not_supports"},
                    {"title": "Подходящий", "score_percent": 72, "verdict": "supports"},
                ]
            }
        ]

        filtered = _remove_weak_results(claims)

        self.assertEqual(
            [item["title"] for item in filtered[0]["recommendations"]],
            ["Подходящий"],
        )

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

    def test_added_reference_continues_word_automatic_numbering(self):
        document = Document()
        document.add_paragraph(
            "YOLO используется для обнаружения людей [1]. MediaPipe определяет точки тела [2]. "
            "VideoMAE анализирует последовательность кадров [3]. "
            "Нейронные сети применяются для анализа движений человека."
        )
        document.add_paragraph("Список использованной литературы")
        document.add_paragraph("Первый источник.", style="List Number")
        document.add_paragraph("Второй источник.", style="List Number")
        reference_prototype = document.add_paragraph("Третий источник.", style="List Number")
        reference_prototype.alignment = WD_ALIGN_PARAGRAPH.LEFT
        reference_prototype.paragraph_format.left_indent = Cm(1)
        reference_prototype.paragraph_format.first_line_indent = Cm(-1)
        reference_prototype.paragraph_format.line_spacing = 1
        source = BytesIO()
        document.save(source)
        claim = {
            "id": "claim-1",
            "text": "Нейронные сети применяются для анализа движений человека.",
            "recommendations": [
                {
                    "article_id": "1001",
                    "title": "Система компьютерного зрения",
                    "citation": "Обухов А. Д. Система компьютерного зрения. 2023.",
                }
            ],
        }
        payload = create_workspace(
            user_id=self.user.pk,
            file_bytes=source.getvalue(),
            file_name="numbered.docx",
            snapshot={"text": claim["text"]},
            claims=[claim],
            index_status={"ready": True},
        )

        output, _name = apply_to_docx(
            user_id=self.user.pk,
            token=payload["token"],
            selections=[{"claim_id": "claim-1", "article_id": "1001"}],
        )
        result = Document(output)
        reference = result.paragraphs[-1]

        self.assertEqual(reference.text, "Обухов А. Д. Система компьютерного зрения. 2023.")
        self.assertEqual(reference.style.name, "List Number")
        self.assertNotIn("[4]", reference.text)
        self.assertIn("движений человека. [4]", result.paragraphs[0].text)
        self.assertEqual(reference.alignment, WD_ALIGN_PARAGRAPH.LEFT)
        self.assertAlmostEqual(reference.paragraph_format.left_indent.cm, 1, places=2)
        self.assertAlmostEqual(reference.paragraph_format.first_line_indent.cm, -1, places=2)
        self.assertEqual(reference.paragraph_format.line_spacing, 1)

    def test_apply_source_when_docx_has_no_heading_style(self):
        document = Document()
        heading_style = document.styles["Heading 1"]
        heading_style._element.getparent().remove(heading_style._element)
        document.add_paragraph(
            "Нейронные сети используются для анализа медицинских изображений."
        )
        source = BytesIO()
        document.save(source)
        claim = {
            "id": "claim-without-heading-style",
            "text": "Нейронные сети используются для анализа медицинских изображений.",
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
            file_name="custom-styles.docx",
            snapshot={"text": claim["text"]},
            claims=[claim],
            index_status={"ready": True},
        )

        output, _name = apply_to_docx(
            user_id=self.user.pk,
            token=payload["token"],
            selections=[
                {
                    "claim_id": claim["id"],
                    "article_id": "1001",
                }
            ],
        )
        result = Document(output)
        headings = [
            paragraph.text
            for paragraph in result.paragraphs
            if paragraph.text == "Список литературы"
        ]
        self.assertEqual(headings, ["Список литературы"])

    def test_submission_source_stage_prepares_preview_and_new_version(self):
        document = Document()
        document.add_heading("Анализ медицинских изображений", level=1)
        document.add_paragraph("Иванов И. И.")
        document.add_paragraph(
            "Нейронные сети широко используются для классификации медицинских "
            "изображений и позволяют повысить точность распознавания."
        )
        source = BytesIO()
        document.save(source)
        journal = Journal.objects.create(name="Журнал RAG")
        article_type = ArticleType.objects.create(code="rag-article", name="Статья RAG")
        submission = create_submission_with_initial_version(
            author=self.user,
            title="Анализ медицинских изображений",
            abstract="Нейронные сети для классификации изображений.",
            document_authors="Иванов И. И.",
            keywords="нейронные сети; изображения",
            journal=journal,
            article_type=article_type,
            file=SimpleUploadedFile("article.docx", source.getvalue()),
            defer_checks=True,
            mark_as_checking=False,
        )
        self.client.force_login(self.user)

        page = self.client.get(
            f"{reverse('citations:workspace')}?submission={submission.pk}"
        )
        self.assertContains(page, "Распознано из документа")
        self.assertContains(page, "Иванов И. И.")

        search = self.client.post(
            reverse("citations:workspace"),
            {"submission": submission.pk, "max_claims": 3},
        )
        self.assertEqual(search.status_code, 200)
        result = search.context["result"]
        self.assertEqual(result["submission_id"], submission.pk)
        claim = next(
            item for item in result["claims"] if item.get("recommendations")
        )
        article = claim["recommendations"][0]

        prepared = self.client.post(
            reverse("citations:prepare_submission_result"),
            {
                "workspace_token": result["token"],
                "selections": (
                    f'[{{"claim_id":"{claim["id"]}",'
                    f'"article_id":"{article["article_id"]}"}}]'
                ),
            },
        )
        self.assertRedirects(
            prepared,
            reverse("citations:submission_result_preview", args=[result["token"]]),
            fetch_redirect_response=False,
        )
        preview_content = self.client.get(
            reverse("citations:submission_result_content", args=[result["token"]])
        )
        self.assertEqual(preview_content.status_code, 200)
        self.assertContains(preview_content, "Тестовый журнал. 2024. № 1")

        with patch("apps.checks.services.queue_submission_checks") as mocked_queue:
            applied = self.client.post(
                reverse("citations:use_submission_result", args=[result["token"]])
            )
        self.assertRedirects(
            applied,
            reverse("submissions:detail", args=[submission.pk]),
            fetch_redirect_response=False,
        )
        submission.refresh_from_db()
        self.assertEqual(submission.status, SubmissionStatus.DRAFT)
        self.assertEqual(submission.versions.count(), 2)
        mocked_queue.assert_called_once()
