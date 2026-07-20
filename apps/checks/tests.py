from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.checks.recommendations import recommend_articles
from apps.directory.models import ArticleType, Journal
from apps.submissions.services import create_submission_with_initial_version


class ArticleRecommendationTests(TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.corpus_root = Path(self.temp_dir.name)
        self.issue_root = self.corpus_root / "Т.99_№1"
        self.issue_root.mkdir(parents=True, exist_ok=True)
        self.settings_override = override_settings(
            ARTICLE_RECOMMENDATION_CORPUS_ROOT=self.corpus_root,
            ARTICLE_RECOMMENDATION_LIMIT=3,
            SUBMISSION_ROUTE_SUGGESTION_ENABLED=False,
            SUBMISSION_CHECKS_ASYNC=False,
        )
        self.settings_override.enable()
        self._build_test_corpus()

        self.user = get_user_model().objects.create_user(username="recommend_author", password="1234")
        self.journal = Journal.objects.create(name="Журнал рекомендаций")
        self.article_type = ArticleType.objects.create(code="recommend-type", name="Статья для рекомендаций")

    def tearDown(self):
        self.settings_override.disable()
        self.temp_dir.cleanup()

    def _build_test_corpus(self):
        metadata_path = self.issue_root / "articles_metadata.csv"
        metadata_path.write_text(
            "\n".join(
                [
                    "section;article_id;title;authors;pages;url",
                    "Информатика;1001;Нейронные сети для анализа медицинских изображений;Иванов И.И.;1-10;https://example.com/1001",
                    "Химия;1002;Экстракция биологически активных веществ из растительного сырья;Петров П.П.;11-20;https://example.com/1002",
                ]
            ),
            encoding="utf-8",
        )
        (self.issue_root / "1001 - Нейронные сети для анализа медицинских изображений.html").write_text(
            """
            <html><body>
            <div id="abstract1">Предложен метод анализа медицинских изображений на основе нейронных сетей и компьютерного зрения.</div>
            <p>Keywords: <a>NEURAL NETWORKS</a>, <a>COMPUTER VISION</a>, <a>MEDICAL IMAGES</a></p>
            </body></html>
            """,
            encoding="utf-8",
        )
        (self.issue_root / "1002 - Экстракция биологически активных веществ из растительного сырья.html").write_text(
            """
            <html><body>
            <div id="abstract1">Рассмотрены процессы экстракции и выделения активных веществ из растений.</div>
            <p>Keywords: <a>EXTRACTION</a>, <a>PLANT RAW MATERIAL</a>, <a>CHEMISTRY</a></p>
            </body></html>
            """,
            encoding="utf-8",
        )

    def test_recommend_articles_returns_best_match(self):
        payload = recommend_articles(
            title="Анализ медицинских изображений нейронными сетями",
            abstract="Компьютерное зрение и распознавание визуальных данных.",
        )

        self.assertEqual(payload["recommendations"][0]["article_id"], "1001")
        self.assertTrue(payload["recommendations"][0]["matched_terms"])

    def test_submission_detail_shows_recommendations_for_current_version(self):
        submission = create_submission_with_initial_version(
            author=self.user,
            title="Анализ медицинских изображений нейронными сетями",
            abstract="Компьютерное зрение и распознавание визуальных данных.",
            journal=self.journal,
            article_type=self.article_type,
            file=SimpleUploadedFile("article.txt", b"content"),
        )

        recommendation_run = submission.check_runs.filter(
            check_definition__code="article_recommendations",
            version=submission.current_version,
        ).first()

        self.assertIsNotNone(recommendation_run)
        self.assertTrue(recommendation_run.result_payload["recommendations"])

        self.client.force_login(self.user)
        response = self.client.get(reverse("submissions:detail", args=[submission.pk]))

        self.assertContains(response, "Рекомендуемые статьи")
        self.assertContains(response, "Нейронные сети для анализа медицинских изображений")
