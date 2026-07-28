import io
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from apps.directory.models import ArticleType
from apps.submissions.document_preview import get_preview_kind
from apps.submissions.forms import SubmissionVersionUploadForm
from apps.submissions.latex_projects import (
    LatexProjectError,
    prepare_latex_archive,
)
from apps.submissions.models import Submission, SubmissionStatus, SubmissionVersion


def _latex_project(*, source=None, extra=None):
    source = source or r"""
\documentclass{article}
\usepackage{graphicx}
\begin{document}
\section{Test}
\includegraphics{Figures/chart.png}
\end{document}
"""
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("ARTICLE.tex", source.encode("utf-8"))
        archive.writestr("figures/chart.png", b"png-payload")
        for name, payload in (extra or {}).items():
            archive.writestr(name, payload)
    return output.getvalue()


class LatexProjectParserTests(SimpleTestCase):
    def test_archive_selects_main_file_and_repairs_case_sensitive_asset_path(self):
        prepared = prepare_latex_archive(
            _latex_project(),
            filename="article-project.zip",
        )

        self.assertEqual(prepared.main_path, "ARTICLE.tex")
        self.assertIn(
            r"\includegraphics{figures/chart.png}",
            prepared.main_file.read().decode("utf-8"),
        )
        self.assertEqual(prepared.manifest["asset_files"], ["figures/chart.png"])
        self.assertTrue(
            any("Исправлен регистр пути" in item for item in prepared.manifest["warnings"])
        )

    def test_archive_rejects_parent_path(self):
        output = io.BytesIO()
        with ZipFile(output, "w", ZIP_DEFLATED) as archive:
            archive.writestr("../ARTICLE.tex", r"\begin{document}\end{document}")

        with self.assertRaisesMessage(LatexProjectError, "Недопустимый путь"):
            prepare_latex_archive(output.getvalue(), filename="unsafe.zip")

    def test_archive_rejects_differently_cased_duplicate_paths(self):
        with self.assertRaisesMessage(LatexProjectError, "различающиеся только регистром"):
            prepare_latex_archive(
                _latex_project(extra={"FIGURES/chart.png": b"duplicate"}),
                filename="duplicate.zip",
            )

    def test_upload_form_accepts_latex_project_and_exposes_main_tex(self):
        form = SubmissionVersionUploadForm(
            data={"comment": ""},
            files={
                "file": SimpleUploadedFile(
                    "article-project.zip",
                    _latex_project(),
                    content_type="application/zip",
                )
            },
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["file"].name, "ARTICLE.tex")
        self.assertIsNotNone(form.prepared_latex_upload.archive_file)
        self.assertEqual(form.prepared_latex_upload.main_path, "ARTICLE.tex")

    def test_tex_has_preview_kind(self):
        self.assertEqual(get_preview_kind("ARTICLE.tex"), "latex")


@override_settings(SUBMISSION_CHECKS_ASYNC=False)
class LatexProjectViewsTests(TestCase):
    def setUp(self):
        self.media_directory = TemporaryDirectory()
        self.override_media = override_settings(MEDIA_ROOT=self.media_directory.name)
        self.override_media.enable()
        self.addCleanup(self.override_media.disable)
        self.addCleanup(self.media_directory.cleanup)
        self.user = get_user_model().objects.create_user(
            username="latex-author",
            password="password",
        )
        self.article_type = ArticleType.objects.create(
            code="article-latex",
            name="LaTeX article",
        )
        self.prepared = prepare_latex_archive(
            _latex_project(),
            filename="article-project.zip",
        )
        self.submission = Submission.objects.create(
            title="LaTeX project",
            author=self.user,
            article_type=self.article_type,
            status=SubmissionStatus.DRAFT,
        )
        self.version = SubmissionVersion.objects.create(
            submission=self.submission,
            version_number=1,
            file=self.prepared.main_file,
            project_archive=self.prepared.archive_file,
            project_main_path=self.prepared.main_path,
            project_manifest=self.prepared.manifest,
            uploaded_by=self.user,
        )
        self.submission.current_version = self.version
        self.submission.save(update_fields=["current_version"])
        self.client.force_login(self.user)

    def test_project_asset_is_available_through_authorized_view(self):
        response = self.client.get(
            reverse(
                "submissions:version_project_file",
                args=[self.submission.pk, self.version.pk, "figures/chart.png"],
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"png-payload")

    @patch("apps.checks.services.queue_submission_checks")
    def test_editor_creates_new_version_and_preserves_project_files(self, queue_checks):
        updated_source = r"""
\documentclass{article}
\usepackage{graphicx}
\begin{document}
\section{Updated}
\includegraphics{figures/chart.png}
\end{document}
"""
        response = self.client.post(
            reverse(
                "submissions:version_latex_editor",
                args=[self.submission.pk, self.version.pk],
            ),
            {
                "source": updated_source,
                "comment": "Updated source",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.submission.refresh_from_db()
        new_version = self.submission.current_version
        self.assertEqual(new_version.version_number, 2)
        self.assertTrue(new_version.project_archive)
        with new_version.project_archive.open("rb") as source, ZipFile(source) as archive:
            self.assertIn("figures/chart.png", archive.namelist())
            self.assertIn(
                r"\section{Updated}",
                archive.read("ARTICLE.tex").decode("utf-8"),
            )
        queue_checks.assert_called_once()

    @patch(
        "apps.submissions.views.build_latex_project_pdf",
        side_effect=LatexProjectError("compiler unavailable"),
    )
    def test_preview_keeps_source_visible_when_pdf_cannot_be_built(self, _build_pdf):
        response = self.client.get(
            reverse(
                "submissions:version_preview",
                args=[self.submission.pk, self.version.pk],
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "compiler unavailable")
        self.assertContains(response, r"\includegraphics{figures/chart.png}")
        self.assertContains(response, "Редактировать TEX")
