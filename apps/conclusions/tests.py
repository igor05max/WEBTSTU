import hashlib
import io
import tempfile
from unittest.mock import patch
from xml.etree import ElementTree

from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfReader, PdfWriter

from apps.accounts.models import User
from apps.conclusions.models import ConclusionDocument, ConclusionSignature
from apps.conclusions.services import (
    calculate_file_sha256,
    ensure_conclusion_document,
    verify_conclusion_document,
)
from apps.directory.models import ArticleType, Direction, Journal, OrgUnit
from apps.submissions.models import Submission, SubmissionVersion
from apps.workflow.models import (
    ApprovalTask,
    ApprovalTaskStatus,
    AssigneeKind,
    RouteTemplate,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowStep,
    WorkflowStepStatus,
)
from apps.workflow.services import approve_task


class ConclusionSignatureTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=self.media_root.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.media_root.cleanup)

        self.author = User.objects.create_user(username="author", password="1234")
        self.reviewer_unit = OrgUnit.objects.create(name="Экспертная комиссия")
        self.reviewer_role = Group.objects.create(name="Эксперт НТС")
        self.reviewer_unit.available_roles.add(self.reviewer_role)
        self.reviewer = User.objects.create_user(
            username="reviewer",
            password="1234",
            first_name="Иван",
            last_name="Иванов",
            org_unit=self.reviewer_unit,
        )
        self.reviewer.groups.add(self.reviewer_role)
        self.journal = Journal.objects.create(name="Вестник тестирования")
        self.article_type = ArticleType.objects.create(code="article", name="Статья")
        self.direction = Direction.objects.create(code="main", name="Основное направление")
        self.route_template = RouteTemplate.objects.create(name="Экспертный маршрут")
        self.submission = Submission.objects.create(
            title="Проверка неизменяемого заключения",
            author=self.author,
            journal=self.journal,
            article_type=self.article_type,
            direction=self.direction,
            route_template=self.route_template,
        )
        self.first_version = SubmissionVersion.objects.create(
            submission=self.submission,
            version_number=1,
            file=ContentFile(b"first manuscript", name="manuscript-v1.docx"),
            uploaded_by=self.author,
        )
        self.second_version = SubmissionVersion.objects.create(
            submission=self.submission,
            version_number=2,
            file=ContentFile(b"second manuscript", name="manuscript-v2.docx"),
            uploaded_by=self.author,
        )
        self.submission.current_version = self.second_version
        self.submission.save(update_fields=["current_version", "updated_at"])

        self.workflow_run = WorkflowRun.objects.create(
            submission=self.submission,
            route_template=self.route_template,
            status=WorkflowRunStatus.ACTIVE,
        )
        self.step = WorkflowStep.objects.create(
            workflow_run=self.workflow_run,
            order=1,
            name="Экспертиза",
            assignee_kind=AssigneeKind.FIXED_UNIT_GROUP,
            assigned_group=self.reviewer_role,
            assigned_unit=self.reviewer_unit,
            status=WorkflowStepStatus.ACTIVE,
            started_at=timezone.now(),
        )
        self.workflow_run.current_step = self.step
        self.workflow_run.save(update_fields=["current_step"])
        self.task = ApprovalTask.objects.create(
            workflow_step=self.step,
            status=ApprovalTaskStatus.ACTIVE,
            assigned_group=self.reviewer_role,
            assigned_unit=self.reviewer_unit,
            activated_at=timezone.now(),
        )
        self.final_step = WorkflowStep.objects.create(
            workflow_run=self.workflow_run,
            order=2,
            name="Утверждение",
            assignee_kind=AssigneeKind.FIXED_UNIT_GROUP,
            assigned_group=self.reviewer_role,
            assigned_unit=self.reviewer_unit,
            status=WorkflowStepStatus.PENDING,
        )
        self.docx_bytes = b"immutable conclusion docx"
        self.document = ConclusionDocument.objects.create(
            workflow_run=self.workflow_run,
            submission=self.submission,
            source_version=self.first_version,
            registration_number="ЗОП-2026-00001",
            document_file=ContentFile(self.docx_bytes, name="conclusion.docx"),
            document_sha256=hashlib.sha256(self.docx_bytes).hexdigest(),
            sealed_at=timezone.now(),
            is_sealed=True,
        )

    def test_approval_creates_immutable_signature_for_current_manuscript_version(self):
        approve_task(
            self.task,
            self.reviewer,
            request_meta={"client_ip": "127.0.0.1", "user_agent": "test-agent"},
        )

        signature = ConclusionSignature.objects.get(task=self.task)

        self.assertEqual(signature.document, self.document)
        self.assertEqual(signature.signer, self.reviewer)
        self.assertEqual(signature.signer_role, self.reviewer_role.name)
        self.assertEqual(signature.submission_version, self.second_version)
        self.assertEqual(signature.submission_version_number, 2)
        self.assertEqual(
            signature.submission_version_sha256,
            hashlib.sha256(b"second manuscript").hexdigest(),
        )
        self.assertEqual(signature.document_sha256, self.document.document_sha256)
        self.assertEqual(signature.confirmation_method, "authenticated_session")
        self.assertEqual(signature.client_ip, "127.0.0.1")
        self.assertTrue(signature.event_hash)
        self.assertTrue(verify_conclusion_document(self.document)["is_valid"])

    def test_sealed_document_and_signature_cannot_be_changed(self):
        self.document.registration_number = "ЗОП-2026-99999"
        with self.assertRaises(ValidationError):
            self.document.save()

        approve_task(self.task, self.reviewer)
        signature = ConclusionSignature.objects.get(task=self.task)
        signature.signer_name = "Другой пользователь"
        with self.assertRaises(ValidationError):
            signature.save()

    def test_generated_conclusion_is_a_sealed_docx_without_pdf_conversion(self):
        self.document.delete()

        document = ensure_conclusion_document(self.workflow_run)

        self.assertTrue(document.document_file.name.endswith(".docx"))
        self.assertEqual(document.document_sha256, calculate_file_sha256(document.document_file))
        self.assertTrue(document.is_sealed)

    def test_final_approval_creates_three_file_package_with_visual_signatures(self):
        approve_task(self.task, self.reviewer)
        final_task = ApprovalTask.objects.get(workflow_step=self.final_step)

        source_pdf = io.BytesIO()
        source_writer = PdfWriter()
        source_writer.add_blank_page(width=595, height=842)
        source_writer.add_blank_page(width=595, height=842)
        source_writer.write(source_pdf)
        with patch(
            "apps.conclusions.services._convert_conclusion_docx_to_pdf",
            return_value=source_pdf.getvalue(),
        ):
            approve_task(final_task, self.reviewer)

        self.document.refresh_from_db()
        self.assertIsNotNone(self.document.package_finalized_at)
        self.assertEqual(self.document.source_pdf_file.name.rsplit("/", 1)[-1], f"{self.document.package_id}.pdf")
        self.assertEqual(self.document.printed_pdf_file.name.rsplit("/", 1)[-1], "Печатная_форма.pdf")
        self.assertEqual(self.document.signature_data_file.name.rsplit("/", 1)[-1], "wredc_data.xml")
        self.assertTrue(verify_conclusion_document(self.document)["package_is_valid"])

        with self.document.printed_pdf_file.open("rb") as source:
            printed_pdf = PdfReader(source)
            printed_text = "\n".join(page.extract_text() or "" for page in printed_pdf.pages)
        self.assertEqual(len(printed_pdf.pages), 2)
        self.assertIn("ПЭП ТГТУ", printed_text)
        self.assertIn(self.reviewer.get_full_name(), printed_text)
        self.assertIn("Согласовано", printed_text)
        self.assertIn("Подписано", printed_text)

        with self.document.signature_data_file.open("rb") as source:
            xml_bytes = source.read()
        root = ElementTree.fromstring(xml_bytes)
        namespace = {"edoc": "urn:tgtu:electronic-document:1.0"}
        self.assertEqual(root.get("id"), str(self.document.package_id))
        self.assertEqual(
            root.findtext(".//edoc:file", namespaces=namespace),
            f"{self.document.package_id}.pdf",
        )
        self.assertEqual(
            len(root.findall(".//edoc:signature", namespaces=namespace)),
            2,
        )

        self.client.force_login(self.author)
        detail_response = self.client.get(reverse("submissions:detail", args=[self.submission.pk]))
        self.assertContains(detail_response, self.document.registration_number)
        self.assertContains(detail_response, "Заключение с подписями ПЭП")
        self.assertContains(detail_response, self.reviewer.get_full_name())

        printed_response = self.client.get(
            reverse(
                "submissions:conclusion_package_file",
                args=[self.submission.pk, self.document.pk, "printed"],
            )
        )
        self.assertEqual(printed_response.status_code, 200)
        self.assertEqual(printed_response["Content-Type"], "application/pdf")
        printed_response.close()

    def test_final_package_downloads_require_visible_task(self):
        approve_task(self.task, self.reviewer)
        final_task = ApprovalTask.objects.get(workflow_step=self.final_step)
        source_pdf = io.BytesIO()
        source_writer = PdfWriter()
        source_writer.add_blank_page(width=595, height=842)
        source_writer.write(source_pdf)
        with patch(
            "apps.conclusions.services._convert_conclusion_docx_to_pdf",
            return_value=source_pdf.getvalue(),
        ):
            approve_task(final_task, self.reviewer)

        self.client.force_login(self.reviewer)
        expected_types = {
            "source": "application/pdf",
            "printed": "application/pdf",
            "signatures": "application/xml",
        }
        for file_kind, content_type in expected_types.items():
            response = self.client.get(
                reverse(
                    "workflow:conclusion_package_file",
                    args=[final_task.pk, file_kind],
                )
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], content_type)
            self.assertTrue(b"".join(response.streaming_content))
            response.close()

    def test_conclusion_download_requires_visible_workflow_task(self):
        download_url = reverse("workflow:conclusion_download", args=[self.task.pk])
        self.client.force_login(self.reviewer)

        response = self.client.get(download_url)

        self.assertEqual(response.status_code, 200)
        self.assertIn('filename="conclusion.docx"', response["Content-Disposition"])
        self.assertEqual(b"".join(response.streaming_content), self.docx_bytes)
        response.close()

        outsider = User.objects.create_user(username="conclusion-outsider", password="1234")
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(download_url).status_code, 404)

    def test_submission_conclusion_is_visible_to_author_and_past_route_participant(self):
        approve_task(self.task, self.reviewer)
        self.reviewer.groups.clear()

        author_url = reverse("submissions:detail", args=[self.submission.pk])
        download_url = reverse(
            "submissions:conclusion_download",
            args=[self.submission.pk, self.document.pk],
        )

        for user in (self.author, self.reviewer):
            self.client.force_login(user)
            detail_response = self.client.get(author_url)
            self.assertContains(detail_response, self.document.registration_number)
            self.assertContains(detail_response, "Подписано ПЭП")
            response = self.client.get(download_url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(b"".join(response.streaming_content), self.docx_bytes)
            response.close()

        outsider = User.objects.create_user(username="submission-conclusion-outsider", password="1234")
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(author_url).status_code, 404)
        self.assertEqual(self.client.get(download_url).status_code, 404)
