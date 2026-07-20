from django.conf import settings
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.roles import CHAIR_HEAD_ROLE_NAME
from apps.conclusions.models import ConclusionDocument
from apps.conclusions.services import PRORECTOR_ROLE_NAME
from apps.workflow.admin import RouteStepTemplateAdminForm
from apps.directory.models import ArticleType, Direction, Journal, OrgUnit
from apps.submissions.models import Submission, SubmissionStatus
from apps.workflow.models import (
    ApprovalTask,
    ApprovalTaskStatus,
    AssigneeKind,
    RouteStepDirectionAssignment,
    RouteStepTemplate,
    RouteTemplate,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowStep,
    WorkflowStepStatus,
)
from apps.workflow.selectors import (
    filter_tasks_by_scope,
    get_decision_history_queryset,
    get_visible_tasks_queryset,
)
from apps.workflow.services import select_route_template
from apps.workflow.services import (
    approve_task,
    insert_manual_step,
    request_revision,
    resume_or_start_workflow,
    start_route_review_workflow,
    start_workflow,
)


@override_settings(
    SUBMISSION_SELECTABLE_ROUTE_TEMPLATE_IDS=(),
)
class WorkflowRouteSelectionTests(TestCase):
    def setUp(self):
        self.author = User.objects.create_user(username="author", password="1234")
        self.journal = Journal.objects.create(name="Вестник тестирования")
        self.article_type = ArticleType.objects.create(code="article", name="Научная статья")
        self.article_type_theses = ArticleType.objects.create(code="theses", name="Тезисы доклада")
        self.direction_main = Direction.objects.create(code="main", name="Основное направление")
        self.direction_other = Direction.objects.create(code="other", name="Другое направление")

    def test_select_route_template_uses_submission_direction(self):
        expected_template = RouteTemplate.objects.create(
            name="Маршрут основного направления",
            direction=self.direction_main,
            article_type=self.article_type,
            priority=1,
            is_active=True,
        )
        RouteTemplate.objects.create(
            name="Маршрут другого направления с большим приоритетом",
            direction=self.direction_other,
            article_type=self.article_type,
            priority=100,
            is_active=True,
        )

        submission = Submission.objects.create(
            title="Тестовая заявка",
            author=self.author,
            journal=self.journal,
            article_type=self.article_type,
            direction=self.direction_main,
        )

        selected_template = select_route_template(submission)

        self.assertEqual(selected_template, expected_template)

    def test_select_route_template_uses_matching_material_type(self):
        expected_template = RouteTemplate.objects.create(
            name="Маршрут статьи",
            direction=self.direction_main,
            article_type=self.article_type,
            priority=1,
            is_active=True,
        )
        RouteTemplate.objects.create(
            name="Маршрут тезисов с большим приоритетом",
            direction=self.direction_main,
            article_type=self.article_type_theses,
            priority=100,
            is_active=True,
        )

        submission = Submission.objects.create(
            title="Тестовая заявка по типу материала",
            author=self.author,
            journal=self.journal,
            article_type=self.article_type,
            direction=self.direction_main,
        )

        selected_template = select_route_template(submission)

        self.assertEqual(selected_template, expected_template)

    def test_select_route_template_prefers_submission_route_template(self):
        RouteTemplate.objects.create(
            name="Маршрут основного направления с большим приоритетом",
            direction=self.direction_main,
            article_type=self.article_type,
            priority=100,
            is_active=True,
        )
        expected_template = RouteTemplate.objects.create(
            name="Маршрут, выбранный пользователем",
            direction=self.direction_main,
            article_type=self.article_type,
            priority=1,
            is_active=True,
        )

        submission = Submission.objects.create(
            title="Тестовая заявка с явным маршрутом",
            author=self.author,
            journal=self.journal,
            article_type=self.article_type,
            direction=self.direction_main,
            route_template=expected_template,
        )

        selected_template = select_route_template(submission)

        self.assertEqual(selected_template, expected_template)

    def test_select_route_template_prefers_base_material_template_over_direction_specific_copy(self):
        RouteTemplate.objects.create(
            name="Старый маршрут направления",
            direction=self.direction_main,
            article_type=self.article_type,
            priority=100,
            is_active=True,
        )
        expected_template = RouteTemplate.objects.create(
            name="Базовый маршрут статьи",
            direction=None,
            article_type=self.article_type,
            priority=1,
            is_active=True,
        )

        submission = Submission.objects.create(
            title="Тестовая заявка по базовому маршруту",
            author=self.author,
            journal=self.journal,
            article_type=self.article_type,
            direction=self.direction_main,
        )

        selected_template = select_route_template(submission)

        self.assertEqual(selected_template, expected_template)

    def test_start_workflow_allows_group_assignment_without_fixed_user(self):
        reviewer_unit = OrgUnit.objects.create(name="Эксперты по темам", code="topic-experts")
        reviewer_group = Group.objects.create(name="Эксперт по теме")
        reviewer_unit.available_roles.add(reviewer_group)
        route_template = RouteTemplate.objects.create(
            name="Маршрут статьи",
            direction=self.direction_main,
            is_active=True,
        )
        RouteStepTemplate.objects.create(
            route_template=route_template,
            order=1,
            name="Эксперт по теме",
            assignee_kind=AssigneeKind.FIXED_UNIT_GROUP,
            target_group=reviewer_group,
            target_unit=reviewer_unit,
            target_user=None,
        )

        submission = Submission.objects.create(
            title="Групповая экспертная заявка",
            author=self.author,
            journal=self.journal,
            article_type=self.article_type,
            direction=self.direction_main,
            route_template=route_template,
        )

        workflow_run = start_workflow(submission, route_template=route_template)
        task = ApprovalTask.objects.get(workflow_step__workflow_run=workflow_run)

        self.assertIsNone(task.assigned_user)
        self.assertEqual(task.assigned_group, reviewer_group)
        self.assertEqual(task.assigned_unit, reviewer_unit)

    def test_start_workflow_uses_direction_specific_assignment_for_base_template(self):
        default_unit = OrgUnit.objects.create(name="Резервная группа", code="fallback-group")
        default_group = Group.objects.create(name="Резервная роль")
        default_unit.available_roles.add(default_group)

        direction_unit = OrgUnit.objects.create(name="Тематические эксперты", code="direction-group")
        direction_group = Group.objects.create(name="Эксперт направления")
        direction_unit.available_roles.add(direction_group)
        direction_reviewer = User.objects.create_user(
            username="direction_reviewer",
            password="1234",
            org_unit=direction_unit,
        )
        direction_reviewer.groups.add(direction_group)

        route_template = RouteTemplate.objects.create(
            name="Базовый маршрут статьи",
            direction=None,
            article_type=self.article_type,
            is_active=True,
        )
        step_template = RouteStepTemplate.objects.create(
            route_template=route_template,
            order=1,
            name="Тематический эксперт",
            assignee_kind=AssigneeKind.FIXED_UNIT_GROUP,
            target_group=default_group,
            target_unit=default_unit,
            target_user=None,
        )
        RouteStepDirectionAssignment.objects.create(
            step_template=step_template,
            direction=self.direction_main,
            target_group=direction_group,
            target_unit=direction_unit,
            target_user=direction_reviewer,
        )

        submission = Submission.objects.create(
            title="Заявка с базовым шаблоном",
            author=self.author,
            journal=self.journal,
            article_type=self.article_type,
            direction=self.direction_main,
            route_template=route_template,
        )

        workflow_run = start_workflow(submission, route_template=route_template)
        task = ApprovalTask.objects.get(workflow_step__workflow_run=workflow_run)

        self.assertEqual(task.assigned_user, direction_reviewer)
        self.assertEqual(task.assigned_group, direction_group)
        self.assertEqual(task.assigned_unit, direction_unit)

    def test_start_workflow_resolves_author_chair_head_assignment(self):
        chair_org_unit = OrgUnit.objects.create(name='Кафедра "Тестовая кафедра"', code="chair-test")
        chair_head_role, _ = Group.objects.get_or_create(name=CHAIR_HEAD_ROLE_NAME)
        chair_org_unit.available_roles.add(chair_head_role)
        self.author.chair_org_unit = chair_org_unit
        self.author.save(update_fields=["chair_org_unit"])
        chair_head = User.objects.create_user(
            username="chair_head",
            password="1234",
            first_name="Мария",
            last_name="Иванова",
            chair_org_unit=chair_org_unit,
        )
        chair_head.groups.add(chair_head_role)

        route_template = RouteTemplate.objects.create(
            name="Маршрут с заведующим кафедрой",
            direction=self.direction_main,
            is_active=True,
        )
        RouteStepTemplate.objects.create(
            route_template=route_template,
            order=1,
            name="Заведующий кафедрой автора",
            assignee_kind=AssigneeKind.AUTHOR_CHAIR_HEAD,
        )

        submission = Submission.objects.create(
            title="Заявка на кафедральное согласование",
            author=self.author,
            journal=self.journal,
            article_type=self.article_type,
            direction=self.direction_main,
            route_template=route_template,
        )

        workflow_run = start_workflow(submission, route_template=route_template)
        task = ApprovalTask.objects.get(workflow_step__workflow_run=workflow_run)

        self.assertEqual(task.assigned_user, chair_head)
        self.assertEqual(task.assigned_group, chair_head_role)
        self.assertIsNone(task.assigned_unit)
        self.assertEqual(task.workflow_step.assignee_kind, AssigneeKind.AUTHOR_CHAIR_HEAD)

    def test_start_workflow_raises_for_chair_head_step_without_author_chair(self):
        route_template = RouteTemplate.objects.create(
            name="Маршрут без кафедры автора",
            direction=self.direction_main,
            is_active=True,
        )
        RouteStepTemplate.objects.create(
            route_template=route_template,
            order=1,
            name="Заведующий кафедрой автора",
            assignee_kind=AssigneeKind.AUTHOR_CHAIR_HEAD,
        )
        submission = Submission.objects.create(
            title="Заявка без кафедры",
            author=self.author,
            journal=self.journal,
            article_type=self.article_type,
            direction=self.direction_main,
            route_template=route_template,
        )

        with self.assertRaisesMessage(
            ValueError,
            "У отправителя не указана кафедра, поэтому нельзя выбрать заведующего кафедрой.",
        ):
            start_workflow(submission, route_template=route_template)

    def test_start_route_review_workflow_creates_chair_head_stage_before_main_route(self):
        chair_org_unit = OrgUnit.objects.create(name='Кафедра "Маршрут кафедры"', code="chair-route")
        chair_head_role, _ = Group.objects.get_or_create(name=CHAIR_HEAD_ROLE_NAME)
        chair_org_unit.available_roles.add(chair_head_role)
        self.author.chair_org_unit = chair_org_unit
        self.author.save(update_fields=["chair_org_unit"])
        chair_head = User.objects.create_user(
            username="route_chair_head",
            password="1234",
            chair_org_unit=chair_org_unit,
        )
        chair_head.groups.add(chair_head_role)

        reviewer_unit = OrgUnit.objects.create(name="Основной рецензент", code="main-review")
        reviewer_group = Group.objects.create(name="Основной рецензент роли")
        reviewer_unit.available_roles.add(reviewer_group)
        reviewer = User.objects.create_user(
            username="main_reviewer",
            password="1234",
            org_unit=reviewer_unit,
        )
        reviewer.groups.add(reviewer_group)

        route_template = RouteTemplate.objects.create(
            name="Основной маршрут после кафедры",
            direction=self.direction_main,
            is_active=True,
        )
        RouteStepTemplate.objects.create(
            route_template=route_template,
            order=1,
            name="Основной этап",
            assignee_kind=AssigneeKind.FIXED_UNIT_GROUP,
            target_unit=reviewer_unit,
            target_group=reviewer_group,
            target_user=reviewer,
        )
        submission = Submission.objects.create(
            title="Заявка с кафедральной проверкой маршрута",
            author=self.author,
            journal=self.journal,
            article_type=self.article_type,
            direction=self.direction_main,
            route_template=route_template,
            status=SubmissionStatus.SUBMITTED,
        )
        version = submission.versions.create(
            version_number=1,
            file=ContentFile(b"manuscript", name="manuscript.docx"),
            uploaded_by=self.author,
        )
        submission.current_version = version
        submission.save(update_fields=["current_version", "updated_at"])

        workflow_run = start_route_review_workflow(submission)
        active_task = ApprovalTask.objects.get(workflow_step__workflow_run=workflow_run, status=ApprovalTaskStatus.ACTIVE)

        submission.refresh_from_db()
        self.assertTrue(workflow_run.awaiting_route_approval)
        self.assertEqual(submission.status, SubmissionStatus.IN_REVIEW)
        self.assertEqual(workflow_run.steps.count(), 1)
        self.assertEqual(active_task.assigned_user, chair_head)
        self.assertEqual(active_task.assigned_group, chair_head_role)
        self.assertEqual(active_task.workflow_step.assignee_kind, AssigneeKind.AUTHOR_CHAIR_HEAD)

    def test_approving_route_review_stage_launches_main_route(self):
        chair_org_unit = OrgUnit.objects.create(name='Кафедра "Маршрут кафедры 2"', code="chair-route-2")
        chair_head_role, _ = Group.objects.get_or_create(name=CHAIR_HEAD_ROLE_NAME)
        chair_org_unit.available_roles.add(chair_head_role)
        self.author.chair_org_unit = chair_org_unit
        self.author.save(update_fields=["chair_org_unit"])
        chair_head = User.objects.create_user(
            username="route_chair_head_2",
            password="1234",
            chair_org_unit=chair_org_unit,
        )
        chair_head.groups.add(chair_head_role)

        reviewer_unit = OrgUnit.objects.create(name="Основной эксперт 2", code="main-review-2")
        reviewer_group = Group.objects.create(name="Основной эксперт роли 2")
        reviewer_unit.available_roles.add(reviewer_group)
        reviewer = User.objects.create_user(
            username="main_reviewer_2",
            password="1234",
            org_unit=reviewer_unit,
        )
        reviewer.groups.add(reviewer_group)

        route_template = RouteTemplate.objects.create(
            name="Маршрут после кафедральной проверки",
            direction=self.direction_main,
            is_active=True,
        )
        RouteStepTemplate.objects.create(
            route_template=route_template,
            order=1,
            name="Основной этап после кафедры",
            assignee_kind=AssigneeKind.FIXED_UNIT_GROUP,
            target_unit=reviewer_unit,
            target_group=reviewer_group,
            target_user=reviewer,
        )
        submission = Submission.objects.create(
            title="Запуск после одобрения кафедрой",
            author=self.author,
            journal=self.journal,
            article_type=self.article_type,
            direction=self.direction_main,
            route_template=route_template,
            status=SubmissionStatus.SUBMITTED,
        )
        version = submission.versions.create(
            version_number=1,
            file=ContentFile(b"manuscript", name="manuscript.docx"),
            uploaded_by=self.author,
        )
        submission.current_version = version
        submission.save(update_fields=["current_version", "updated_at"])

        workflow_run = start_route_review_workflow(submission)
        chair_task = ApprovalTask.objects.get(
            workflow_step__workflow_run=workflow_run,
            status=ApprovalTaskStatus.ACTIVE,
        )

        approve_task(chair_task, chair_head, comment="Маршрут кафедрой проверен.")

        workflow_run.refresh_from_db()
        submission.refresh_from_db()
        next_task = ApprovalTask.objects.get(
            workflow_step__workflow_run=workflow_run,
            status=ApprovalTaskStatus.ACTIVE,
        )

        self.assertFalse(workflow_run.awaiting_route_approval)
        self.assertEqual(submission.status, SubmissionStatus.IN_REVIEW)
        self.assertEqual(workflow_run.current_step.order, 2)
        self.assertEqual(workflow_run.steps.count(), 3)
        self.assertEqual(next_task.assigned_user, reviewer)
        self.assertEqual(next_task.assigned_group, reviewer_group)
        self.assertTrue(ConclusionDocument.objects.filter(workflow_run=workflow_run).exists())
        self.assertTrue(
            workflow_run.steps.filter(assigned_group__name=PRORECTOR_ROLE_NAME).exists()
        )


@override_settings(SUBMISSION_SELECTABLE_ROUTE_TEMPLATE_IDS=())
class WorkflowVisibilityTests(TestCase):
    def setUp(self):
        self.department = OrgUnit.objects.create(name="Научный отдел", code="science")
        self.other_department = OrgUnit.objects.create(name="Патентный отдел", code="patent")
        self.role = Group.objects.create(name="Согласующие научного отдела")
        self.direction = Direction.objects.create(code="science-route", name="Научное направление")
        self.journal = Journal.objects.create(name="Известия тестовой кафедры")
        self.article_type = ArticleType.objects.create(code="review", name="Обзорная статья")
        self.author = User.objects.create_user(username="author_visibility", password="1234")

        self.route_template = RouteTemplate.objects.create(
            name="Маршрут научного отдела",
            direction=self.direction,
            is_active=True,
        )
        self.step_template = RouteStepTemplate.objects.create(
            route_template=self.route_template,
            order=1,
            name="Проверка научным отделом",
            assignee_kind=AssigneeKind.FIXED_UNIT_GROUP,
            target_group=self.role,
            target_unit=self.department,
        )
        self.submission = Submission.objects.create(
            title="Заявка на видимость задач",
            author=self.author,
            journal=self.journal,
            article_type=self.article_type,
            direction=self.direction,
        )
        self.workflow_run = WorkflowRun.objects.create(
            submission=self.submission,
            route_template=self.route_template,
            status=WorkflowRunStatus.ACTIVE,
        )
        self.workflow_step = WorkflowStep.objects.create(
            workflow_run=self.workflow_run,
            step_template=self.step_template,
            order=1,
            name=self.step_template.name,
            assignee_kind=self.step_template.assignee_kind,
            assigned_group=self.role,
            assigned_unit=self.department,
            can_reject=True,
            can_request_revision=True,
            status=WorkflowStepStatus.ACTIVE,
        )
        self.task = ApprovalTask.objects.create(
            workflow_step=self.workflow_step,
            status=ApprovalTaskStatus.ACTIVE,
            assigned_group=self.role,
            assigned_unit=self.department,
        )

    def test_role_in_unit_task_visible_only_to_matching_role_and_unit(self):
        matching_user = User.objects.create_user(
            username="science_reviewer",
            password="1234",
            org_unit=self.department,
        )
        matching_user.groups.add(self.role)

        missing_role_user = User.objects.create_user(
            username="science_employee",
            password="1234",
            org_unit=self.department,
        )

        wrong_unit_user = User.objects.create_user(
            username="patent_reviewer",
            password="1234",
            org_unit=self.other_department,
        )
        wrong_unit_user.groups.add(self.role)

        self.assertTrue(get_visible_tasks_queryset(matching_user).filter(pk=self.task.pk).exists())
        self.assertFalse(get_visible_tasks_queryset(missing_role_user).filter(pk=self.task.pk).exists())
        self.assertFalse(get_visible_tasks_queryset(wrong_unit_user).filter(pk=self.task.pk).exists())

    def test_unit_scope_does_not_duplicate_role_tasks(self):
        matching_user = User.objects.create_user(
            username="science_unit_reviewer",
            password="1234",
            org_unit=self.department,
        )
        matching_user.groups.add(self.role)

        visible_tasks = get_visible_tasks_queryset(matching_user)

        self.assertTrue(filter_tasks_by_scope(visible_tasks, matching_user, f"role:{self.role.id}").filter(pk=self.task.pk).exists())
        self.assertFalse(filter_tasks_by_scope(visible_tasks, matching_user, "unit").filter(pk=self.task.pk).exists())


@override_settings(SUBMISSION_SELECTABLE_ROUTE_TEMPLATE_IDS=())
class WorkflowAssignmentOptionsTests(TestCase):
    def setUp(self):
        self.root = User.objects.create_superuser(
            username=settings.ROOT_ADMIN_USERNAME,
            password="root-secret",
            email="root@example.local",
        )
        self.group = OrgUnit.objects.create(name="Редакция", code="editorial")
        self.role = Group.objects.create(name="Главный редактор")
        self.other_role = Group.objects.create(name="Секретарь")
        self.group.available_roles.add(self.role)

        self.matching_user = User.objects.create_user(
            username="editor_match",
            password="1234",
            org_unit=self.group,
            first_name="Иван",
            last_name="Редактор",
        )
        self.matching_user.groups.add(self.role)

        self.other_role_user = User.objects.create_user(
            username="editor_other_role",
            password="1234",
            org_unit=self.group,
            first_name="Петр",
            last_name="Секретарь",
        )
        self.other_role_user.groups.add(self.other_role)

        self.other_group = OrgUnit.objects.create(name="Ректорат", code="rectorate")
        self.other_group.available_roles.add(self.role)
        self.other_group_user = User.objects.create_user(
            username="editor_other_group",
            password="1234",
            org_unit=self.other_group,
            first_name="Анна",
            last_name="Другая",
        )
        self.other_group_user.groups.add(self.role)

    def test_assignment_options_returns_roles_and_matching_users_for_root(self):
        self.client.force_login(self.root)

        response = self.client.get(
            reverse("workflow:assignment_options"),
            {"group_id": self.group.id, "role_id": self.role.id},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["roles"], [{"id": self.role.id, "name": self.role.name}])
        self.assertEqual(
            payload["users"],
            [{"id": self.matching_user.id, "name": str(self.matching_user)}],
        )

    def test_assignment_options_hidden_from_non_root(self):
        non_root = User.objects.create_user(username="ordinary", password="1234")
        self.client.force_login(non_root)

        response = self.client.get(reverse("workflow:assignment_options"), {"group_id": self.group.id})

        self.assertEqual(response.status_code, 404)

    def test_admin_form_limits_users_to_selected_group_and_role(self):
        form = RouteStepTemplateAdminForm(
            data={
                "order": "1",
                "name": "Этап",
                "assignee_kind": AssigneeKind.FIXED_UNIT_GROUP,
                "target_unit": str(self.group.id),
                "target_group": str(self.role.id),
                "target_user": str(self.matching_user.id),
                "can_reject": "on",
                "can_request_revision": "on",
            }
        )

        self.assertQuerySetEqual(
            form.fields["target_user"].queryset.order_by("id"),
            [self.matching_user],
            transform=lambda user: user,
        )

    def test_admin_form_allows_author_chair_head_without_fixed_assignment_fields(self):
        form = RouteStepTemplateAdminForm(
            data={
                "order": "1",
                "name": "Заведующий кафедрой автора",
                "assignee_kind": AssigneeKind.AUTHOR_CHAIR_HEAD,
                "can_reject": "on",
                "can_request_revision": "on",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)


@override_settings(SUBMISSION_SELECTABLE_ROUTE_TEMPLATE_IDS=())
class WorkflowRunAdminEditingTests(TestCase):
    def setUp(self):
        self.root = User.objects.create_superuser(
            username=settings.ROOT_ADMIN_USERNAME,
            password="root-secret",
            email="root@example.local",
        )
        self.group = OrgUnit.objects.create(name="Тестовая группа", code="test-group")
        self.role = Group.objects.create(name="Тестовая роль")
        self.group.available_roles.add(self.role)
        self.reviewer = User.objects.create_user(
            username="reviewer_admin_test",
            password="1234",
            org_unit=self.group,
            first_name="Тест",
            last_name="Проверяющий",
        )
        self.reviewer.groups.add(self.role)
        self.author = User.objects.create_user(username="author_admin_test", password="1234", org_unit=self.group)
        self.journal = Journal.objects.create(name="Журнал для админ-теста")
        self.article_type = ArticleType.objects.create(code="admin-test-type", name="Научная статья")
        self.direction = Direction.objects.create(code="admin-test-direction", name="Направление админ-теста")
        self.route_template = RouteTemplate.objects.create(
            name="Маршрут для админ-теста",
            direction=self.direction,
            is_active=True,
        )
        self.step_template = RouteStepTemplate.objects.create(
            route_template=self.route_template,
            order=1,
            name="Этап админ-теста",
            assignee_kind=AssigneeKind.FIXED_UNIT_GROUP,
            target_unit=self.group,
            target_group=self.role,
            target_user=self.reviewer,
            can_reject=True,
            can_request_revision=True,
        )
        self.submission = Submission.objects.create(
            title="Заявка для админ-теста",
            author=self.author,
            journal=self.journal,
            article_type=self.article_type,
            direction=self.direction,
        )
        self.workflow_run = WorkflowRun.objects.create(
            submission=self.submission,
            route_template=self.route_template,
            status=WorkflowRunStatus.ACTIVE,
        )
        self.workflow_step = WorkflowStep.objects.create(
            workflow_run=self.workflow_run,
            step_template=self.step_template,
            order=1,
            name="Этап админ-теста",
            assignee_kind=AssigneeKind.FIXED_UNIT_GROUP,
            assigned_unit=self.group,
            assigned_group=self.role,
            assigned_user=self.reviewer,
            can_reject=True,
            can_request_revision=True,
            status=WorkflowStepStatus.ACTIVE,
        )
        self.pending_step_template = RouteStepTemplate.objects.create(
            route_template=self.route_template,
            order=2,
            name="Следующий этап",
            assignee_kind=AssigneeKind.FIXED_UNIT_GROUP,
            target_unit=self.group,
            target_group=self.role,
            target_user=self.reviewer,
            can_reject=True,
            can_request_revision=True,
        )
        self.pending_step = WorkflowStep.objects.create(
            workflow_run=self.workflow_run,
            step_template=self.pending_step_template,
            order=2,
            name="Следующий этап",
            assignee_kind=AssigneeKind.FIXED_UNIT_GROUP,
            assigned_unit=self.group,
            assigned_group=self.role,
            assigned_user=self.reviewer,
            can_reject=True,
            can_request_revision=True,
            status=WorkflowStepStatus.PENDING,
        )
        self.task = ApprovalTask.objects.create(
            workflow_step=self.workflow_step,
            status=ApprovalTaskStatus.ACTIVE,
            assigned_group=self.role,
            assigned_unit=self.group,
            assigned_user=self.reviewer,
        )

    def test_admin_can_save_workflow_run_without_step_template_integrity_error(self):
        self.client.force_login(self.root)

        response = self.client.post(
            reverse("admin:workflow_workflowrun_change", args=[self.workflow_run.id]),
            {
                "steps-TOTAL_FORMS": "1",
                "steps-INITIAL_FORMS": "1",
                "steps-MIN_NUM_FORMS": "0",
                "steps-MAX_NUM_FORMS": "0",
                "steps-0-id": str(self.workflow_step.id),
                "steps-0-workflow_run": str(self.workflow_run.id),
                "steps-0-order": "1",
                "steps-0-name": "Этап админ-теста",
                "steps-0-assigned_unit": str(self.group.id),
                "steps-0-assigned_group": str(self.role.id),
                "steps-0-assigned_user": str(self.reviewer.id),
                "steps-0-can_reject": "on",
                "steps-0-can_request_revision": "on",
                "_save": "Сохранить",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.workflow_step.refresh_from_db()
        self.assertEqual(self.workflow_step.step_template, self.step_template)

    def test_insert_manual_step_creates_pending_step_without_template(self):
        manual_step = insert_manual_step(
            self.workflow_run,
            name="Индивидуальная патентная проверка",
            assigned_unit=self.group,
            assigned_group=self.role,
            assigned_user=self.reviewer,
            insert_after_step=self.workflow_step,
        )

        self.assertIsNone(manual_step.step_template)
        self.assertEqual(manual_step.status, WorkflowStepStatus.PENDING)
        self.assertEqual(manual_step.order, 2)
        self.pending_step.refresh_from_db()
        self.assertEqual(self.pending_step.order, 3)

    def test_approve_task_moves_route_to_inserted_manual_step(self):
        manual_step = insert_manual_step(
            self.workflow_run,
            name="Индивидуальная патентная проверка",
            assigned_unit=self.group,
            assigned_group=self.role,
            assigned_user=self.reviewer,
        )

        approve_task(self.task, self.reviewer, comment="Согласовано")

        manual_step.refresh_from_db()
        self.workflow_run.refresh_from_db()
        self.pending_step.refresh_from_db()

        self.assertEqual(manual_step.order, 1)
        self.assertEqual(manual_step.status, WorkflowStepStatus.ACTIVE)
        self.assertEqual(self.workflow_run.current_step_id, manual_step.id)
        self.assertEqual(self.pending_step.order, 3)


@override_settings(SUBMISSION_SELECTABLE_ROUTE_TEMPLATE_IDS=())
class WorkflowRevisionRequestTests(TestCase):
    def setUp(self):
        self.group = OrgUnit.objects.create(name="Группа доработки", code="revision-group")
        self.role = Group.objects.create(name="Рецензент доработки")
        self.group.available_roles.add(self.role)
        self.reviewer = User.objects.create_user(
            username="revision_reviewer",
            password="1234",
            org_unit=self.group,
        )
        self.reviewer.groups.add(self.role)
        self.author = User.objects.create_user(
            username="revision_author",
            password="1234",
            org_unit=self.group,
        )
        self.journal = Journal.objects.create(name="Журнал доработки")
        self.article_type = ArticleType.objects.create(code="revision-article", name="Научная статья")
        self.direction = Direction.objects.create(code="revision-direction", name="Направление доработки")
        self.route_template = RouteTemplate.objects.create(
            name="Маршрут доработки",
            direction=self.direction,
            is_active=True,
        )
        self.submission = Submission.objects.create(
            title="Заявка на доработку",
            author=self.author,
            journal=self.journal,
            article_type=self.article_type,
            direction=self.direction,
            route_template=self.route_template,
            status=SubmissionStatus.IN_REVIEW,
        )
        self.workflow_run = WorkflowRun.objects.create(
            submission=self.submission,
            route_template=self.route_template,
            status=WorkflowRunStatus.ACTIVE,
        )
        self.workflow_step = WorkflowStep.objects.create(
            workflow_run=self.workflow_run,
            order=1,
            name="Первичная проверка",
            assignee_kind=AssigneeKind.FIXED_UNIT_GROUP,
            assigned_group=self.role,
            assigned_unit=self.group,
            assigned_user=self.reviewer,
            can_reject=True,
            can_request_revision=True,
            status=WorkflowStepStatus.ACTIVE,
        )
        self.workflow_run.current_step = self.workflow_step
        self.workflow_run.save(update_fields=["current_step"])
        self.pending_step = WorkflowStep.objects.create(
            workflow_run=self.workflow_run,
            order=2,
            name="Следующий этап",
            assignee_kind=AssigneeKind.FIXED_UNIT_GROUP,
            assigned_group=self.role,
            assigned_unit=self.group,
            assigned_user=self.reviewer,
            can_reject=True,
            can_request_revision=True,
            status=WorkflowStepStatus.PENDING,
        )
        self.task = ApprovalTask.objects.create(
            workflow_step=self.workflow_step,
            status=ApprovalTaskStatus.ACTIVE,
            assigned_group=self.role,
            assigned_unit=self.group,
            assigned_user=self.reviewer,
        )

    def test_request_revision_pauses_workflow_and_marks_submission(self):
        request_revision(self.task, self.reviewer, comment="Нужно обновить файл статьи.")

        self.task.refresh_from_db()
        self.workflow_step.refresh_from_db()
        self.workflow_run.refresh_from_db()
        self.submission.refresh_from_db()

        self.assertEqual(self.task.status, ApprovalTaskStatus.REVISION_REQUESTED)
        self.assertEqual(self.workflow_step.status, WorkflowStepStatus.REVISION_REQUESTED)
        self.assertEqual(self.workflow_run.status, WorkflowRunStatus.PAUSED_FOR_REVISION)
        self.assertEqual(self.workflow_run.current_step_id, self.workflow_step.id)
        self.assertEqual(self.submission.status, SubmissionStatus.REVISION_REQUESTED)
        self.assertEqual(self.task.decisions.last().decision, "request_revision")

    def test_resume_or_start_workflow_restarts_current_step_after_revision(self):
        request_revision(self.task, self.reviewer, comment="Нужно обновить файл статьи.")
        self.submission.status = SubmissionStatus.SUBMITTED
        self.submission.save(update_fields=["status", "updated_at"])

        resumed_run = resume_or_start_workflow(self.submission)

        self.workflow_run.refresh_from_db()
        self.workflow_step.refresh_from_db()
        self.submission.refresh_from_db()

        self.assertEqual(resumed_run.id, self.workflow_run.id)
        self.assertEqual(self.workflow_run.status, WorkflowRunStatus.ACTIVE)
        self.assertEqual(self.workflow_run.current_step_id, self.workflow_step.id)
        self.assertEqual(self.workflow_step.status, WorkflowStepStatus.ACTIVE)
        self.assertEqual(self.submission.status, SubmissionStatus.IN_REVIEW)
        self.assertTrue(
            ApprovalTask.objects.filter(
                workflow_step=self.workflow_step,
                status=ApprovalTaskStatus.ACTIVE,
            ).exists()
        )

    def test_resume_or_start_workflow_returns_to_same_reviewer_even_for_restart_route_template(self):
        self.route_template.revision_strategy = "restart_route"
        self.route_template.save(update_fields=["revision_strategy"])

        request_revision(self.task, self.reviewer, comment="Нужно обновить файл статьи.")
        self.submission.status = SubmissionStatus.SUBMITTED
        self.submission.save(update_fields=["status", "updated_at"])

        resumed_run = resume_or_start_workflow(self.submission)

        self.workflow_run.refresh_from_db()
        self.workflow_step.refresh_from_db()
        self.submission.refresh_from_db()

        self.assertEqual(resumed_run.id, self.workflow_run.id)
        self.assertEqual(self.workflow_run.status, WorkflowRunStatus.ACTIVE)
        self.assertEqual(self.workflow_run.current_step_id, self.workflow_step.id)
        self.assertEqual(self.workflow_step.status, WorkflowStepStatus.ACTIVE)
        self.assertEqual(self.submission.status, SubmissionStatus.IN_REVIEW)
        self.assertTrue(
            ApprovalTask.objects.filter(
                workflow_step=self.workflow_step,
                assigned_user=self.reviewer,
                status=ApprovalTaskStatus.ACTIVE,
            ).exists()
        )


class WorkflowDecisionHistoryTests(TestCase):
    def setUp(self):
        self.unit = OrgUnit.objects.create(name="Группа истории решений", code="decision-history")
        self.role = Group.objects.create(name="Проверяющий истории решений")
        self.unit.available_roles.add(self.role)
        self.reviewer = User.objects.create_user(
            username="decision_history_reviewer",
            password="1234",
            org_unit=self.unit,
        )
        self.reviewer.groups.add(self.role)
        self.author = User.objects.create_user(username="decision_history_author", password="1234")
        journal = Journal.objects.create(name="Журнал истории решений")
        article_type = ArticleType.objects.create(code="decision-history-article", name="Статья")
        direction = Direction.objects.create(code="decision-history-direction", name="История решений")
        route_template = RouteTemplate.objects.create(
            name="Маршрут истории решений",
            direction=direction,
            is_active=True,
        )
        self.submission = Submission.objects.create(
            title="Материал с решением проверяющего",
            author=self.author,
            journal=journal,
            article_type=article_type,
            direction=direction,
            route_template=route_template,
            status=SubmissionStatus.IN_REVIEW,
        )
        self.workflow_run = WorkflowRun.objects.create(
            submission=self.submission,
            route_template=route_template,
            status=WorkflowRunStatus.ACTIVE,
        )
        self.step = WorkflowStep.objects.create(
            workflow_run=self.workflow_run,
            order=1,
            name="Проверка материала",
            assignee_kind=AssigneeKind.FIXED_UNIT_GROUP,
            assigned_group=self.role,
            assigned_unit=self.unit,
            status=WorkflowStepStatus.ACTIVE,
        )
        self.workflow_run.current_step = self.step
        self.workflow_run.save(update_fields=["current_step"])
        self.task = ApprovalTask.objects.create(
            workflow_step=self.step,
            status=ApprovalTaskStatus.ACTIVE,
            assigned_group=self.role,
            assigned_unit=self.unit,
        )

    def test_history_tab_keeps_approved_task_and_its_decision(self):
        approve_task(self.task, self.reviewer, comment="Материал проверен и согласован.")

        self.client.force_login(self.reviewer)
        response = self.client.get(reverse("workflow:inbox"), {"scope": "history"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "История моих решений")
        self.assertContains(response, self.submission.title)
        self.assertContains(response, "Согласовано")
        self.assertContains(response, "Материал проверен и согласован.")
        self.assertEqual(get_decision_history_queryset(self.reviewer).count(), 1)

    def test_reviewer_keeps_access_to_completed_task_after_role_is_removed(self):
        approve_task(self.task, self.reviewer, comment="Согласовано")
        self.reviewer.groups.remove(self.role)

        self.client.force_login(self.reviewer)
        response = self.client.get(reverse("workflow:task_detail", args=[self.task.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ваше решение сохранено")
