from io import BytesIO
from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, ZipFile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.checks.document_checks import build_document_quality_report, build_file_safety_report
from apps.checks.models import CheckRunStatus
from apps.directory.models import ArticleType, Journal
from apps.submissions.document_analysis import analyze_document_bytes, match_authors_to_users
from apps.submissions.models import SubmissionStatus
from apps.submissions.services import create_submission_with_initial_version


def build_article_docx(*, dangerous_member=False, reference_heading="Список литературы"):
    paragraphs = [
        "УДК 004.9",
        "author-header@example.ru",
        "author-header@example.ru",
        "ПРОГРАММНЫЙ МОДУЛЬ ДЛЯ АНАЛИЗА НАУЧНЫХ МАТЕРИАЛОВ",
        "А.Е. Архипов, В.С. Круглов",
        "Кафедра информационных систем ФГБОУ ВО «ТГТУ»",
        "author@example.ru",
        "Ключевые слова: анализ, документ, метаданные.",
        "Аннотация: описан метод автоматической проверки научной статьи.",
        "Введение",
        "Основной текст с подозрительным словом cистема.",
        "Материалы и методы",
        "Результаты",
        "Обсуждение",
        "Заключение",
        reference_heading,
        "Иванов И. И. Анализ документов. 2025.",
    ]
    body = "".join(
        f"<w:p><w:r><w:t>{value}</w:t></w:r></w:p>" for value in paragraphs
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}<w:sectPr/></w:body></w:document>"
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document_xml)
        if dangerous_member:
            archive.writestr("word/vbaProject.bin", b"macro")
    return buffer.getvalue()


class DocumentAnalysisTests(TestCase):
    def test_extracts_editable_metadata_and_matches_directory_users(self):
        snapshot = analyze_document_bytes(build_article_docx(), "article.docx")

        self.assertEqual(
            snapshot["metadata"]["title"],
            "ПРОГРАММНЫЙ МОДУЛЬ ДЛЯ АНАЛИЗА НАУЧНЫХ МАТЕРИАЛОВ",
        )
        self.assertEqual(snapshot["metadata"]["authors"], ["А.Е. Архипов", "В.С. Круглов"])
        self.assertEqual(snapshot["metadata"]["contact_emails"], "author@example.ru")

        user = get_user_model().objects.create_user(
            username="arhipov_ae",
            first_name="Архипов Алексей Евгеньевич",
        )
        matches = match_authors_to_users(snapshot["metadata"]["authors"], [user])
        self.assertEqual(matches[0]["user_id"], user.id)

    def test_dangerous_docx_member_is_critical(self):
        snapshot = analyze_document_bytes(build_article_docx(dangerous_member=True), "article.docx")
        submission = SimpleNamespace()
        version = SimpleNamespace(file=True)

        passed, payload = build_file_safety_report(submission, version, snapshot=snapshot)

        self.assertFalse(passed)
        self.assertTrue(
            any(issue["code"] == "dangerous_archive_member" for issue in payload["issues"])
        )
        self.assertEqual(payload["summary"]["critical"], 1)

    def test_recognizes_list_of_used_literature_heading(self):
        snapshot = analyze_document_bytes(
            build_article_docx(reference_heading="СПИСОК ИСПОЛЬЗОВАННОЙ ЛИТЕРАТУРЫ"),
            "article.docx",
        )
        submission = SimpleNamespace(
            title="Программный модуль для анализа научных материалов",
            document_authors="А.Е. Архипов, В.С. Круглов",
            organizations="ФГБОУ ВО «ТГТУ»",
            contact_emails="author@example.ru",
            keywords="анализ, документ, метаданные",
            abstract="Описан метод автоматической проверки научной статьи.",
            journal=SimpleNamespace(editorial_policy={}),
            article_type=SimpleNamespace(
                code="article",
                name="Статья",
                min_word_count=1,
                max_word_count=100000,
            ),
            get_authors_display=lambda: "А.Е. Архипов, В.С. Круглов",
        )

        _passed, payload = build_document_quality_report(
            submission,
            SimpleNamespace(file=True),
            snapshot=snapshot,
        )

        self.assertEqual(payload["metrics"]["references"], 1)
        self.assertNotIn("missing_references", {issue["code"] for issue in payload["issues"]})


@override_settings(
    SUBMISSION_CHECKS_ASYNC=False,
    SUBMISSION_CONTENT_REVIEW_ENABLED=False,
    SUBMISSION_ROUTE_SUGGESTION_ENABLED=False,
)
class AdvisoryChecksTests(TestCase):
    def test_quality_check_does_not_invent_missing_metadata_or_sections(self):
        user = get_user_model().objects.create_user(username="quality_author", password="1234")
        journal = Journal.objects.create(name="Журнал проверки")
        article_type = ArticleType.objects.create(code="article", name="Статья")

        submission = create_submission_with_initial_version(
            author=user,
            title="Короткий материал",
            abstract="",
            journal=journal,
            article_type=article_type,
            file=SimpleUploadedFile("article.txt", b"short text"),
        )

        submission.refresh_from_db()
        metadata_run = submission.check_runs.get(check_definition__code="metadata_complete")
        content_run = submission.check_runs.get(check_definition__code="mock_content_screening")
        subject_area_run = submission.check_runs.get(check_definition__code="subject_area_detection")
        formatting_run = submission.check_runs.get(
            check_definition__code="formatting_compliance"
        )
        self.assertEqual(metadata_run.status, CheckRunStatus.PASSED)
        self.assertEqual(content_run.status, CheckRunStatus.NOT_PERFORMED)
        self.assertEqual(subject_area_run.status, CheckRunStatus.NOT_PERFORMED)
        self.assertEqual(formatting_run.status, CheckRunStatus.NOT_PERFORMED)
        self.assertEqual(content_run.result_payload["execution_status"], "not_performed")
        self.assertEqual(submission.status, SubmissionStatus.SUBMITTED)
        self.assertIn("summary", metadata_run.result_payload)
        self.assertEqual(metadata_run.result_payload["issues"], [])

        self.client.force_login(user)
        response = self.client.get(reverse("submissions:detail", args=[submission.pk]))
        self.assertContains(response, "Не выполнена", count=3)
