from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import Client
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.roles import CHAIR_HEAD_ROLE_NAME
from apps.accounts.staff_directory import parse_staff_directory_html, sync_staff_directory_entries
from apps.accounts.models import PublicationPlan, PublicationPlanItem
from apps.accounts.workspace import get_workspace_navigation
from apps.activities.models import Activity, ActivityStatus, ActivityType
from apps.directory.models import ArticleType, Journal, OrgUnit, Position
from apps.submissions.models import Submission, SubmissionStatus
from apps.workflow.models import AssigneeKind, RouteStepTemplate, RouteTemplate


class WorkspaceNavigationUnitTests(TestCase):
    def test_sidebar_prefers_users_chair_over_general_org_unit(self):
        general_unit = OrgUnit.objects.create(name="Институт автоматики", code="institute-nav")
        chair_unit = OrgUnit.objects.create(name='Кафедра "Высшая математика"', code="chair-nav")
        user = get_user_model().objects.create_user(
            username="workspace-chair-user",
            org_unit=general_unit,
            chair_org_unit=chair_unit,
        )

        navigation = get_workspace_navigation(user)

        self.assertEqual(navigation["workspace_user_unit"], chair_unit.name)


@override_settings(ROOT_ADMIN_USERNAME="root")
class RootAdminAccessTests(TestCase):
    def setUp(self):
        self.root_user = self._create_admin("root")
        self.other_admin = self._create_admin("admin2")
        self.request_factory = RequestFactory()

    def _create_admin(self, username):
        return get_user_model().objects.create_superuser(
            username=username,
            email=f"{username}@example.com",
            password="1234",
        )

    def test_root_user_can_open_admin_index(self):
        self.client.force_login(self.root_user)

        response = self.client.get("/admin/")

        self.assertEqual(response.status_code, 200)

    def test_other_superuser_is_redirected_from_admin_index(self):
        self.client.force_login(self.other_admin)

        response = self.client.get("/admin/")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response.url)

    def test_other_superuser_cannot_log_into_admin(self):
        response = self.client.post(
            "/admin/login/",
            {"username": "admin2", "password": "1234"},
        )

        self.assertContains(response, "Доступ в админку разрешен только root-пользователю.")

    def test_admin_index_shows_only_operational_models(self):
        request = self.request_factory.get("/admin/")
        request.user = self.root_user

        app_list = admin.site.get_app_list(request)
        model_names = {
            model["object_name"]
            for app in app_list
            for model in app["models"]
        }

        self.assertTrue(
            {
                "Group",
                "User",
                "OrgUnit",
                "Position",
                "Direction",
                "RouteTemplate",
                "Submission",
            }.issubset(model_names)
        )
        self.assertTrue(
            {
                "SubmissionVersion",
                "RouteStepTemplate",
                "WorkflowRun",
                "WorkflowStep",
                "ApprovalTask",
                "TaskDecision",
                "CheckDefinition",
                "CheckRun",
            }.isdisjoint(model_names)
        )

    def test_user_change_page_shows_author_material_links(self):
        self.client.force_login(self.root_user)
        author = get_user_model().objects.create_user(
            username="author_in_admin",
            password="1234",
            first_name="Иван",
            last_name="Петров",
        )
        journal = Journal.objects.create(name="Админский журнал")
        article_type = ArticleType.objects.create(code="admin-article", name="Статья")
        Submission.objects.create(
            title="Черновик автора в админке",
            author=author,
            journal=journal,
            article_type=article_type,
            status=SubmissionStatus.DRAFT,
        )
        Submission.objects.create(
            title="Согласованная статья автора в админке",
            author=author,
            journal=journal,
            article_type=article_type,
            status=SubmissionStatus.APPROVED,
            submitted_at=timezone.now(),
        )

        response = self.client.get(reverse("admin:accounts_user_change", args=[author.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Материалы автора")
        self.assertContains(response, "Все: 2")
        self.assertContains(response, "Отправленные: 1")
        self.assertContains(response, "Согласованные: 1")
        self.assertContains(response, f"/admin/submissions/submission/?authors__id__exact={author.id}")


@override_settings(ROOT_ADMIN_USERNAME="rootUser")
class StaffDirectorySyncTests(TestCase):
    def setUp(self):
        self.existing_org_unit = OrgUnit.objects.create(name="Экспертная комиссия", code="expert-committee")
        self.existing_role = Group.objects.create(name="Эксперт по теме")
        self.existing_org_unit.available_roles.add(self.existing_role)
        self.existing_user = get_user_model().objects.create_user(
            username="akulinin_ei",
            password="1234",
            first_name="Акулинин Евгений Игоревич",
            last_name="",
            org_unit=self.existing_org_unit,
        )
        self.existing_user.groups.add(self.existing_role)
        self.free_user = get_user_model().objects.create_user(
            username="belousov_oa",
            password="1234",
            first_name="Белоусов Олег Андреевич",
            last_name="",
            org_unit=OrgUnit.objects.create(name="Ошибочное подразделение", code="wrong-unit"),
        )
        self.html_text = """
        <a href="javascript:passBack('2627','x');">Акулинин Евгений Игоревич / Доцент, Кафедра "Технологии и оборудование пищевых и химических производств"</a>
        <a href="javascript:passBack('4406','x');">Абакумова Людмила Станиславовна / Помощник первого проректора, Ректорат</a>
        <a href="javascript:passBack('526','x');">Белоусов Олег Андреевич / Директор института, Институт энергетики, приборостроения и радиоэлектроники</a>
        """
        self.second_html_text = """
        <a href="javascript:passBack('700','x');">Алексеева Надежда Вячеславовна / Доцент, Кафедра "Технологические процессы, аппараты и техносферная безопасность"</a>
        <a href="javascript:passBack('10607','x');">Кузнецова Мария Сергеевна / Начальник отдела патентоведения, стандартизации и метрологии, Управление фундаментальных и прикладных исследований</a>
        """

    def test_parse_staff_directory_html_handles_commas_in_position_and_org_unit(self):
        entries = parse_staff_directory_html(self.html_text + self.second_html_text)

        self.assertEqual(len(entries), 5)
        self.assertEqual(entries[0].external_id, "2627")
        self.assertEqual(entries[0].position_name, "Доцент")
        self.assertEqual(entries[0].org_unit_name, 'Кафедра "Технологии и оборудование пищевых и химических производств"')
        self.assertEqual(entries[1].full_name, "Абакумова Людмила Станиславовна")
        self.assertEqual(entries[2].position_name, "Директор института")
        self.assertEqual(entries[2].org_unit_name, "Институт энергетики, приборостроения и радиоэлектроники")
        self.assertEqual(entries[3].position_name, "Доцент")
        self.assertEqual(
            entries[3].org_unit_name,
            'Кафедра "Технологические процессы, аппараты и техносферная безопасность"',
        )
        self.assertEqual(
            entries[4].position_name,
            "Начальник отдела патентоведения, стандартизации и метрологии",
        )
        self.assertEqual(
            entries[4].org_unit_name,
            "Управление фундаментальных и прикладных исследований",
        )

    def test_sync_staff_directory_entries_updates_existing_and_creates_missing_user(self):
        stats = sync_staff_directory_entries(parse_staff_directory_html(self.html_text))

        self.existing_user.refresh_from_db()
        self.free_user.refresh_from_db()
        created_user = get_user_model().objects.get(external_directory_id="4406")

        self.assertEqual(stats["users_updated"], 2)
        self.assertEqual(stats["users_created"], 1)
        self.assertEqual(self.existing_user.position.name, "Доцент")
        self.assertEqual(self.existing_user.org_unit, self.existing_org_unit)
        self.assertEqual(
            self.existing_user.chair_org_unit.name,
            'Кафедра "Технологии и оборудование пищевых и химических производств"',
        )
        self.assertEqual(self.free_user.position.name, "Директор института")
        self.assertEqual(
            self.free_user.org_unit.name,
            "Институт энергетики, приборостроения и радиоэлектроники",
        )
        self.assertIsNone(self.free_user.chair_org_unit)
        self.assertEqual(created_user.get_full_name().strip(), "Абакумова Людмила Станиславовна")
        self.assertEqual(created_user.position.name, "Помощник первого проректора")
        self.assertEqual(created_user.org_unit.name, "Ректорат")
        self.assertIsNone(created_user.chair_org_unit)
        self.assertEqual(created_user.username, "abakumova_ls")
        self.assertTrue(Position.objects.filter(name="Доцент").exists())

    def test_management_command_imports_multiple_staff_directory_files(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp_dir:
            first_html_path = Path(temp_dir) / "staff_1.html"
            second_html_path = Path(temp_dir) / "staff_2.html"
            first_html_path.write_text(self.html_text, encoding="utf-8")
            second_html_path.write_text(self.second_html_text, encoding="utf-8")

            call_command("sync_staff_directory", str(first_html_path), str(second_html_path))

        self.assertTrue(get_user_model().objects.filter(external_directory_id="4406").exists())
        imported_user = get_user_model().objects.get(external_directory_id="700")
        self.assertEqual(
            imported_user.chair_org_unit.name,
            'Кафедра "Технологические процессы, аппараты и техносферная безопасность"',
        )

    def test_sync_staff_directory_assigns_chair_head_role_and_chair_mapping(self):
        html_text = """
        <a href="javascript:passBack('9001','x');">Иванов Иван Иванович / Заведующий кафедрой, Кафедра "Тестовая кафедра материалов"</a>
        """

        sync_staff_directory_entries(parse_staff_directory_html(html_text))

        user = get_user_model().objects.get(external_directory_id="9001")
        chair_head_role = Group.objects.get(name=CHAIR_HEAD_ROLE_NAME)
        self.assertEqual(user.position.name, "Заведующий кафедрой")
        self.assertEqual(user.chair_org_unit.name, 'Кафедра "Тестовая кафедра материалов"')
        self.assertTrue(user.groups.filter(id=chair_head_role.id).exists())
        self.assertTrue(user.chair_org_unit.available_roles.filter(id=chair_head_role.id).exists())

    def test_user_get_chair_name_returns_value_without_prefix(self):
        self.existing_user.chair_org_unit = OrgUnit.objects.create(
            name='Кафедра "Технологии и оборудование пищевых и химических производств"',
            code="chair-food-tech",
        )
        self.existing_user.save(update_fields=["chair_org_unit"])

        self.assertEqual(
            self.existing_user.get_chair_name(),
            "Технологии и оборудование пищевых и химических производств",
        )


@override_settings(ROOT_ADMIN_USERNAME="rootUser")
class AuthorDirectoryAndProfileTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.author = get_user_model().objects.create_user(
            username="author_profile",
            password="1234",
            first_name="Иван",
            last_name="Иванов",
            email="author@example.com",
            org_unit=OrgUnit.objects.create(name="Научная группа", code="science-group"),
        )
        self.other_user = get_user_model().objects.create_user(
            username="without_submissions",
            password="1234",
            first_name="Петр",
            last_name="Петров",
        )
        self.coauthor = get_user_model().objects.create_user(
            username="coauthor_profile",
            password="1234",
            first_name="Мария",
            last_name="Сидорова",
        )
        self.journal = Journal.objects.create(name="Журнал авторских профилей")
        self.article_type = ArticleType.objects.create(code="author-profile-article", name="Статья")

        Submission.objects.create(
            title="Черновик автора",
            author=self.author,
            journal=self.journal,
            article_type=self.article_type,
            status=SubmissionStatus.DRAFT,
        )
        Submission.objects.create(
            title="Отправленная статья автора",
            author=self.author,
            journal=self.journal,
            article_type=self.article_type,
            status=SubmissionStatus.IN_REVIEW,
            submitted_at=timezone.now(),
        )
        Submission.objects.create(
            title="Одобренная статья автора",
            author=self.author,
            journal=self.journal,
            article_type=self.article_type,
            status=SubmissionStatus.APPROVED,
            submitted_at=timezone.now(),
        )
        shared_submission = Submission.objects.create(
            title="Совместная статья двух авторов",
            author=self.author,
            journal=self.journal,
            article_type=self.article_type,
            status=SubmissionStatus.IN_REVIEW,
            submitted_at=timezone.now(),
        )
        shared_submission.authors.add(self.coauthor)

    def test_author_directory_lists_only_users_with_materials(self):
        self.client.force_login(self.author)

        response = self.client.get(reverse("author_directory"))

        self.assertEqual(response.status_code, 200)
        authors = list(response.context["authors"])
        self.assertEqual([author.id for author in authors], [self.author.id, self.coauthor.id])
        self.assertEqual(authors[0].submission_count, 4)
        self.assertEqual(authors[0].sent_submission_count, 3)
        self.assertEqual(authors[0].approved_submission_count, 1)

    def test_author_profile_separates_sent_and_approved_materials(self):
        self.client.force_login(self.author)

        response = self.client.get(reverse("author_profile", args=[self.author.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["submission_count"], 4)
        self.assertEqual(response.context["sent_submission_count"], 3)
        self.assertEqual(response.context["approved_submission_count"], 1)
        self.assertQuerySetEqual(
            response.context["sent_submissions"].order_by("title"),
            ["Одобренная статья автора", "Отправленная статья автора", "Совместная статья двух авторов"],
            transform=lambda submission: submission.title,
        )
        self.assertQuerySetEqual(
            response.context["approved_submissions"],
            ["Одобренная статья автора"],
            transform=lambda submission: submission.title,
        )

    def test_profile_route_opens_current_user_profile(self):
        self.client.force_login(self.author)

        response = self.client.get(reverse("profile"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["author"].id, self.author.id)
        self.assertTrue(response.context["is_own_profile"])

    def test_coauthor_profile_includes_shared_submission(self):
        self.client.force_login(self.coauthor)

        response = self.client.get(reverse("author_profile", args=[self.coauthor.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["submission_count"], 1)
        self.assertEqual(response.context["sent_submission_count"], 1)
        self.assertQuerySetEqual(
            response.context["sent_submissions"],
            ["Совместная статья двух авторов"],
            transform=lambda submission: submission.title,
        )

    def test_profile_shows_saved_plan_items_without_upload_or_progress(self):
        self.client.force_login(self.author)
        plan = PublicationPlan.objects.create(
            user=self.author,
            original_filename="plan.xlsx",
        )
        PublicationPlanItem.objects.create(
            plan=plan,
            order=1,
            level="БС3",
            journal_name="Вестник ТГТУ",
            article_title="Первая статья",
            source_sheet="3",
            source_cell="B31",
        )

        response = self.client.get(reverse("profile"))

        self.assertContains(response, "Пункты из плана")
        self.assertContains(response, "Первая статья")
        self.assertNotContains(response, "Публикационный план")
        self.assertNotContains(response, "Заменить план")
        self.assertNotContains(response, "Прогресс")

    def test_profile_does_not_accept_plan_uploads(self):
        self.client.force_login(self.author)

        response = self.client.post(reverse("profile"), {})

        self.assertEqual(response.status_code, 405)


class DashboardActivityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="dashboard_activity_user",
            password="1234",
            first_name="Иван",
            last_name="Иванов",
        )
        self.other_user = get_user_model().objects.create_user(
            username="other_dashboard_activity_user",
            password="1234",
            first_name="Мария",
            last_name="Петрова",
        )
        article_type = ActivityType.objects.get(code="article")
        Activity.objects.create(
            owner=self.user,
            activity_type=article_type,
            title="Моя статья из индивидуального плана",
            quantity=3,
            academic_year="2025/2026",
            status=ActivityStatus.PLANNED,
            source_key="a" * 64,
            source_file="КИСМ/ИП Иванова.xlsx",
        )
        Activity.objects.create(
            owner=self.other_user,
            activity_type=article_type,
            title="Чужой результат",
            academic_year="2025/2026",
        )

    def test_dashboard_shows_only_the_users_planned_results(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("home"))

        self.assertContains(response, "Мои запланированные результаты")
        self.assertContains(response, "Моя статья из индивидуального плана")
        self.assertNotContains(response, "Чужой результат")
        self.assertEqual(response.context["personal_activity_year"], "2025/2026")
        self.assertEqual(response.context["my_activity_total"], 3)
        self.assertEqual(response.context["my_activity_planned"], 3)
        activity = Activity.objects.get(title="Моя статья из индивидуального плана")
        self.assertContains(response, reverse("activities:edit", args=[activity.pk]))

    def test_dashboard_hides_review_navigation_for_regular_author(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("home"))

        self.assertFalse(response.context["show_review_navigation"])
        self.assertNotContains(response, "История проверок")
        self.assertNotContains(response, "Рут админ")

    def test_dashboard_shows_django_admin_only_for_root_superuser(self):
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save(update_fields=["is_staff", "is_superuser"])
        self.client.force_login(self.user)

        other_superuser_response = self.client.get(reverse("home"))
        self.assertNotContains(other_superuser_response, "Рут админ")

        root_user = get_user_model().objects.create_superuser(
            username="rootUser",
            email="root@example.com",
            password="1234",
        )
        self.client.force_login(root_user)
        root_response = self.client.get(reverse("home"))
        admin_response = self.client.get(reverse("admin:index"))

        self.assertContains(root_response, "Рут админ")
        self.assertContains(root_response, reverse("admin:index"))
        self.assertEqual(admin_response.status_code, 200)

    def test_dashboard_shows_review_navigation_for_route_expert(self):
        expert_role = Group.objects.create(name="Эксперт дашборда")
        self.user.groups.add(expert_role)
        route_template = RouteTemplate.objects.create(name="Маршрут для эксперта")
        RouteStepTemplate.objects.create(
            route_template=route_template,
            order=1,
            name="Экспертная проверка",
            assignee_kind=AssigneeKind.FIXED_GROUP,
            target_group=expert_role,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("home"))

        self.assertTrue(response.context["show_review_navigation"])
        self.assertContains(response, "Проверка")
        self.assertContains(response, "История проверок")
