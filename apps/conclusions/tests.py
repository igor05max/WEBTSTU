import hashlib
import tempfile

from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone

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
