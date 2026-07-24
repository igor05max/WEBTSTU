from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.directory.formatting_templates import (
    create_formatting_template,
    get_latest_formatting_template,
    process_formatting_template,
)
from apps.directory.journal_search import build_journal_search_index
from apps.directory.models import ArticleType, Journal, PublicationTopic
from apps.directory.publication_topics import (
    resolve_or_create_publication_topic,
    search_publication_topics,
)


class JournalSearchViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="journal_search_user", password="1234")
        self.journal = Journal.objects.create(
            name="2D MATERIALS",
            issn="2053-1583",
            search_index=build_journal_search_index(["2D MATERIALS"], ["2053-1583"]),
            white_list_level=1,
        )

    def test_search_finds_journal_by_compact_issn(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("directory:journal_search"), {"q": "20531583"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["id"], self.journal.id)


class PublicationTopicAndTemplateTests(TestCase):
    def setUp(self):
        self.media_dir = TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=self.media_dir.name)
        self.settings_override.enable()
        self.user = get_user_model().objects.create_user(
            username="topic_catalog_user",
            password="1234",
        )
        self.article_type = ArticleType.objects.create(code="theses", name="Тезисы")

    def tearDown(self):
        self.settings_override.disable()
        self.media_dir.cleanup()

    def test_normalized_duplicate_reuses_existing_topic(self):
        first, created_first = resolve_or_create_publication_topic(
            "Наука и технологии — 2027",
            created_by=self.user,
        )
        second, created_second = resolve_or_create_publication_topic(
            "  НАУКА и технологии 2027 ",
            created_by=self.user,
        )

        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(PublicationTopic.objects.count(), 1)

    def test_flexible_search_finds_topic_with_typo(self):
        topic, _created = resolve_or_create_publication_topic(
            "Информационные технологии в образовании 2027",
            created_by=self.user,
        )

        results = search_publication_topics("информационые техналогии образование")

        self.assertEqual(results[0].pk, topic.pk)

    def test_latest_template_version_is_offered(self):
        topic, _created = resolve_or_create_publication_topic(
            "Цифровой университет 2027",
            created_by=self.user,
        )
        first = create_formatting_template(
            article_type=self.article_type,
            publication_topic=topic,
            uploaded_by=self.user,
            file=SimpleUploadedFile("template-v1.txt", b"Version one"),
        )
        second = create_formatting_template(
            article_type=self.article_type,
            publication_topic=topic,
            uploaded_by=self.user,
            file=SimpleUploadedFile("template-v2.txt", b"Version two"),
        )

        latest = get_latest_formatting_template(
            article_type=self.article_type,
            publication_topic=topic,
        )

        self.assertEqual(first.version_number, 1)
        self.assertEqual(second.version_number, 2)
        self.assertEqual(latest.pk, second.pk)

    def test_latex_template_is_processed_and_can_be_downloaded(self):
        topic, _created = resolve_or_create_publication_topic(
            "LaTeX-конференция 2027",
            created_by=self.user,
        )
        template = create_formatting_template(
            article_type=self.article_type,
            publication_topic=topic,
            uploaded_by=self.user,
            file=SimpleUploadedFile(
                "conference.tex",
                (
                    r"\documentclass[14pt,a4paper]{article}"
                    r"\usepackage[margin=2cm]{geometry}"
                    r"\setlength{\parindent}{1cm}"
                    r"\begin{document}Текст\end{document}"
                ).encode("utf-8"),
            ),
        )
        process_formatting_template(template)
        template.refresh_from_db()
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("directory:formatting_template_latex_download", args=[template.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/x-tex; charset=utf-8")
        self.assertIn(".tex", response["Content-Disposition"])
        self.assertEqual(template.extracted_rules["body"]["font_size_pt"], 14)
