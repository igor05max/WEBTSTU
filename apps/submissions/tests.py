from django.contrib.auth.models import Group
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.accounts.roles import CHAIR_HEAD_ROLE_NAME, ensure_chair_head_role_for_org_unit
from apps.checks.models import CheckRunStatus
from apps.directory.models import ArticleType, Direction, Journal, OrgUnit
from apps.directory.journal_search import build_journal_search_index
from apps.submissions.forms import SubmissionCreateForm, SubmissionSubmitForm
from apps.submissions.document_preview import _build_word_document_pdf_with_libreoffice
from apps.submissions.models import Submission, SubmissionAppealStatus, SubmissionStatus, SubmissionVersion
from apps.submissions.subject_area import detect_direction_for_submission
from apps.submissions.views import _build_route_preview_templates
from apps.submissions.services import add_submission_version, create_submission_with_initial_version, submit_submission
from apps.workflow.models import (
    ApprovalTask,
    ApprovalTaskStatus,
    AssigneeKind,
    RouteStepTemplate,
    TaskDecision,
    TaskDecisionType,
    RouteTemplate,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowStep,
    WorkflowStepStatus,
)
from apps.workflow.services import (
    approve_task,
    approve_submission_appeal,
    reject_submission_appeal,
    request_revision,
    submit_submission_appeal,
)


def _build_preview_docx():
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Название документа</w:t></w:r></w:p>
    <w:p><w:r><w:t>Первый абзац для просмотра.</w:t></w:r></w:p>
    <w:tbl>
      <w:tr><w:tc><w:p><w:r><w:t>Колонка 1</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Колонка 2</w:t></w:r></w:p></w:tc></w:tr>
      <w:tr><w:tc><w:p><w:r><w:t>Значение 1</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Значение 2</w:t></w:r></w:p></w:tc></w:tr>
    </w:tbl>
    <w:sectPr/>
  </w:body>
</w:document>"""
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


@override_settings(
    SUBMISSION_SELECTABLE_ROUTE_TEMPLATE_IDS=(),
    SUBMISSION_ROUTE_SUGGESTION_ENABLED=False,
    SUBMISSION_CHECKS_ASYNC=False,
)
class SubmissionListScopeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="author", password="1234")
        self.coauthor = get_user_model().objects.create_user(username="coauthor", password="1234")
        self.journal = Journal.objects.create(name="Тестовый журнал")
        self.article_type = ArticleType.objects.create(code="article", name="Научная статья")

        self._create_submission("Черновик", SubmissionStatus.DRAFT)
        self._create_submission("На согласовании", SubmissionStatus.IN_REVIEW)
        self._create_submission("Согласованная", SubmissionStatus.APPROVED)

    def _create_submission(self, title, status):
        return Submission.objects.create(
            title=title,
            author=self.user,
            journal=self.journal,
            article_type=self.article_type,
            status=status,
        )

    def test_history_scope_shows_only_finished_submissions(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("submissions:list"), {"scope": "history"})
        titles = list(response.context["submissions"].values_list("title", flat=True))

        self.assertEqual(titles, ["Согласованная"])

    def test_in_review_scope_shows_only_active_review_submissions(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("submissions:list"), {"scope": "in_review"})
        titles = list(response.context["submissions"].values_list("title", flat=True))

        self.assertEqual(titles, ["На согласовании"])

    def test_coauthor_sees_shared_submission_in_personal_list(self):
        shared_submission = Submission.objects.create(
            title="Совместная статья",
            author=self.user,
            journal=self.journal,
            article_type=self.article_type,
            status=SubmissionStatus.IN_REVIEW,
        )
        shared_submission.authors.add(self.coauthor)

        self.client.force_login(self.coauthor)
        response = self.client.get(reverse("submissions:list"))

        titles = list(response.context["submissions"].values_list("title", flat=True))
        self.assertIn("Совместная статья", titles)

    def test_work_scope_groups_active_submission_stages(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("submissions:list"), {"scope": "work"})
        titles = list(response.context["submissions"].values_list("title", flat=True))

        self.assertEqual(titles, ["На согласовании"])

    def test_search_finds_submission_by_title(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("submissions:list"), {"q": "согласованная"})
        titles = list(response.context["submissions"].values_list("title", flat=True))

        self.assertEqual(titles, ["Согласованная"])

    def test_personal_list_opens_submission_by_full_row_and_shows_draft_delete_action(self):
        draft = Submission.objects.get(title="Черновик")
        self.client.force_login(self.user)

        response = self.client.get(reverse("submissions:list"))

        self.assertContains(response, 'class="submissions-row-open"')
        self.assertContains(response, reverse("submissions:detail", args=[draft.pk]))
        self.assertContains(response, reverse("submissions:delete_draft", args=[draft.pk]))
        self.assertContains(response, "Удалить черновик")

    def test_author_can_delete_own_draft(self):
        draft = Submission.objects.get(title="Черновик")
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("submissions:delete_draft", args=[draft.pk]),
            {"return_scope": "drafts"},
        )

        self.assertRedirects(response, f'{reverse("submissions:list")}?owner=me&scope=drafts')
        self.assertFalse(Submission.objects.filter(pk=draft.pk).exists())

    def test_coauthor_cannot_delete_another_users_draft(self):
        draft = Submission.objects.get(title="Черновик")
        draft.authors.add(self.coauthor)
        self.client.force_login(self.coauthor)

        response = self.client.post(reverse("submissions:delete_draft", args=[draft.pk]))

        self.assertEqual(response.status_code, 404)
        self.assertTrue(Submission.objects.filter(pk=draft.pk).exists())

    def test_submission_in_route_cannot_be_deleted_as_draft(self):
        submission = Submission.objects.get(title="На согласовании")
        self.client.force_login(self.user)

        response = self.client.post(reverse("submissions:delete_draft", args=[submission.pk]))

        self.assertRedirects(response, reverse("submissions:detail", args=[submission.pk]))
        self.assertTrue(Submission.objects.filter(pk=submission.pk).exists())


@override_settings(
    SUBMISSION_SELECTABLE_ROUTE_TEMPLATE_IDS=(),
    SUBMISSION_ROUTE_SUGGESTION_ENABLED=False,
    SUBMISSION_CHECKS_ASYNC=False,
)
class SubmissionRouteSelectionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="author_route", password="1234")
        self.journal = Journal.objects.create(name="Маршрутный журнал")
        self.article_type = ArticleType.objects.create(code="route-article", name="Маршрутная статья")
        self.article_type_theses = ArticleType.objects.create(code="route-theses", name="Тезисы доклада")
        self.direction_main = Direction.objects.create(code="main-route", name="Основное направление")
        self.direction_other = Direction.objects.create(code="other-route", name="Другое направление")
        self.route_template_main = RouteTemplate.objects.create(
            name="Основной маршрут",
            direction=self.direction_main,
            article_type=self.article_type,
            is_active=True,
        )
        self.route_template_main_theses = RouteTemplate.objects.create(
            name="Маршрут тезисов",
            direction=self.direction_main,
            article_type=self.article_type_theses,
            is_active=True,
        )
        self.route_template_other = RouteTemplate.objects.create(
            name="Другой маршрут",
            direction=self.direction_other,
            article_type=self.article_type,
            is_active=True,
        )

    def test_submit_form_rejects_route_from_another_direction(self):
        form = SubmissionSubmitForm(
            data={
                "direction": str(self.direction_main.id),
                "route_template": str(self.route_template_other.id),
            },
            current_article_type=self.article_type,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("route_template", form.errors)

    def test_submit_form_rejects_route_from_another_material_type(self):
        form = SubmissionSubmitForm(
            data={
                "direction": str(self.direction_main.id),
                "route_template": str(self.route_template_main_theses.id),
            },
            current_article_type=self.article_type,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("route_template", form.errors)

    def test_submit_form_auto_selects_only_route_for_selected_direction(self):
        form = SubmissionSubmitForm(
            data={
                "direction": str(self.direction_main.id),
            },
            current_article_type=self.article_type,
        )

        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["route_template"], self.route_template_main)

    def test_submit_form_auto_selects_single_base_route_template_for_material_type(self):
        base_route_template = RouteTemplate.objects.create(
            name="Базовый маршрут статьи",
            direction=None,
            article_type=self.article_type,
            is_active=True,
        )

        form = SubmissionSubmitForm(
            data={
                "direction": str(self.direction_other.id),
            },
            current_article_type=self.article_type,
        )

        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["route_template"], base_route_template)

    def test_submit_form_renders_empty_direction_binding_for_base_route_template(self):
        RouteTemplate.objects.create(
            name="Базовый маршрут статьи",
            direction=None,
            article_type=self.article_type,
            is_active=True,
        )

        form = SubmissionSubmitForm(current_article_type=self.article_type)
        route_template_html = str(form["route_template"])

        self.assertIn('data-direction-id=""', route_template_html)
        self.assertNotIn('data-direction-id="None"', route_template_html)

    def test_submit_form_keeps_all_selectable_routes_for_manual_switching(self):
        form = SubmissionSubmitForm(
            current_direction=self.direction_main,
            current_route_template=self.route_template_main,
            current_article_type=self.article_type,
        )

        self.assertQuerySetEqual(
            form.fields["route_template"].queryset.order_by("id"),
            [self.route_template_main, self.route_template_other],
            transform=lambda route: route,
        )

    def test_submit_form_uses_only_configured_selectable_routes(self):
        with self.settings(
            SUBMISSION_SELECTABLE_ROUTE_TEMPLATE_IDS=(self.route_template_other.id,),
        ):
            form = SubmissionSubmitForm(current_article_type=self.article_type)

        self.assertQuerySetEqual(
            form.fields["direction"].queryset,
            [self.direction_other],
            transform=lambda direction: direction,
        )
        self.assertQuerySetEqual(
            form.fields["route_template"].queryset,
            [self.route_template_other],
            transform=lambda route: route,
        )

    def test_submission_detail_hides_route_details_before_submit(self):
        reviewer_group = Group.objects.create(name="Рецензент")
        reviewer_unit = OrgUnit.objects.create(name="Научный отдел", code="science")
        reviewer_unit.available_roles.add(reviewer_group)
        reviewer = get_user_model().objects.create_user(
            username="preview_reviewer",
            password="1234",
            org_unit=reviewer_unit,
        )
        reviewer.groups.add(reviewer_group)
        RouteStepTemplate.objects.create(
            route_template=self.route_template_main,
            order=1,
            name="Первичная проверка",
            assignee_kind=AssigneeKind.FIXED_UNIT_GROUP,
            target_unit=reviewer_unit,
            target_group=reviewer_group,
            target_user=reviewer,
        )
        submission = Submission.objects.create(
            title="Статья для предпросмотра",
            author=self.user,
            journal=self.journal,
            article_type=self.article_type,
            status=SubmissionStatus.SUBMITTED,
        )
        version = SubmissionVersion.objects.create(
            submission=submission,
            version_number=1,
            file=SimpleUploadedFile("article.txt", b"content"),
            uploaded_by=self.user,
        )
        submission.current_version = version
        submission.save(update_fields=["current_version", "updated_at"])

        self.client.force_login(self.user)
        response = self.client.get(reverse("submissions:detail", args=[submission.pk]))

        self.assertFalse(response.context["can_view_route_details"])
        self.assertContains(response, "Маршрут станет доступен автору после проверки и одобрения заведующим кафедрой.")
        self.assertNotContains(response, "route-picker")
        self.assertNotContains(response, "Первичная проверка")

    def test_submission_detail_shows_waiting_message_when_auto_area_is_not_found(self):
        submission = Submission.objects.create(
            title="Статья без автоподбора",
            author=self.user,
            journal=self.journal,
            article_type=self.article_type,
            status=SubmissionStatus.SUBMITTED,
        )
        version = SubmissionVersion.objects.create(
            submission=submission,
            version_number=1,
            file=SimpleUploadedFile("article.txt", b"content"),
            uploaded_by=self.user,
        )
        submission.current_version = version
        submission.save(update_fields=["current_version", "updated_at"])

        self.client.force_login(self.user)
        response = self.client.get(reverse("submissions:detail", args=[submission.pk]))

        submission.refresh_from_db()
        self.assertIsNone(submission.direction)
        self.assertIsNone(submission.route_template)
        self.assertIsNone(response.context["route_suggestion"])
        self.assertTrue(response.context["can_submit"])
        self.assertFalse(response.context["route_selection_ready"])
        self.assertContains(response, "Автоматически определить область экспертизы пока не удалось.")
        self.assertNotContains(response, "route-picker")

    def test_submission_detail_hides_invalid_saved_route_and_waits_for_new_auto_selection(self):
        submission = Submission.objects.create(
            title="Статья с устаревшим маршрутом",
            author=self.user,
            journal=self.journal,
            article_type=self.article_type,
            direction=self.direction_main,
            route_template=self.route_template_main,
            status=SubmissionStatus.SUBMITTED,
        )
        version = SubmissionVersion.objects.create(
            submission=submission,
            version_number=1,
            file=SimpleUploadedFile("article.txt", b"content"),
            uploaded_by=self.user,
        )
        submission.current_version = version
        submission.save(update_fields=["current_version", "updated_at"])

        self.client.force_login(self.user)
        with self.settings(
            SUBMISSION_SELECTABLE_ROUTE_TEMPLATE_IDS=(self.route_template_other.id,),
        ):
            response = self.client.get(reverse("submissions:detail", args=[submission.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["route_selection_ready"])
        self.assertFalse(response.context["can_view_route_details"])
        self.assertContains(response, "Автоматически определить область экспертизы пока не удалось.")
        self.assertNotContains(response, '<div class="submission-meta-label">Маршрут</div>', html=False)
        self.assertNotContains(response, "route-picker")

    def test_build_route_preview_templates_uses_submission_direction_for_base_route(self):
        base_route_template = RouteTemplate.objects.create(
            name="Базовый маршрут статьи",
            direction=None,
            article_type=self.article_type,
            is_active=True,
        )
        reviewer_group = Group.objects.create(name="Рецензент базового маршрута")
        reviewer_unit = OrgUnit.objects.create(name="Научный отдел базового маршрута", code="science-base")
        reviewer_unit.available_roles.add(reviewer_group)
        reviewer = get_user_model().objects.create_user(
            username="base_preview_reviewer",
            password="1234",
            org_unit=reviewer_unit,
        )
        reviewer.groups.add(reviewer_group)
        RouteStepTemplate.objects.create(
            route_template=base_route_template,
            order=1,
            name="Проверка базового маршрута",
            assignee_kind=AssigneeKind.FIXED_UNIT_GROUP,
            target_unit=reviewer_unit,
            target_group=reviewer_group,
            target_user=reviewer,
        )

        preview_templates = _build_route_preview_templates(
            article_type=self.article_type,
            direction=self.direction_other,
            route_template=base_route_template,
        )

        self.assertEqual(
            preview_templates[0]["direction_name"],
            self.direction_other.name,
        )
        self.assertEqual(
            preview_templates[0]["direction_id"],
            self.direction_other.id,
        )

    def test_submission_detail_hides_version_upload_until_revision_is_requested(self):
        submission = create_submission_with_initial_version(
            author=self.user,
            title="Статья без лишней дозагрузки",
            abstract="Аннотация",
            journal=self.journal,
            article_type=self.article_type,
            file=SimpleUploadedFile("article.txt", b"content"),
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse("submissions:detail", args=[submission.pk]))

        self.assertIsNone(response.context["upload_form"])
        self.assertNotContains(response, "Загрузить новую версию")

    def test_submission_detail_shows_version_upload_after_revision_request(self):
        submission = create_submission_with_initial_version(
            author=self.user,
            title="Статья после замечаний",
            abstract="Аннотация",
            journal=self.journal,
            article_type=self.article_type,
            file=SimpleUploadedFile("article.txt", b"content"),
        )
        submission.status = SubmissionStatus.REVISION_REQUESTED
        submission.save(update_fields=["status", "updated_at"])

        self.client.force_login(self.user)
        response = self.client.get(reverse("submissions:detail", args=[submission.pk]))

        self.assertIsNotNone(response.context["upload_form"])
        self.assertContains(response, "Загрузить новую версию")


@override_settings(
    SUBMISSION_SELECTABLE_ROUTE_TEMPLATE_IDS=(),
    SUBMISSION_ROUTE_SUGGESTION_ENABLED=False,
    SUBMISSION_CHECKS_ASYNC=False,
)
class SubmissionCreateViewTests(TestCase):
    def setUp(self):
        self.chair_org_unit = OrgUnit.objects.create(name='Кафедра "Тестовая кафедра"', code="chair-create")
        self.user = get_user_model().objects.create_user(
            username="create_author",
            password="1234",
            chair_org_unit=self.chair_org_unit,
        )
        self.chair_head_role = ensure_chair_head_role_for_org_unit(self.chair_org_unit)
        self.chair_head = get_user_model().objects.create_user(
            username="chair_head_create",
            password="1234",
            chair_org_unit=self.chair_org_unit,
            first_name="Мария",
            last_name="Проверяющая",
        )
        self.chair_head.groups.add(self.chair_head_role)
        self.journal = Journal.objects.create(name="Журнал загрузки")
        self.article_type = ArticleType.objects.create(code="create-article", name="Статья для загрузки")
        self.direction_main = Direction.objects.create(code="create-main-route", name="Основное направление")
        self.direction_other = Direction.objects.create(code="create-other-route", name="Другое направление")
        self.route_template_main = RouteTemplate.objects.create(
            name="Основной маршрут",
            direction=self.direction_main,
            is_active=True,
        )
        self.route_template_other = RouteTemplate.objects.create(
            name="Другой маршрут",
            direction=self.direction_other,
            is_active=True,
        )

    def test_create_form_resolves_journal_by_compact_issn(self):
        self.journal.issn = "2053-1583"
        self.journal.search_index = build_journal_search_index([self.journal.name], ["2053-1583"])
        self.journal.save(update_fields=["issn", "search_index"])

        form = SubmissionCreateForm(
            data={
                "title": "Статья с журналом по ISSN",
                "abstract": "Аннотация",
                "journal_query": "20531583",
                "article_type": self.article_type.id,
            },
            files={"file": SimpleUploadedFile("article.txt", b"content")},
            current_user=self.user,
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["journal"], self.journal)

    def test_create_form_resolves_journal_from_autocomplete_label(self):
        self.journal.name = "2D MATERIALS"
        self.journal.issn = "2053-1583"
        self.journal.search_index = build_journal_search_index([self.journal.name], ["2053-1583"])
        self.journal.save(update_fields=["name", "issn", "search_index"])

        form = SubmissionCreateForm(
            data={
                "title": "Статья с журналом из подсказки",
                "abstract": "Аннотация",
                "journal_query": "2D MATERIALS (2053-1583)",
                "article_type": self.article_type.id,
            },
            files={"file": SimpleUploadedFile("article.txt", b"content")},
            current_user=self.user,
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["journal"], self.journal)

    def test_create_page_hides_process_explainer_block(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("submissions:create"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Как дальше пойдёт заявка")
        self.assertContains(response, "Загрузить материал")

    def test_create_submission_runs_checks_immediately(self):
        submission = create_submission_with_initial_version(
            author=self.user,
            title="Статья с первичной проверкой",
            abstract="Аннотация",
            journal=self.journal,
            article_type=self.article_type,
            file=SimpleUploadedFile("article.txt", b"content"),
        )

        submission.refresh_from_db()

        self.assertEqual(submission.status, SubmissionStatus.SUBMITTED)
        self.assertEqual(submission.check_runs.count(), 5)
        self.assertTrue(
            submission.check_runs.filter(
                check_definition__code="article_recommendations",
                version=submission.current_version,
            ).exists()
        )

    def test_create_submission_stores_coauthors(self):
        coauthor = get_user_model().objects.create_user(username="submission_coauthor", password="1234")

        submission = create_submission_with_initial_version(
            author=self.user,
            title="Статья с соавторами",
            abstract="Аннотация",
            journal=self.journal,
            article_type=self.article_type,
            file=SimpleUploadedFile("article.txt", b"content"),
            co_authors=[coauthor],
        )

        self.assertQuerySetEqual(
            submission.authors.order_by("id"),
            [self.user, coauthor],
            transform=lambda user: user,
        )

    def test_upload_new_version_after_revision_request_runs_checks_again(self):
        submission = create_submission_with_initial_version(
            author=self.user,
            title="Статья после доработки",
            abstract="Аннотация",
            journal=self.journal,
            article_type=self.article_type,
            file=SimpleUploadedFile("article.txt", b"content"),
        )
        submission.status = SubmissionStatus.REVISION_REQUESTED
        submission.save(update_fields=["status", "updated_at"])

        version = add_submission_version(
            submission,
            self.user,
            SimpleUploadedFile("article_v2.txt", b"content-v2"),
            comment="Исправленная версия",
        )

        submission.refresh_from_db()
        self.assertEqual(version.version_number, 2)
        self.assertEqual(submission.current_version_id, version.id)
        self.assertEqual(submission.status, SubmissionStatus.SUBMITTED)
        self.assertEqual(submission.check_runs.filter(version=version).count(), 5)

    def test_upload_new_version_after_revision_request_auto_returns_to_review(self):
        reviewer_group = Group.objects.create(name="Проверяющий доработки")
        reviewer_unit = OrgUnit.objects.create(name="Группа доработки", code="revision-unit")
        reviewer_unit.available_roles.add(reviewer_group)
        reviewer = get_user_model().objects.create_user(
            username="submission_revision_reviewer",
            password="1234",
            org_unit=reviewer_unit,
        )
        reviewer.groups.add(reviewer_group)
        RouteStepTemplate.objects.create(
            route_template=self.route_template_main,
            order=1,
            name="Проверка после доработки",
            assignee_kind=AssigneeKind.FIXED_UNIT_GROUP,
            target_unit=reviewer_unit,
            target_group=reviewer_group,
            target_user=reviewer,
            can_request_revision=True,
        )
        submission = create_submission_with_initial_version(
            author=self.user,
            title="Статья для повторного согласования",
            abstract="Аннотация",
            journal=self.journal,
            article_type=self.article_type,
            file=SimpleUploadedFile("article.txt", b"content"),
        )
        submit_submission(
            submission,
            direction=self.direction_main,
            route_template=self.route_template_main,
            submitted_by=self.user,
        )
        active_task = ApprovalTask.objects.get(
            workflow_step__workflow_run__submission=submission,
            status=ApprovalTaskStatus.ACTIVE,
        )
        request_revision(active_task, self.chair_head, comment="Исправьте файл и загрузите новую версию.")

        version = add_submission_version(
            submission,
            self.user,
            SimpleUploadedFile("article_v2.txt", b"content-v2"),
            comment="Исправленная версия",
        )

        submission.refresh_from_db()
        self.assertEqual(version.version_number, 2)
        self.assertEqual(submission.status, SubmissionStatus.IN_REVIEW)
        self.assertTrue(
            ApprovalTask.objects.filter(
                workflow_step__workflow_run__submission=submission,
                workflow_step__status=WorkflowStepStatus.ACTIVE,
                status=ApprovalTaskStatus.ACTIVE,
            ).exists()
        )

    def test_submission_detail_shows_full_step_history_after_revision_and_approval(self):
        reviewer_group = Group.objects.create(name="История доработки")
        reviewer_unit = OrgUnit.objects.create(name="Группа истории", code="history-unit")
        reviewer_unit.available_roles.add(reviewer_group)
        reviewer = get_user_model().objects.create_user(
            username="history_reviewer",
            password="1234",
            org_unit=reviewer_unit,
        )
        reviewer.groups.add(reviewer_group)
        RouteStepTemplate.objects.create(
            route_template=self.route_template_main,
            order=1,
            name="Проверка истории",
            assignee_kind=AssigneeKind.FIXED_UNIT_GROUP,
            target_unit=reviewer_unit,
            target_group=reviewer_group,
            target_user=reviewer,
            can_request_revision=True,
        )
        submission = create_submission_with_initial_version(
            author=self.user,
            title="Статья с историей согласования",
            abstract="Аннотация",
            journal=self.journal,
            article_type=self.article_type,
            file=SimpleUploadedFile("article.txt", b"content"),
        )
        submit_submission(
            submission,
            direction=self.direction_main,
            route_template=self.route_template_main,
            submitted_by=self.user,
        )

        first_task = ApprovalTask.objects.get(
            workflow_step__workflow_run__submission=submission,
            status=ApprovalTaskStatus.ACTIVE,
        )
        request_revision(first_task, self.chair_head, comment="Нужно доработать статью.")
        add_submission_version(
            submission,
            self.user,
            SimpleUploadedFile("article_v2.txt", b"content-v2"),
            comment="Исправленная версия",
        )
        second_task = ApprovalTask.objects.get(
            workflow_step__workflow_run__submission=submission,
            status=ApprovalTaskStatus.ACTIVE,
        )
        approve_task(second_task, self.chair_head, comment="Теперь согласовано.")

        self.client.force_login(self.user)
        response = self.client.get(reverse("submissions:detail", args=[submission.pk]))

        self.assertContains(response, "На доработку")
        self.assertContains(response, "Теперь согласовано.")
        self.assertContains(response, "Нужно доработать статью.")
        self.assertIn(
            "Согласовано",
            [entry["result"] for entry in response.context["primary_workflow_run"].commentary_entries],
        )

    @patch("apps.workflow.services.start_route_review_workflow")
    def test_submit_submission_starts_route_review_after_checks(self, mocked_start_route_review):
        submission = Submission.objects.create(
            title="Статья с маршрутом",
            author=self.user,
            journal=self.journal,
            article_type=self.article_type,
            status=SubmissionStatus.SUBMITTED,
        )
        version = SubmissionVersion.objects.create(
            submission=submission,
            version_number=1,
            file=SimpleUploadedFile("article.txt", b"content"),
            uploaded_by=self.user,
        )
        submission.current_version = version
        submission.save(update_fields=["current_version", "updated_at"])

        submit_submission(
            submission,
            direction=self.direction_main,
            route_template=self.route_template_main,
            submitted_by=self.user,
        )

        submission.refresh_from_db()
        self.assertEqual(submission.direction, self.direction_main)
        self.assertEqual(submission.route_template, self.route_template_main)
        mocked_start_route_review.assert_called_once_with(submission)

    def test_submit_submission_rejects_nonselectable_route_template(self):
        submission = Submission.objects.create(
            title="Статья с ограниченным маршрутом",
            author=self.user,
            journal=self.journal,
            article_type=self.article_type,
            status=SubmissionStatus.SUBMITTED,
        )
        version = SubmissionVersion.objects.create(
            submission=submission,
            version_number=1,
            file=SimpleUploadedFile("article.txt", b"content"),
            uploaded_by=self.user,
        )
        submission.current_version = version
        submission.save(update_fields=["current_version", "updated_at"])

        with self.settings(
            SUBMISSION_SELECTABLE_ROUTE_TEMPLATE_IDS=(self.route_template_other.id,),
        ):
            with self.assertRaisesMessage(
                ValueError,
                "Выбранный маршрут недоступен для отправки материала.",
            ):
                submit_submission(
                    submission,
                    direction=self.direction_main,
                    route_template=self.route_template_main,
                    submitted_by=self.user,
                )

    def test_chair_head_can_open_chair_scope_submission_list(self):
        colleague = get_user_model().objects.create_user(
            username="chair_colleague",
            password="1234",
            chair_org_unit=self.chair_org_unit,
        )
        submission = Submission.objects.create(
            title="Материал кафедры",
            author=colleague,
            journal=self.journal,
            article_type=self.article_type,
            direction=self.direction_main,
            route_template=self.route_template_main,
            status=SubmissionStatus.IN_REVIEW,
            submitted_at=timezone.now(),
        )
        submission.authors.add(colleague)

        self.client.force_login(self.chair_head)
        response = self.client.get(reverse("submissions:list"), {"owner": "chair"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_owner"], "chair")
        self.assertContains(response, "Материалы моей кафедры")
        self.assertContains(response, "Материал кафедры")

    def test_author_does_not_see_route_until_chair_head_approves(self):
        RouteStepTemplate.objects.create(
            route_template=self.route_template_main,
            order=1,
            name="Основной эксперт",
            assignee_kind=AssigneeKind.FIXED_UNIT_GROUP,
            target_unit=OrgUnit.objects.create(name="Группа скрытого маршрута", code="hidden-route-unit"),
            target_group=Group.objects.create(name="Роль скрытого маршрута"),
        )
        self.route_template_main.step_templates.first().target_unit.available_roles.add(
            self.route_template_main.step_templates.first().target_group
        )
        submission = Submission.objects.create(
            title="Статья до одобрения заведующим",
            author=self.user,
            journal=self.journal,
            article_type=self.article_type,
            status=SubmissionStatus.SUBMITTED,
        )
        version = SubmissionVersion.objects.create(
            submission=submission,
            version_number=1,
            file=SimpleUploadedFile("article.txt", b"content"),
            uploaded_by=self.user,
        )
        submission.current_version = version
        submission.save(update_fields=["current_version", "updated_at"])
        submit_submission(
            submission,
            direction=self.direction_main,
            route_template=self.route_template_main,
            submitted_by=self.user,
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse("submissions:detail", args=[submission.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_view_route_details"])
        self.assertContains(response, "Маршрут станет доступен автору после проверки и одобрения заведующим кафедрой.")
        self.assertNotContains(response, "Основной эксперт")

    def test_chair_head_detail_contains_preview_payload_for_direction_switching(self):
        reviewer_group = Group.objects.create(name="Эксперт базового маршрута")
        reviewer_unit = OrgUnit.objects.create(name="Институт базового маршрута", code="base-route-unit")
        reviewer_unit.available_roles.add(reviewer_group)
        reviewer = get_user_model().objects.create_user(
            username="base_route_reviewer",
            password="1234",
            org_unit=reviewer_unit,
        )
        reviewer.groups.add(reviewer_group)
        base_route_template = RouteTemplate.objects.create(
            name="Базовый маршрут: Монография",
            direction=None,
            article_type=self.article_type,
            is_active=True,
        )
        RouteStepTemplate.objects.create(
            route_template=base_route_template,
            order=1,
            name="Тематический эксперт",
            assignee_kind=AssigneeKind.FIXED_UNIT_GROUP,
            target_unit=reviewer_unit,
            target_group=reviewer_group,
            target_user=reviewer,
        )
        submission = Submission.objects.create(
            title="Монография для смены области",
            author=self.user,
            journal=self.journal,
            article_type=self.article_type,
            direction=self.direction_main,
            route_template=base_route_template,
            status=SubmissionStatus.SUBMITTED,
        )
        version = SubmissionVersion.objects.create(
            submission=submission,
            version_number=1,
            file=SimpleUploadedFile("monograph.txt", b"content"),
            uploaded_by=self.user,
        )
        submission.current_version = version
        submission.save(update_fields=["current_version", "updated_at"])
        submit_submission(
            submission,
            direction=self.direction_main,
            route_template=base_route_template,
            submitted_by=self.user,
        )

        self.client.force_login(self.chair_head)
        response = self.client.get(reverse("submissions:detail", args=[submission.pk]))

        self.assertEqual(response.status_code, 200)
        payload = response.context["route_review_preview_payload"]
        self.assertIsNotNone(payload)
        self.assertIn(str(self.direction_main.id), payload["previewsByDirection"])
        self.assertIn(str(self.direction_other.id), payload["previewsByDirection"])
        self.assertEqual(
            payload["previewsByDirection"][str(self.direction_other.id)][0]["direction_name"],
            self.direction_other.name,
        )
        self.assertContains(response, "route-review-preview-data")

    def test_chair_head_inbox_keeps_single_submission_link_for_route_review(self):
        RouteStepTemplate.objects.create(
            route_template=self.route_template_main,
            order=1,
            name="Основной эксперт",
            assignee_kind=AssigneeKind.FIXED_UNIT_GROUP,
            target_unit=OrgUnit.objects.create(name="Группа маршрута inbox", code="route-inbox-unit"),
            target_group=Group.objects.create(name="Роль маршрута inbox"),
        )
        self.route_template_main.step_templates.first().target_unit.available_roles.add(
            self.route_template_main.step_templates.first().target_group
        )
        submission = Submission.objects.create(
            title="Статья для списка входящих",
            author=self.user,
            journal=self.journal,
            article_type=self.article_type,
            status=SubmissionStatus.SUBMITTED,
        )
        version = SubmissionVersion.objects.create(
            submission=submission,
            version_number=1,
            file=SimpleUploadedFile("article.txt", b"content"),
            uploaded_by=self.user,
        )
        submission.current_version = version
        submission.save(update_fields=["current_version", "updated_at"])
        submit_submission(
            submission,
            direction=self.direction_main,
            route_template=self.route_template_main,
            submitted_by=self.user,
        )

        route_review_task = ApprovalTask.objects.get(
            workflow_step__workflow_run__submission=submission,
            status=ApprovalTaskStatus.ACTIVE,
        )

        self.client.force_login(self.chair_head)
        response = self.client.get(reverse("workflow:inbox"))

        self.assertEqual(response.status_code, 200)
        response_html = response.content.decode("utf-8")
        submission_url = reverse("submissions:detail", args=[submission.pk])
        self.assertIn(f'<strong>#{route_review_task.id}</strong>', response_html)
        self.assertNotIn(
            f'<a href="{submission_url}"><strong>#{route_review_task.id}</strong></a>',
            response_html,
        )
        self.assertEqual(response_html.count(f'href="{submission_url}"'), 1)

    def test_chair_head_can_update_route_and_launch_main_route_from_submission_detail(self):
        RouteStepTemplate.objects.create(
            route_template=self.route_template_main,
            order=1,
            name="Основной эксперт",
            assignee_kind=AssigneeKind.FIXED_UNIT_GROUP,
            target_unit=OrgUnit.objects.create(name="Группа основного эксперта", code="main-review-unit"),
            target_group=Group.objects.create(name="Основной эксперт роли"),
        )
        RouteStepTemplate.objects.create(
            route_template=self.route_template_other,
            order=1,
            name="Другой эксперт",
            assignee_kind=AssigneeKind.FIXED_UNIT_GROUP,
            target_unit=OrgUnit.objects.create(name="Группа другого эксперта", code="other-review-unit"),
            target_group=Group.objects.create(name="Другой эксперт роли"),
        )
        self.route_template_main.step_templates.first().target_unit.available_roles.add(
            self.route_template_main.step_templates.first().target_group
        )
        self.route_template_other.step_templates.first().target_unit.available_roles.add(
            self.route_template_other.step_templates.first().target_group
        )

        submission = Submission.objects.create(
            title="Статья для проверки маршрута кафедрой",
            author=self.user,
            journal=self.journal,
            article_type=self.article_type,
            status=SubmissionStatus.SUBMITTED,
        )
        version = SubmissionVersion.objects.create(
            submission=submission,
            version_number=1,
            file=SimpleUploadedFile("article.txt", b"content"),
            uploaded_by=self.user,
        )
        submission.current_version = version
        submission.save(update_fields=["current_version", "updated_at"])
        submit_submission(
            submission,
            direction=self.direction_main,
            route_template=self.route_template_main,
            submitted_by=self.user,
        )

        self.client.force_login(self.chair_head)
        detail_response = self.client.get(reverse("submissions:detail", args=[submission.pk]))
        self.assertContains(detail_response, "Все правильно, запустить дальше")
        self.assertContains(detail_response, "Предварительный основной маршрут")
        self.assertContains(
            detail_response,
            "Первый этап ниже уже активен. Остальные шаги запустятся после подтверждения кафедрой.",
        )
        self.assertContains(detail_response, "Основной эксперт")
        self.assertNotContains(detail_response, "После проверки вернитесь к")
        detail_html = detail_response.content.decode("utf-8")
        self.assertLess(detail_html.index("Маршрут согласования"), detail_html.index("Проверки"))
        self.assertGreater(
            detail_html.index("Все правильно, запустить дальше"),
            detail_html.index("Фактические результаты этапов"),
        )
        self.assertGreater(
            detail_html.index("Все правильно, запустить дальше"),
            detail_html.index("Проверки"),
        )

        response = self.client.post(
            reverse("submissions:update_route", args=[submission.pk]),
            {
                "direction": str(self.direction_other.id),
                "route_template": str(self.route_template_other.id),
            },
        )

        self.assertEqual(response.status_code, 302)
        submission.refresh_from_db()
        workflow_run = submission.workflow_runs.get()
        active_task = ApprovalTask.objects.get(
            workflow_step__workflow_run=workflow_run,
            status=ApprovalTaskStatus.ACTIVE,
        )
        self.assertEqual(submission.direction, self.direction_other)
        self.assertEqual(submission.route_template, self.route_template_other)
        self.assertEqual(submission.status, SubmissionStatus.IN_REVIEW)
        self.assertEqual(workflow_run.route_template, self.route_template_other)
        self.assertFalse(workflow_run.awaiting_route_approval)
        self.assertEqual(workflow_run.current_step.order, 2)
        self.assertEqual(active_task.workflow_step.name, "Другой эксперт")

        reviewer = get_user_model().objects.create_user(
            username="active_submission_reviewer",
            password="1234",
            org_unit=active_task.assigned_unit,
        )
        reviewer.groups.add(active_task.assigned_group)
        self.client.force_login(reviewer)
        reviewer_response = self.client.get(reverse("submissions:detail", args=[submission.pk]))
        reviewer_html = reviewer_response.content.decode("utf-8")
        task_url = reverse("workflow:task_detail", args=[active_task.pk])

        self.assertEqual(reviewer_response.status_code, 200)
        self.assertContains(reviewer_response, "Материал ожидает вашей проверки")
        self.assertContains(reviewer_response, f'href="{task_url}">Проверить</a>')
        self.assertGreater(
            reviewer_html.index("Материал ожидает вашей проверки"),
            reviewer_html.index("Проверки"),
        )


@override_settings(
    SUBMISSION_SELECTABLE_ROUTE_TEMPLATE_IDS=(),
    SUBMISSION_ROUTE_SUGGESTION_ENABLED=False,
    SUBMISSION_CHECKS_ASYNC=True,
)
class SubmissionAsyncCheckQueueTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="async_author", password="1234")
        self.journal = Journal.objects.create(name="Асинхронный журнал")
        self.article_type = ArticleType.objects.create(code="async-article", name="Асинхронная статья")

    @patch("apps.checks.services.launch_submission_checks_process")
    def test_create_submission_returns_immediately_and_prepares_pending_checks(self, mocked_launch):
        with self.captureOnCommitCallbacks(execute=True):
            submission = create_submission_with_initial_version(
                author=self.user,
                title="Асинхронная загрузка",
                abstract="Материал для фоновых автопроверок.",
                journal=self.journal,
                article_type=self.article_type,
                file=SimpleUploadedFile("article.txt", b"content"),
            )

        submission.refresh_from_db()
        current_version_runs = list(
            submission.check_runs.filter(version=submission.current_version).select_related("check_definition")
        )

        self.assertEqual(submission.status, SubmissionStatus.AUTO_CHECKING)
        self.assertEqual(len(current_version_runs), 5)
        self.assertTrue(all(run.status == CheckRunStatus.PENDING for run in current_version_runs))
        mocked_launch.assert_called_once_with(submission.id, submission.current_version_id, False)


@override_settings(
    SUBMISSION_SELECTABLE_ROUTE_TEMPLATE_IDS=(),
    SUBMISSION_CHECKS_ASYNC=False,
)
class SubmissionAutomaticRouteSelectionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="auto_route_author", password="1234")
        self.journal = Journal.objects.create(name="Журнал автоподбора")
        self.article_type = ArticleType.objects.create(code="auto-route-type", name="Автостатья")
        self.direction_main = Direction.objects.create(code="auto-main-route", name="Основное направление")
        self.direction_other = Direction.objects.create(code="auto-other-route", name="Другое направление")
        self.route_template_main = RouteTemplate.objects.create(
            name="Основной маршрут",
            direction=self.direction_main,
            article_type=self.article_type,
            is_active=True,
        )
        self.route_template_other = RouteTemplate.objects.create(
            name="Другой маршрут",
            direction=self.direction_other,
            article_type=self.article_type,
            is_active=True,
        )
        self.subject_area_payload = {
            "matched": True,
            "source": "gemini",
            "message": "Gemini определил предметную область по материалу.",
            "direction_code": self.direction_other.code,
            "direction_name": self.direction_other.name,
            "confidence": 92,
            "reasoning": "В тексте преобладает тематика второго направления.",
            "details": "",
        }

    @patch("apps.checks.services.detect_direction_for_submission")
    def test_create_submission_auto_selects_area_and_route(self, mocked_detect_direction):
        mocked_detect_direction.return_value = self.subject_area_payload

        submission = create_submission_with_initial_version(
            author=self.user,
            title="Материал для автоподбора области",
            abstract="Тематика относится ко второму направлению.",
            journal=self.journal,
            article_type=self.article_type,
            file=SimpleUploadedFile("article.txt", b"content"),
        )

        submission.refresh_from_db()
        subject_area_run = submission.check_runs.filter(
            check_definition__code="subject_area_detection",
            version=submission.current_version,
        ).first()

        self.assertEqual(submission.direction, self.direction_other)
        self.assertEqual(submission.route_template, self.route_template_other)
        self.assertIsNotNone(subject_area_run)
        self.assertEqual(subject_area_run.status, CheckRunStatus.PASSED)
        self.assertEqual(subject_area_run.result_payload["direction_code"], self.direction_other.code)

    @patch("apps.checks.services.detect_direction_for_submission")
    def test_submission_detail_hides_manual_route_form_when_area_detected(self, mocked_detect_direction):
        mocked_detect_direction.return_value = self.subject_area_payload
        submission = create_submission_with_initial_version(
            author=self.user,
            title="Материал без ручного выбора области",
            abstract="Тематика относится ко второму направлению.",
            journal=self.journal,
            article_type=self.article_type,
            file=SimpleUploadedFile("article.txt", b"content"),
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse("submissions:detail", args=[submission.pk]))

        self.assertTrue(response.context["route_selection_ready"])
        self.assertFalse(response.context["can_view_route_details"])
        self.assertIsNone(response.context["selected_route_preview_template"])
        self.assertContains(response, "После отправки система автоматически использует определенную область экспертизы")
        self.assertNotContains(response, '<div class="submission-meta-label">Область экспертизы</div>', html=False)
        self.assertNotContains(response, '<div class="submission-meta-label">Маршрут</div>', html=False)
        self.assertNotContains(response, "data-field-role=\"direction-select\"")
        self.assertNotContains(response, "route-picker")
        detail_html = response.content.decode("utf-8")
        self.assertLess(detail_html.index("Проверки"), detail_html.index("Отправить в согласование"))

    @patch("apps.checks.services.detect_direction_for_submission")
    @patch("apps.workflow.services.start_route_review_workflow")
    def test_submit_submission_uses_auto_selected_area_and_route_without_manual_fields(
        self,
        mocked_start_route_review,
        mocked_detect_direction,
    ):
        mocked_detect_direction.return_value = self.subject_area_payload
        submission = create_submission_with_initial_version(
            author=self.user,
            title="Материал для автоматической отправки",
            abstract="Тематика относится ко второму направлению.",
            journal=self.journal,
            article_type=self.article_type,
            file=SimpleUploadedFile("article.txt", b"content"),
        )

        submit_submission(submission, submitted_by=self.user)

        submission.refresh_from_db()
        self.assertEqual(submission.direction, self.direction_other)
        self.assertEqual(submission.route_template, self.route_template_other)
        mocked_start_route_review.assert_called_once_with(submission)


@override_settings(
    SUBMISSION_SELECTABLE_ROUTE_TEMPLATE_IDS=(),
    SUBMISSION_ROUTE_SUGGESTION_ENABLED=True,
    GEMINI_API_KEY="test-key",
)
class SubmissionSubjectAreaDetectionTests(TestCase):
    def setUp(self):
        self.article_type = ArticleType.objects.create(code="subject-area-type", name="Тестовый материал")
        self.direction = Direction.objects.create(
            code="biotechnical-systems",
            name="Биотехнические системы и технологии",
        )
        self.other_direction = Direction.objects.create(
            code="informatics-security",
            name="Информатика, вычислительная техника и информационная безопасность",
        )

    @patch("apps.submissions.subject_area._call_gemini")
    def test_detect_direction_accepts_truncated_json_from_gemini(self, mocked_call_gemini):
        mocked_call_gemini.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": (
                                    '{\n'
                                    '  "direction_code": "biotechnical-systems",\n'
                                    '  "confidence": 0.95,\n'
                                    '  "reasoning": "Тематика относится к биотехническим системам."'
                                )
                            }
                        ]
                    }
                }
            ]
        }
        submission = Submission(
            title="Алгоритм анализа ОКТ-изображений",
            abstract="Материал про биологические жидкости, офтальмологию и ОКТ.",
            article_type=self.article_type,
        )

        payload = detect_direction_for_submission(
            submission,
            directions=Direction.objects.filter(id__in=[self.direction.id, self.other_direction.id]),
        )

        self.assertTrue(payload["matched"])
        self.assertEqual(payload["direction_code"], self.direction.code)
        self.assertEqual(payload["direction_name"], self.direction.name)
        self.assertEqual(payload["confidence"], 95)

    @patch("apps.submissions.subject_area._call_gemini")
    def test_detect_direction_falls_back_to_local_keywords_when_gemini_times_out(self, mocked_call_gemini):
        mocked_call_gemini.side_effect = TimeoutError("The read operation timed out")
        submission = Submission(
            title="Алгоритм анализа ОКТ-изображений для картирования потоков биологических жидкостей",
            abstract=(
                "Материал посвящен офтальмологическим приложениям оптической когерентной томографии, "
                "доплеровскому картированию и визуализации биологических жидкостей."
            ),
            article_type=self.article_type,
        )

        payload = detect_direction_for_submission(
            submission,
            directions=Direction.objects.filter(id__in=[self.direction.id, self.other_direction.id]),
        )

        self.assertTrue(payload["matched"])
        self.assertEqual(payload["source"], "local_keywords")
        self.assertEqual(payload["direction_code"], self.direction.code)
        self.assertEqual(payload["direction_name"], self.direction.name)
        self.assertIn("Gemini недоступен", payload["message"])


@override_settings(
    SUBMISSION_SELECTABLE_ROUTE_TEMPLATE_IDS=(),
    SUBMISSION_ROUTE_SUGGESTION_ENABLED=False,
    SUBMISSION_CHECKS_ASYNC=False,
)
class SubmissionAppealFlowTests(TestCase):
    def setUp(self):
        self.author = get_user_model().objects.create_user(username="appeal_author", password="1234")
        self.reviewer_group = Group.objects.create(name="Апелляционный рецензент")
        self.reviewer_unit = OrgUnit.objects.create(name="Научная комиссия", code="science-council")
        self.reviewer_unit.available_roles.add(self.reviewer_group)
        self.reviewer = get_user_model().objects.create_user(
            username="appeal_reviewer",
            password="1234",
            org_unit=self.reviewer_unit,
        )
        self.reviewer.groups.add(self.reviewer_group)
        self.journal = Journal.objects.create(name="Журнал апелляций")
        self.article_type = ArticleType.objects.create(code="appeal-article", name="Статья для апелляции")
        self.direction = Direction.objects.create(code="appeal-direction", name="Направление апелляции")
        self.route_template = RouteTemplate.objects.create(
            name="Маршрут апелляции",
            direction=self.direction,
            is_active=True,
        )
        self.submission = Submission.objects.create(
            title="Отклоненная статья",
            author=self.author,
            journal=self.journal,
            article_type=self.article_type,
            direction=self.direction,
            route_template=self.route_template,
            status=SubmissionStatus.REJECTED,
        )
        self.workflow_run = WorkflowRun.objects.create(
            submission=self.submission,
            route_template=self.route_template,
            status=WorkflowRunStatus.REJECTED,
        )
        self.rejected_step = WorkflowStep.objects.create(
            workflow_run=self.workflow_run,
            order=1,
            name="Первый этап",
            assignee_kind=AssigneeKind.FIXED_UNIT_GROUP,
            assigned_unit=self.reviewer_unit,
            assigned_group=self.reviewer_group,
            assigned_user=self.reviewer,
            can_reject=True,
            can_request_revision=False,
            status=WorkflowStepStatus.REJECTED,
        )
        self.pending_step = WorkflowStep.objects.create(
            workflow_run=self.workflow_run,
            order=2,
            name="Следующий этап",
            assignee_kind=AssigneeKind.FIXED_UNIT_GROUP,
            assigned_unit=self.reviewer_unit,
            assigned_group=self.reviewer_group,
            assigned_user=self.reviewer,
            can_reject=True,
            can_request_revision=False,
            status=WorkflowStepStatus.PENDING,
        )
        self.rejected_task = ApprovalTask.objects.create(
            workflow_step=self.rejected_step,
            status=ApprovalTaskStatus.REJECTED,
            assigned_unit=self.reviewer_unit,
            assigned_group=self.reviewer_group,
            assigned_user=self.reviewer,
        )

    def test_author_can_submit_single_appeal_after_rejection(self):
        appeal = submit_submission_appeal(
            self.submission,
            self.author,
            comment="Прошу пересмотреть решение.",
        )

        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, SubmissionStatus.APPEAL_PENDING)
        self.assertEqual(appeal.status, SubmissionAppealStatus.PENDING)
        self.assertEqual(appeal.reviewer, self.reviewer)


        with self.assertRaisesMessage(ValueError, "Апелляцию по этой заявке уже подавали."):
            submit_submission_appeal(
                self.submission,
                self.author,
                comment="Вторая попытка",
            )

    def test_approved_appeal_reactivates_route(self):
        appeal = submit_submission_appeal(
            self.submission,
            self.author,
            comment="Прошу пересмотреть решение.",
        )

        approve_submission_appeal(appeal, self.reviewer, comment="Готов продолжить согласование.")

        appeal.refresh_from_db()
        self.submission.refresh_from_db()
        self.workflow_run.refresh_from_db()
        self.pending_step.refresh_from_db()

        self.assertEqual(appeal.status, SubmissionAppealStatus.APPROVED)
        self.assertEqual(self.submission.status, SubmissionStatus.IN_REVIEW)
        self.assertEqual(self.workflow_run.status, WorkflowRunStatus.ACTIVE)
        self.assertEqual(self.workflow_run.current_step_id, self.pending_step.id)
        self.assertEqual(self.pending_step.status, WorkflowStepStatus.ACTIVE)
        self.assertTrue(
            ApprovalTask.objects.filter(
                workflow_step=self.pending_step,
                status=ApprovalTaskStatus.ACTIVE,
            ).exists()
        )

    def test_rejected_appeal_keeps_submission_rejected(self):
        appeal = submit_submission_appeal(
            self.submission,
            self.author,
            comment="Прошу пересмотреть решение.",
        )

        reject_submission_appeal(appeal, self.reviewer, comment="Оснований менять решение нет.")

        appeal.refresh_from_db()
        self.submission.refresh_from_db()

        self.assertEqual(appeal.status, SubmissionAppealStatus.REJECTED)
        self.assertEqual(self.submission.status, SubmissionStatus.REJECTED)

    def test_group_rejection_uses_actual_decision_actor_as_appeal_reviewer(self):
        self.rejected_task.assigned_user = None
        self.rejected_task.save(update_fields=["assigned_user"])
        TaskDecision.objects.create(
            task=self.rejected_task,
            actor=self.reviewer,
            decision=TaskDecisionType.REJECT,
            comment="Отклонено тематическим экспертом.",
        )

        appeal = submit_submission_appeal(
            self.submission,
            self.author,
            comment="Прошу пересмотреть решение.",
        )

        self.assertEqual(appeal.reviewer, self.reviewer)


class SubmissionVersionPreviewTests(TestCase):
    def setUp(self):
        self.media_directory = TemporaryDirectory()
        self.media_override = override_settings(MEDIA_ROOT=self.media_directory.name)
        self.media_override.enable()

        self.author = get_user_model().objects.create_user(username="preview-author", password="1234")
        self.outsider = get_user_model().objects.create_user(username="preview-outsider", password="1234")
        self.journal = Journal.objects.create(name="Журнал предпросмотра")
        self.article_type = ArticleType.objects.create(code="preview", name="Материал предпросмотра")
        self.submission = Submission.objects.create(
            title="Материал для просмотра",
            author=self.author,
            journal=self.journal,
            article_type=self.article_type,
            status=SubmissionStatus.DRAFT,
        )
        self.submission.authors.add(self.author)

    def tearDown(self):
        self.media_override.disable()
        self.media_directory.cleanup()

    def _create_version(self, filename, content, content_type="application/octet-stream"):
        return SubmissionVersion.objects.create(
            submission=self.submission,
            version_number=self.submission.versions.count() + 1,
            file=SimpleUploadedFile(filename, content, content_type=content_type),
            uploaded_by=self.author,
        )

    def test_detail_shows_preview_action_for_supported_file(self):
        version = self._create_version("article.txt", "Текст статьи".encode("utf-8"), "text/plain")
        self.client.force_login(self.author)

        response = self.client.get(reverse("submissions:detail", args=[self.submission.pk]))

        self.assertContains(response, "Посмотреть")
        self.assertContains(
            response,
            reverse("submissions:version_preview", args=[self.submission.pk, version.pk]),
        )

    def test_text_preview_decodes_content_and_escapes_html(self):
        version = self._create_version(
            "article.txt",
            "<script>alert(1)</script>\nТекст статьи".encode("utf-8"),
            "text/plain",
        )
        self.client.force_login(self.author)

        response = self.client.get(
            reverse("submissions:version_preview", args=[self.submission.pk, version.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Текст статьи")
        self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;")
        self.assertNotContains(response, "<script>alert(1)</script>")

    @override_settings(DOCUMENT_PREVIEW_CONVERT_DOCX_TO_PDF=False)
    def test_docx_preview_renders_headings_paragraphs_and_tables(self):
        version = self._create_version(
            "article.docx",
            _build_preview_docx(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        self.client.force_login(self.author)

        response = self.client.get(
            reverse("submissions:version_preview", args=[self.submission.pk, version.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Название документа")
        self.assertContains(response, "Первый абзац для просмотра.")
        self.assertContains(response, "Колонка 1")
        self.assertContains(response, "Значение 2")
        self.assertContains(response, '<div class="word-heading level-1">', html=False)
        self.assertContains(response, "<table>", html=False)

    @patch("apps.submissions.views.build_word_document_pdf")
    def test_docx_preview_uses_converted_pdf_when_available(self, mocked_converter):
        version = self._create_version(
            "article.docx",
            _build_preview_docx(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        converted_pdf = Path(self.media_directory.name) / "converted-docx.pdf"
        converted_pdf.write_bytes(b"%PDF-1.4\n" + (b"0" * 120))
        mocked_converter.return_value = converted_pdf
        self.client.force_login(self.author)

        preview_url = reverse("submissions:version_preview", args=[self.submission.pk, version.pk])
        content_url = reverse("submissions:version_content", args=[self.submission.pk, version.pk])
        preview_response = self.client.get(preview_url)
        content_response = self.client.get(content_url)

        self.assertContains(preview_response, content_url)
        self.assertEqual(content_response.status_code, 200)
        self.assertEqual(content_response["Content-Type"], "application/pdf")
        self.assertIn("private", content_response["Cache-Control"])
        self.assertIn("no-store", content_response["Cache-Control"])
        self.assertTrue(b"".join(content_response.streaming_content).startswith(b"%PDF-"))
        content_response.close()

    def test_pdf_preview_uses_protected_inline_content(self):
        version = self._create_version("article.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")
        self.client.force_login(self.author)
        preview_url = reverse("submissions:version_preview", args=[self.submission.pk, version.pk])
        content_url = reverse("submissions:version_content", args=[self.submission.pk, version.pk])

        preview_response = self.client.get(preview_url)
        content_response = self.client.get(content_url)

        self.assertContains(preview_response, content_url)
        self.assertEqual(content_response.status_code, 200)
        self.assertEqual(content_response["Content-Type"], "application/pdf")
        self.assertEqual(content_response["X-Frame-Options"], "SAMEORIGIN")
        self.assertEqual(b"".join(content_response.streaming_content), b"%PDF-1.4\n%%EOF")

    def test_original_download_is_protected_and_preserves_filename(self):
        version = self._create_version("article.pdf", b"private-pdf", "application/pdf")
        download_url = reverse(
            "submissions:version_download",
            args=[self.submission.pk, version.pk],
        )

        self.client.force_login(self.author)
        response = self.client.get(download_url)

        self.assertEqual(response.status_code, 200)
        self.assertIn('filename="article.pdf"', response["Content-Disposition"])
        self.assertEqual(b"".join(response.streaming_content), b"private-pdf")
        response.close()

        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(download_url).status_code, 404)

    @patch("apps.submissions.views.build_legacy_doc_pdf")
    def test_legacy_doc_preview_uses_converted_pdf(self, mocked_converter):
        version = self._create_version("article.doc", b"legacy-word-content", "application/msword")
        converted_pdf = Path(self.media_directory.name) / "converted.pdf"
        converted_pdf.write_bytes(b"%PDF-1.4\n" + (b"0" * 120))
        mocked_converter.return_value = converted_pdf
        self.client.force_login(self.author)

        preview_response = self.client.get(
            reverse("submissions:version_preview", args=[self.submission.pk, version.pk])
        )
        content_response = self.client.get(
            reverse("submissions:version_content", args=[self.submission.pk, version.pk])
        )

        self.assertEqual(preview_response.status_code, 200)
        self.assertContains(preview_response, "DOC")
        self.assertEqual(content_response.status_code, 200)
        self.assertEqual(content_response["Content-Type"], "application/pdf")
        self.assertTrue(b"".join(content_response.streaming_content).startswith(b"%PDF-"))
        content_response.close()
        self.assertTrue(mocked_converter.called)

    def test_outsider_cannot_preview_or_read_file(self):
        version = self._create_version("article.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")
        self.client.force_login(self.outsider)

        preview_response = self.client.get(
            reverse("submissions:version_preview", args=[self.submission.pk, version.pk])
        )
        content_response = self.client.get(
            reverse("submissions:version_content", args=[self.submission.pk, version.pk])
        )

        self.assertEqual(preview_response.status_code, 404)
        self.assertEqual(content_response.status_code, 404)


class WordConversionEnvironmentTests(SimpleTestCase):
    @override_settings(LIBREOFFICE_BINARY="/usr/bin/libreoffice")
    @patch("apps.submissions.document_preview.subprocess.run")
    def test_libreoffice_receives_writable_home_and_temp_directories(self, mocked_run):
        def create_converted_pdf(command, **_kwargs):
            output_directory = Path(command[command.index("--outdir") + 1])
            (output_directory / "source.pdf").write_bytes(b"%PDF-1.4\n" + (b"0" * 120))
            return SimpleNamespace(returncode=0)

        mocked_run.side_effect = create_converted_pdf
        with TemporaryDirectory() as temporary_directory:
            temporary_directory = Path(temporary_directory)
            source_path = temporary_directory / "source.docx"
            output_path = temporary_directory / "result.pdf"
            source_path.write_bytes(b"docx")

            _build_word_document_pdf_with_libreoffice(
                source_path=source_path,
                output_path=output_path,
                format_name="DOCX",
            )

        environment = mocked_run.call_args.kwargs["env"]
        self.assertEqual(Path(environment["HOME"]).name, "home")
        self.assertTrue(Path(environment["TMPDIR"]).name.startswith("word-preview-"))
        self.assertEqual(
            Path(mocked_run.call_args.kwargs["cwd"]),
            Path(environment["TMPDIR"]),
        )
