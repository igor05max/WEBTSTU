from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from pathlib import Path
from tempfile import TemporaryDirectory

from apps.activities.models import (
    Activity,
    ActivityPeriod,
    ActivityStatus,
    ActivityType,
    GrantType,
    PlanningRosterEntry,
    ScientificResult,
)
from apps.activities.roster import ExtractedRosterPerson, sync_planning_roster
from apps.activities.source_files import current_individual_plan_paths
from apps.activities.plan_import import (
    ExtractedPlanActivity,
    _classify_activity_codes,
    _grant_type_code,
    _quantity_for,
    repair_imported_plan_classification,
    sync_plan_activities,
)
from apps.activities.science_import import (
    ExtractedScientificResult,
    extract_scientific_results,
    sync_scientific_results,
)
from apps.directory.models import ArticleType, Journal, OrgUnit, Position
from apps.submissions.models import Submission, SubmissionStatus


class ActivityRegistryTests(TestCase):
    def setUp(self):
        self.unit = OrgUnit.objects.create(name="Кафедра тестовых результатов")
        self.owner = get_user_model().objects.create_user(
            username="activity_owner",
            password="1234",
            first_name="Иван",
            last_name="Иванов",
            org_unit=self.unit,
        )
        self.other_user = get_user_model().objects.create_user(
            username="activity_other",
            password="1234",
            first_name="Мария",
            last_name="Петрова",
            org_unit=self.unit,
        )
        self.article_type = ActivityType.objects.get(code="article")
        self.grant_activity_type = ActivityType.objects.get(code="grant")
        self.grant_type = GrantType.objects.get(code="rnf")
        self.owner.external_directory_id = "70001"
        self.owner.save(update_fields=["external_directory_id"])

    def test_catalog_contains_types_found_in_individual_plans(self):
        codes = set(ActivityType.objects.values_list("code", flat=True))

        self.assertTrue(
            {
                "article",
                "grant",
                "monograph",
                "teaching_aid",
                "conference",
                "patent",
                "advanced_training",
                "career_guidance",
            }.issubset(codes)
        )

    def test_current_plan_paths_keep_only_the_latest_dated_snapshot(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            mig = root / "МИГ"
            mig.mkdir()
            for filename in (
                "ИПП_2025-2026_Лазарев.xlsx",
                "19.11.2025_ИПП_2025-2026_Лазарев.xlsx",
                "20.11.2025_ИПП_2025-2026_Лазарев.xlsx",
                "ИПП_2025-2026_Иванов.xlsx",
                "Сводный_план.xlsx",
            ):
                (mig / filename).touch()

            selected = [path.relative_to(root).as_posix() for path in current_individual_plan_paths(root)]

        self.assertEqual(
            selected,
            [
                "МИГ/20.11.2025_ИПП_2025-2026_Лазарев.xlsx",
                "МИГ/ИПП_2025-2026_Иванов.xlsx",
                "МИГ/Сводный_план.xlsx",
            ],
        )

    def test_grant_requires_grant_type(self):
        activity = Activity(
            owner=self.owner,
            activity_type=self.grant_activity_type,
            title="Заявка на конкурс",
        )

        with self.assertRaises(ValidationError):
            activity.full_clean()

        activity.grant_type = self.grant_type
        activity.full_clean()

    def test_registry_shows_who_is_responsible_and_coexecutor(self):
        activity = Activity.objects.create(
            owner=self.other_user,
            activity_type=self.article_type,
            title="Статья в журнале",
            academic_year="2025/2026",
            period=ActivityPeriod.FIRST_HALF,
        )
        activity.collaborators.add(self.owner)
        self.client.force_login(self.owner)

        response = self.client.get(reverse("activities:list"))

        self.assertContains(response, "Статья в журнале")
        self.assertContains(response, "Мария Петрова")
        self.assertContains(response, "Иван Иванов")

        mine_response = self.client.get(reverse("activities:list"), {"scope": "mine"})
        self.assertContains(mine_response, "Статья в журнале")

    def test_my_results_is_a_direct_personal_plan_without_registry_filters(self):
        Activity.objects.create(
            owner=self.owner,
            activity_type=self.article_type,
            title="Моя статья",
            academic_year="2025/2026",
        )
        Activity.objects.create(
            owner=self.other_user,
            activity_type=self.article_type,
            title="Чужая статья",
            academic_year="2025/2026",
        )
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("activities:list"),
            {"scope": "mine", "q": "Чужая", "type": self.article_type.pk, "year": "2025/2026"},
        )

        self.assertContains(response, "Мой план")
        self.assertContains(response, "Моя статья")
        self.assertNotContains(response, "Чужая статья")
        self.assertNotContains(response, "Поиск по сотруднику или результату")
        self.assertEqual(response.context["summary"]["total"], 1)
        self.assertEqual(response.context["selected_year"], "2025/2026")

    def test_my_plan_does_not_mix_in_submission_history(self):
        journal = Journal.objects.create(name="Журнал личных результатов")
        submission_article_type = ArticleType.objects.create(
            code="personal-results-article", name="Статья"
        )
        Submission.objects.create(
            title="Статья на согласовании",
            author=self.owner,
            journal=journal,
            article_type=submission_article_type,
            status=SubmissionStatus.IN_REVIEW,
            submitted_at="2026-07-14T10:00:00+03:00",
        )
        Submission.objects.create(
            title="Согласованная статья",
            author=self.owner,
            journal=journal,
            article_type=submission_article_type,
            status=SubmissionStatus.APPROVED,
            submitted_at="2026-07-14T10:00:00+03:00",
        )
        Submission.objects.create(
            title="Черновик не должен попасть в результаты",
            author=self.owner,
            journal=journal,
            article_type=submission_article_type,
            status=SubmissionStatus.DRAFT,
        )
        self.client.force_login(self.owner)

        response = self.client.get(reverse("activities:list"), {"scope": "mine"})

        self.assertNotContains(response, "Согласованные материалы")
        self.assertNotContains(response, "Согласованная статья")
        self.assertNotContains(response, "Статья на согласовании")
        self.assertNotContains(response, "Черновик не должен попасть в результаты")

    def test_personal_plan_honors_year_and_sums_quantities(self):
        Activity.objects.create(
            owner=self.owner,
            activity_type=self.article_type,
            title="Три статьи в текущем плане",
            academic_year="2025/2026",
            quantity=3,
            status=ActivityStatus.PLANNED,
        )
        Activity.objects.create(
            owner=self.owner,
            activity_type=self.article_type,
            title="Результат прошлого года",
            academic_year="2024/2025",
            quantity=2,
            status=ActivityStatus.COMPLETED,
        )
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("activities:list"),
            {"scope": "mine", "year": "2025/2026"},
        )

        self.assertContains(response, "Три статьи в текущем плане")
        self.assertNotContains(response, "Результат прошлого года")
        self.assertEqual(response.context["summary"]["total"], 3)
        self.assertEqual(response.context["summary"]["planned"], 3)
        self.assertEqual(response.context["summary"]["completed"], 0)

    def test_user_can_add_own_grant_and_cannot_edit_another_users_result(self):
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("activities:create"),
            {
                "activity_type": self.grant_activity_type.pk,
                "grant_type": self.grant_type.pk,
                "title": "Заявка РНФ по новым материалам",
                "academic_year": "2025/2026",
                "period": ActivityPeriod.WHOLE_YEAR,
                "status": ActivityStatus.PLANNED,
                "collaborators": [self.other_user.pk],
            },
        )

        self.assertRedirects(response, f"{reverse('activities:list')}?scope=mine")
        activity = Activity.objects.get(title="Заявка РНФ по новым материалам")
        self.assertEqual(activity.owner, self.owner)
        self.assertEqual(activity.grant_type, self.grant_type)
        self.assertTrue(activity.collaborators.filter(pk=self.other_user.pk).exists())

        another_activity = Activity.objects.create(
            owner=self.other_user,
            activity_type=self.article_type,
            title="Чужая статья",
        )
        denied = self.client.get(reverse("activities:edit", args=[another_activity.pk]))
        self.assertEqual(denied.status_code, 403)

    def test_matrix_groups_people_by_chair_and_links_to_their_results(self):
        chair = OrgUnit.objects.create(name="Кафедра матрицы")
        self.owner.chair_org_unit = chair
        self.owner.save(update_fields=["chair_org_unit"])
        matrix_activity = Activity.objects.create(
            owner=self.owner,
            activity_type=self.article_type,
            title="Статья для сводной таблицы",
            academic_year="2025/2026",
            quantity=2,
        )
        ScientificResult.objects.create(
            source_key="matrix-result-1",
            source_id="matrix-result-1",
            external_author_id=self.owner.external_directory_id,
            owner=self.owner,
            activity_type=self.article_type,
            planned_activity=matrix_activity,
            title="Первая выполненная статья",
            result_year=2026,
            academic_year="2025/2026",
            source_file="science.txt",
            source_line=1,
        )
        self.client.force_login(self.other_user)

        response = self.client.get(reverse("activities:matrix"), {"year": "2025/2026"})

        self.assertContains(response, "матрицы")
        self.assertContains(response, "Иван Иванов")
        self.assertContains(response, "Статья")
        departments = dict(response.context["departments"])
        matrix_rows = departments["матрицы"]
        owner_row = next(row for row in matrix_rows if row["person"] == self.owner)
        self.assertIn("scope=mine", owner_row["plan_url"])
        article_column = next(
            index
            for index, activity_type in enumerate(
                [
                    activity_type
                    for group in response.context["type_groups"]
                    for activity_type in group["types"]
                ]
            )
            if activity_type.code == "article"
        )
        self.assertEqual(owner_row["cells"][article_column]["count"], 2)
        self.assertEqual(owner_row["cells"][article_column]["planned_count"], 2)
        self.assertEqual(owner_row["cells"][article_column]["actual_count"], 1)
        self.assertEqual(owner_row["cells"][article_column]["ratio_state"], "is-progress")
        self.assertContains(response, "Фактически выполнено")
        self.assertContains(response, "выполнено 1, запланировано 2")
        self.assertIn("owner=", owner_row["cells"][article_column]["url"])

    def test_matrix_shows_fact_without_plan_as_actual_over_dash(self):
        conference_type = ActivityType.objects.get(code="conference")
        ScientificResult.objects.create(
            source_key="matrix-unplanned-result",
            source_id="matrix-unplanned-result",
            external_author_id=self.owner.external_directory_id,
            owner=self.owner,
            activity_type=conference_type,
            title="Доклад вне первоначального плана",
            result_year=2026,
            academic_year="2025/2026",
            source_file="science.txt",
            source_line=2,
        )
        self.client.force_login(self.other_user)

        response = self.client.get(reverse("activities:matrix"), {"year": "2025/2026"})

        departments = dict(response.context["departments"])
        owner_row = next(
            row
            for rows in departments.values()
            for row in rows
            if row["person"] == self.owner
        )
        activity_types = [
            activity_type
            for group in response.context["type_groups"]
            for activity_type in group["types"]
        ]
        conference_column = next(
            index for index, activity_type in enumerate(activity_types) if activity_type.code == "conference"
        )
        cell = owner_row["cells"][conference_column]
        self.assertEqual(cell["planned_count"], 0)
        self.assertEqual(cell["actual_count"], 1)
        self.assertTrue(cell["has_value"])
        self.assertContains(response, "выполнено 1, запланировано не задано")

    def test_matrix_includes_results_beyond_plan_in_the_ratio_without_extra_label(self):
        activity = Activity.objects.create(
            owner=self.owner,
            activity_type=self.article_type,
            title="Одна статья по плану",
            academic_year="2025/2026",
            quantity=1,
        )
        ScientificResult.objects.create(
            source_key="matrix-planned-article",
            source_id="matrix-planned-article",
            owner=self.owner,
            activity_type=self.article_type,
            planned_activity=activity,
            title="Статья по плану",
            result_year=2026,
            academic_year="2025/2026",
            source_file="science.txt",
            source_line=1,
        )
        ScientificResult.objects.create(
            source_key="matrix-extra-article",
            source_id="matrix-extra-article",
            owner=self.owner,
            activity_type=self.article_type,
            title="Статья сверх плана",
            result_year=2026,
            academic_year="2025/2026",
            source_file="science.txt",
            source_line=2,
        )
        self.client.force_login(self.other_user)

        response = self.client.get(reverse("activities:matrix"), {"year": "2025/2026"})

        owner_row = next(
            row
            for rows in dict(response.context["departments"]).values()
            for row in rows
            if row["person"] == self.owner
        )
        article_types = [
            activity_type
            for group in response.context["type_groups"]
            for activity_type in group["types"]
        ]
        article_column = next(
            index for index, activity_type in enumerate(article_types) if activity_type.code == "article"
        )
        cell = owner_row["cells"][article_column]
        self.assertEqual(cell["actual_count"], 2)
        self.assertEqual(cell["planned_count"], 1)
        self.assertContains(response, "выполнено 2, запланировано 1")
        self.assertNotContains(response, "+1 вне плана")

        plan_response = self.client.get(owner_row["plan_url"])
        self.assertContains(plan_response, "Что сделано")
        self.assertContains(plan_response, "Статья — 2")
        self.assertContains(plan_response, "Показать названия написанных статей")
        self.assertContains(plan_response, "Статья по плану")
        self.assertContains(plan_response, "Статья сверх плана")

    def test_employee_plan_marks_confirmed_results_outside_plan(self):
        Activity.objects.create(
            owner=self.owner,
            activity_type=self.article_type,
            title="Запланированная статья",
            academic_year="2025/2026",
        )
        conference_type = ActivityType.objects.get(code="conference")
        ScientificResult.objects.create(
            source_key="employee-plan-unplanned-result",
            source_id="employee-plan-unplanned-result",
            external_author_id=self.owner.external_directory_id,
            owner=self.owner,
            activity_type=conference_type,
            title="Доклад, выполненный вне плана",
            result_year=2026,
            academic_year="2025/2026",
            source_file="science.txt",
            source_line=3,
        )
        self.client.force_login(self.other_user)

        response = self.client.get(
            reverse("activities:list"),
            {"scope": "mine", "owner": self.owner.pk, "year": "2025/2026"},
        )

        self.assertContains(response, "Выполнено вне плана: 1")
        self.assertContains(response, "не увеличивают процент выполнения")
        self.assertContains(response, "Доклад, выполненный вне плана")

    def test_statistics_is_available_to_every_user_and_counts_a_result_once(self):
        PlanningRosterEntry.objects.create(
            user=self.owner,
            academic_year="2025/2026",
            department_code="КИСМ",
            full_name="Иванов Иван",
        )
        first_activity = Activity.objects.create(
            owner=self.owner,
            activity_type=self.article_type,
            title="Первая статья в плане",
            academic_year="2025/2026",
            quantity=1,
        )
        Activity.objects.create(
            owner=self.owner,
            activity_type=self.article_type,
            title="Вторая статья в плане",
            academic_year="2025/2026",
            quantity=1,
        )
        ScientificResult.objects.create(
            source_key="statistics-result-once",
            source_id="statistics-result-once",
            external_author_id=self.owner.external_directory_id,
            owner=self.owner,
            activity_type=self.article_type,
            planned_activity=first_activity,
            title="Одна фактически выполненная статья",
            result_year=2026,
            academic_year="2025/2026",
            source_file="science.txt",
            source_line=1,
        )
        self.client.force_login(self.other_user)

        response = self.client.get(
            reverse("activities:statistics"),
            {"year": "2025/2026", "department": "КИСМ"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["summary"]["people"], 1)
        self.assertEqual(response.context["summary"]["planned"], 2)
        self.assertEqual(response.context["summary"]["confirmed"], 1)
        self.assertEqual(response.context["summary"]["extra"], 0)
        self.assertEqual(response.context["summary"]["progress_percent"], 50)
        article_row = next(
            row for row in response.context["type_rows"] if row["name"] == self.article_type.name
        )
        self.assertEqual(article_row["planned"], 2)
        self.assertEqual(article_row["confirmed"], 1)
        self.assertEqual(article_row["extra"], 0)
        self.assertContains(response, "Статистика выполнения")
        self.assertContains(response, "Иванов Иван")

    def test_statistics_does_not_repeat_a_person_from_two_department_rosters(self):
        PlanningRosterEntry.objects.create(
            user=self.owner,
            academic_year="2025/2026",
            department_code="А-КАФЕДРА",
            full_name="Иванов Иван",
        )
        PlanningRosterEntry.objects.create(
            user=self.owner,
            academic_year="2025/2026",
            department_code="Б-КАФЕДРА",
            full_name="Иванов Иван",
        )
        Activity.objects.create(
            owner=self.owner,
            activity_type=self.article_type,
            title="Единственный пункт плана",
            academic_year="2025/2026",
            quantity=1,
        )
        self.client.force_login(self.other_user)

        response = self.client.get(
            reverse("activities:statistics"),
            {"year": "2025/2026"},
        )

        self.assertEqual(response.context["summary"]["people"], 1)
        self.assertEqual(response.context["summary"]["planned"], 1)
        self.assertEqual(len(response.context["department_rows"]), 1)

    def test_roster_sync_uses_people_from_individual_plans_and_matrix_uses_roster(self):
        source_user = get_user_model().objects.create_user(
            username="plan_source_user",
            password="1234",
            first_name="Сидорова Мария Ивановна",
            last_name="",
            org_unit=self.unit,
        )
        stats = sync_planning_roster(
            [
                ExtractedRosterPerson(
                    department_code="КИСМ",
                    full_name="Сидорова Мария Ивановна",
                    source_file="КИСМ/ИП Сидорова.xlsx",
                )
            ],
            "2025/2026",
        )

        self.assertEqual(stats["created"], 1)
        roster_entry = PlanningRosterEntry.objects.get(user=source_user)
        self.assertEqual(roster_entry.department_code, "КИСМ")
        self.assertEqual(roster_entry.source_files, ["КИСМ/ИП Сидорова.xlsx"])

        self.client.force_login(self.owner)
        response = self.client.get(reverse("activities:matrix"), {"year": "2025/2026"})

        self.assertContains(response, "КИСМ")
        self.assertContains(response, "Сидорова Мария Ивановна")
        self.assertEqual(response.context["roster_count"], 1)

    def test_roster_sync_normalizes_yo_and_creates_missing_employee_when_requested(self):
        existing_user = get_user_model().objects.create_user(
            username="gerasimova_av",
            password="1234",
            first_name="Герасимова Алёна Владимировна",
            last_name="",
            org_unit=self.unit,
        )
        stats = sync_planning_roster(
            [
                ExtractedRosterPerson("ТТПН", "Герасимова Алена Владимировна", "ТТПН/Герасимова.xlsx"),
                ExtractedRosterPerson("ПЗОС", "Дмитриев Вячеслав Михайлович", "ПЗОС/Дмитриев.xlsx"),
            ],
            "2025/2026",
            create_missing=True,
        )

        self.assertEqual(stats["users_created"], 1)
        self.assertTrue(PlanningRosterEntry.objects.filter(user=existing_user).exists())
        created_user = get_user_model().objects.get(username="dmitriev_vm")
        self.assertTrue(PlanningRosterEntry.objects.filter(user=created_user).exists())

    def test_plan_import_preserves_source_and_updates_the_same_record(self):
        PlanningRosterEntry.objects.create(
            user=self.owner,
            academic_year="2025/2026",
            department_code="КИСМ",
            full_name="Иван Иванов",
            source_files=["КИСМ/ИП Иванова.xlsx"],
        )
        record = ExtractedPlanActivity(
            department_code="КИСМ",
            full_name="Иван Иванов",
            source_file="КИСМ/ИП Иванова.xlsx",
            source_sheet="3",
            source_cell="B31",
            source_text="Подготовка двух заявок на конкурс грантов РНФ",
            title="Подготовка двух заявок на конкурс грантов РНФ",
            activity_type_code="grant",
            grant_type_code="rnf",
            item_index=1,
            quantity=2,
        )

        stats = sync_plan_activities([record], "2025/2026")

        self.assertEqual(stats["created"], 1)
        activity = Activity.objects.get(source_key=record.source_key)
        self.assertEqual(activity.owner, self.owner)
        self.assertEqual(activity.grant_type, self.grant_type)
        self.assertEqual(activity.quantity, 2)
        self.assertEqual(activity.source_file, "КИСМ/ИП Иванова.xlsx")
        self.assertTrue(activity.imported_from_plan)

        updated_record = ExtractedPlanActivity(**{**record.__dict__, "title": "Две заявки РНФ"})
        updated_stats = sync_plan_activities([updated_record], "2025/2026")

        self.assertEqual(updated_stats["created"], 0)
        self.assertEqual(updated_stats["updated"], 1)
        self.assertEqual(Activity.objects.filter(source_key=record.source_key).count(), 1)
        self.assertEqual(Activity.objects.get(source_key=record.source_key).title, "Две заявки РНФ")

    def test_plan_import_classifies_grant_and_extracts_quantity(self):
        title = "Подготовка двух заявок на конкурс грантов РНФ"

        self.assertEqual(_classify_activity_codes(title), ("grant",))
        self.assertEqual(_grant_type_code(title), "rnf")
        self.assertEqual(_quantity_for(title, "grant"), 2)

    def test_plan_quantity_sums_several_explicit_article_counts(self):
        title = "Написание и подготовка к изданию 5 статей ВАК и 4 статей Scopus"

        self.assertEqual(_quantity_for(title, "article"), 9)

    def test_plan_quantity_counts_named_semicolon_list(self):
        title = "Статьи: Первая работа; Вторая работа; Третья работа"

        self.assertEqual(_quantity_for(title, "article"), 3)

    def test_plan_article_in_conference_proceedings_is_not_duplicated_as_conference(self):
        title = "Статья в материалах международной научно-практической конференции"

        self.assertEqual(_classify_activity_codes(title), ("article",))

    def test_plan_keeps_explicit_conference_presentation_and_article_as_two_results(self):
        title = "2 выступления и 2 статьи в материалах международной конференции"

        self.assertEqual(_classify_activity_codes(title), ("article", "conference"))
        self.assertEqual(_quantity_for(title, "article"), 2)
        self.assertEqual(_quantity_for(title, "conference"), 2)

    def test_quantity_does_not_read_number_word_inside_international(self):
        title = "Доклад на международной конференции"

        self.assertEqual(_quantity_for(title, "conference"), 1)

    def test_plan_career_guidance_competition_is_not_an_olympiad(self):
        title = "Проведение конкурсов и семинаров для абитуриентов профориентационного характера"

        self.assertEqual(_classify_activity_codes(title), ("career_guidance",))

    def test_plan_combined_training_caption_is_counted_once(self):
        title = "Повышение квалификации и профессиональная переподготовка: повышение квалификации в ТГТУ"

        self.assertEqual(_classify_activity_codes(title), ("advanced_training",))

    def test_repair_removes_old_duplicate_category_and_reallocates_results(self):
        conference_type = ActivityType.objects.get(code="conference")
        title = "Статья в материалах международной конференции"
        article = Activity.objects.create(
            owner=self.owner,
            activity_type=self.article_type,
            title=title,
            academic_year="2025/2026",
            source_key="old-article-row",
        )
        obsolete_conference = Activity.objects.create(
            owner=self.owner,
            activity_type=conference_type,
            title=title,
            academic_year="2025/2026",
            source_key="old-conference-row",
        )
        result = ScientificResult.objects.create(
            source_key="repair-result",
            source_id="repair-result",
            external_author_id="70001",
            owner=self.owner,
            activity_type=self.article_type,
            planned_activity=obsolete_conference,
            title="Фактическая статья",
            result_year=2025,
            academic_year="2025/2026",
            source_file="science.txt",
        )

        stats = repair_imported_plan_classification("2025/2026")

        self.assertEqual(stats["deleted"], 1)
        self.assertFalse(Activity.objects.filter(pk=obsolete_conference.pk).exists())
        result.refresh_from_db()
        self.assertEqual(result.planned_activity, article)

    def test_editing_imported_result_preserves_user_change_during_next_sync(self):
        PlanningRosterEntry.objects.create(
            user=self.owner,
            academic_year="2025/2026",
            department_code="КИСМ",
            full_name="Иван Иванов",
            source_files=["КИСМ/ИП Иванова.xlsx"],
        )
        record = ExtractedPlanActivity(
            department_code="КИСМ",
            full_name="Иван Иванов",
            source_file="КИСМ/ИП Иванова.xlsx",
            source_sheet="3",
            source_cell="B31",
            source_text="Статья из исходного плана",
            title="Статья из исходного плана",
            activity_type_code="article",
            grant_type_code="",
            item_index=1,
        )
        sync_plan_activities([record], "2025/2026")
        imported = Activity.objects.get(source_key=record.source_key)
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("activities:edit", args=[imported.pk]),
            {
                "activity_type": self.article_type.pk,
                "grant_type": "",
                "title": "Уточненное название статьи",
                "quantity": 2,
                "academic_year": "2025/2026",
                "period": ActivityPeriod.WHOLE_YEAR,
                "status": ActivityStatus.IN_PROGRESS,
                "collaborators": [],
            },
        )

        self.assertRedirects(response, f"{reverse('activities:list')}?scope=mine")
        imported.refresh_from_db()
        self.assertTrue(imported.source_is_overridden)
        self.assertEqual(imported.title, "Уточненное название статьи")

        sync_plan_activities([record], "2025/2026")
        imported.refresh_from_db()
        self.assertEqual(imported.title, "Уточненное название статьи")
        self.assertEqual(imported.quantity, 2)
        self.assertEqual(imported.status, ActivityStatus.IN_PROGRESS)

    def test_matrix_filters_people_by_name(self):
        source_user = get_user_model().objects.create_user(
            username="sidorova_mi",
            password="1234",
            first_name="Сидорова Мария Ивановна",
        )
        other_source_user = get_user_model().objects.create_user(
            username="petrov_ai",
            password="1234",
            first_name="Петров Алексей Иванович",
        )
        PlanningRosterEntry.objects.create(
            user=source_user,
            academic_year="2025/2026",
            department_code="КИСМ",
            full_name="Сидорова Мария Ивановна",
        )
        PlanningRosterEntry.objects.create(
            user=other_source_user,
            academic_year="2025/2026",
            department_code="КИСМ",
            full_name="Петров Алексей Иванович",
        )
        self.client.force_login(self.owner)

        response = self.client.get(reverse("activities:matrix"), {"year": "2025/2026", "q": "сидорова"})

        self.assertContains(response, "Сидорова Мария Ивановна")
        self.assertNotContains(response, "Петров Алексей Иванович")
        self.assertEqual(response.context["matrix_query"], "сидорова")

    def test_science_results_are_idempotent_and_fill_matching_plan_capacity(self):
        activity = Activity.objects.create(
            owner=self.owner,
            activity_type=self.article_type,
            title="Две статьи",
            quantity=2,
            academic_year="2025/2026",
            source_key="plan-two-articles",
        )
        records = [
            ExtractedScientificResult(
                source_id=str(source_id),
                external_author_id="70001",
                title=title,
                result_year=2025,
                activity_type_code="article",
                publication_name="Тестовый журнал",
                publication_details="",
                bibliographic_data="Т. 1",
                source_file="science.txt",
                source_line=line,
                source_payload={"ID": str(source_id)},
            )
            for source_id, title, line in ((101, "Первая фактическая статья", 2), (102, "Вторая фактическая статья", 3))
        ]

        first_stats = sync_scientific_results(records, "2025/2026")
        second_stats = sync_scientific_results(records, "2025/2026")

        activity.refresh_from_db()
        self.assertEqual(first_stats["created"], 2)
        self.assertEqual(first_stats["linked"], 2)
        self.assertEqual(second_stats["created"], 0)
        self.assertEqual(ScientificResult.objects.count(), 2)
        self.assertEqual(ScientificResult.objects.filter(planned_activity=activity).count(), 2)
        self.assertEqual(activity.status, ActivityStatus.COMPLETED)

    def test_science_results_match_specific_titles_before_same_type_fallback(self):
        unrelated_plan = Activity.objects.create(
            owner=self.owner,
            activity_type=self.article_type,
            title='Статья "Моделирование теплового процесса"',
            quantity=1,
            academic_year="2025/2026",
            source_key="specific-plan-unrelated",
        )
        matching_plan = Activity.objects.create(
            owner=self.owner,
            activity_type=self.article_type,
            title='Научная статья "Защита металлов от коррозии" в журнале ВАК',
            quantity=1,
            academic_year="2025/2026",
            source_key="specific-plan-matching",
        )
        records = [
            ExtractedScientificResult(
                source_id="specific-1",
                external_author_id="70001",
                title="Защита металлов от коррозии",
                result_year=2025,
                activity_type_code="article",
                publication_name="Научный журнал",
                publication_details="",
                bibliographic_data="",
                source_file="science.txt",
                source_line=1,
                source_payload={},
            ),
            ExtractedScientificResult(
                source_id="specific-2",
                external_author_id="70001",
                title="Совершенно другая публикация",
                result_year=2025,
                activity_type_code="article",
                publication_name="Другой журнал",
                publication_details="",
                bibliographic_data="",
                source_file="science.txt",
                source_line=2,
                source_payload={},
            ),
        ]

        stats = sync_scientific_results(records, "2025/2026")

        matching_result = ScientificResult.objects.get(source_id="specific-1")
        unrelated_result = ScientificResult.objects.get(source_id="specific-2")
        unrelated_plan.refresh_from_db()
        self.assertEqual(stats["linked"], 2)
        self.assertEqual(matching_result.planned_activity, matching_plan)
        self.assertEqual(unrelated_result.planned_activity, unrelated_plan)
        self.assertEqual(unrelated_plan.status, ActivityStatus.COMPLETED)

    def test_conference_plan_does_not_capture_same_named_article_from_science(self):
        conference_type = ActivityType.objects.get(code="conference")
        conference_plan = Activity.objects.create(
            owner=self.owner,
            activity_type=conference_type,
            title='Тезисы доклада "Анализ параметров системы" на международной конференции',
            quantity=1,
            academic_year="2025/2026",
            source_key="conference-plan-by-title",
        )
        record = ExtractedScientificResult(
            source_id="conference-as-article",
            external_author_id="70001",
            title="Анализ параметров системы",
            result_year=2025,
            activity_type_code="article",
            publication_name="Труды международной конференции",
            publication_details="",
            bibliographic_data="",
            source_file="science.txt",
            source_line=3,
            source_payload={},
        )

        stats = sync_scientific_results([record], "2025/2026")

        result = ScientificResult.objects.get(source_id="conference-as-article")
        self.assertEqual(stats["linked"], 0)
        self.assertIsNone(result.planned_activity)

    def test_science_extraction_recovers_approved_row_shifted_by_raw_semicolon(self):
        header = (
            "IMPACT5F;FORMAT_CONF;PATENT_TYPE;RSCI_FLG;LIST_PEER_REVIEWED;REF_PROFILE;"
            "THEMATIC_CTG;ID;SCIENCE_TYPE;YEAR;NAME;VOLUME;CIRCULATION;PUBLISHER_ID;"
            "WORK_TYPE_ID;STAMP_ID;PATENT_DATE;SHOW_NAME;SHOW_PLACE;PAGE_BEGIN;PAGE_END;"
            "VAK_FLG;STAFF_ID;STAFF_SUB_UNITS_ID;INVESTIGATION_ID;FINANCING_ID;OUT_DATA;"
            "PERIOD_ID;RINZ_FLG;WOS_FLG;SCOPUS_FLG;FOREIGN_FLG;EI_FLG;AUTHOR_STAFF_ID;"
            "AUTHOR_DATE;INSPECTOR_STAFF_ID;INSPECTOR_DATE;INSPECTOR_FLG;INSPECTOR_COMMENT;"
            "SPEECH_FLG;WOS_Q1Q2_FLG;SCOPUS_Q1Q2_FLG"
        ).split(";")
        fields = [""] * len(header)
        fields[7] = "401"
        fields[8] = "0"
        fields[9] = "2025"
        fields[10] = "Монография"
        fields[33] = "70001"
        fields[34] = "2025-12-01T10:00:00"
        fields[35] = "100"
        fields[36] = "2025-12-02T10:00:00"
        fields[37] = "A"
        shifted_fields = fields[:11] + ["с подзаголовком"] + fields[11:]

        with TemporaryDirectory() as directory:
            source = Path(directory) / "science.txt"
            source.write_text(
                ";".join(header) + "\n" + ";".join(shifted_fields) + "\n",
                encoding="utf-8",
            )
            records, errors = extract_scientific_results(source)

        self.assertEqual(errors, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].source_id, "401")
        self.assertEqual(records[0].external_author_id, "70001")
        self.assertEqual(records[0].title, "Монография; с подзаголовком")
        self.assertTrue(records[0].source_payload["_RECOVERED_FROM_SHIFT"])

    def test_partial_science_result_marks_plan_in_progress_and_is_visible(self):
        activity = Activity.objects.create(
            owner=self.owner,
            activity_type=self.article_type,
            title="План из двух публикаций",
            quantity=2,
            academic_year="2025/2026",
            source_key="plan-partial-articles",
        )
        record = ExtractedScientificResult(
            source_id="201",
            external_author_id="70001",
            title="Подтверждённая публикация",
            result_year=2025,
            activity_type_code="article",
            publication_name="Научный журнал",
            publication_details="",
            bibliographic_data="2025. № 1",
            source_file="science.txt",
            source_line=4,
            source_payload={"ID": "201"},
        )
        sync_scientific_results([record], "2025/2026")
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("activities:list"),
            {"scope": "mine", "year": "2025/2026"},
        )

        activity.refresh_from_db()
        self.assertEqual(activity.status, ActivityStatus.IN_PROGRESS)
        self.assertEqual(response.context["summary"]["completed"], 1)
        self.assertEqual(response.context["summary"]["in_progress"], 1)
        self.assertContains(response, "Подтверждённая публикация")
        self.assertContains(response, "Зачтено в этот пункт 1/2")
        self.assertContains(response, "Показать написанные статьи")

    def test_unmatched_science_author_is_kept_without_guessing_an_owner(self):
        record = ExtractedScientificResult(
            source_id="301",
            external_author_id="999999",
            title="Результат отсутствующего сотрудника",
            result_year=2025,
            activity_type_code="article",
            publication_name="",
            publication_details="",
            bibliographic_data="",
            source_file="science.txt",
            source_line=5,
            source_payload={"ID": "301"},
        )

        stats = sync_scientific_results([record], "2025/2026")

        result = ScientificResult.objects.get(source_id="301")
        self.assertIsNone(result.owner)
        self.assertEqual(len(stats["unmatched"]), 1)

    def test_superuser_can_open_employee_plan_with_progress_circle(self):
        admin = get_user_model().objects.create_superuser(
            username="plan_admin",
            password="1234",
        )
        Activity.objects.create(
            owner=self.owner,
            activity_type=self.article_type,
            title="Выполненная статья сотрудника",
            academic_year="2025/2026",
            status=ActivityStatus.COMPLETED,
        )
        self.client.force_login(admin)

        response = self.client.get(
            reverse("activities:list"),
            {"scope": "mine", "owner": self.owner.pk, "year": "2025/2026"},
        )

        self.assertContains(response, "План сотрудника")
        self.assertContains(response, "Иван Иванов")
        self.assertContains(response, "Выполнено 100 процентов плана")
        self.assertEqual(response.context["plan_subject"], self.owner)
        self.assertTrue(response.context["is_employee_plan_preview"])
        self.assertEqual(response.context["summary"]["completed"], 1)

    def test_regular_user_can_open_another_employee_plan_with_workplace_details(self):
        workplace = OrgUnit.objects.create(name="Институт цифровых технологий")
        position = Position.objects.create(name="Доцент")
        self.other_user.position = position
        self.other_user.org_unit = workplace
        self.other_user.chair_org_unit = self.unit
        self.other_user.save(update_fields=["position", "org_unit", "chair_org_unit"])
        Activity.objects.create(
            owner=self.other_user,
            activity_type=self.article_type,
            title="Чужой пункт через owner",
            academic_year="2025/2026",
        )
        Activity.objects.create(
            owner=self.owner,
            activity_type=self.article_type,
            title="Собственный пункт",
            academic_year="2025/2026",
        )
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("activities:list"),
            {"scope": "mine", "owner": self.other_user.pk, "year": "2025/2026"},
        )

        self.assertContains(response, "План сотрудника")
        self.assertContains(response, "Чужой пункт через owner")
        self.assertNotContains(response, "Собственный пункт")
        self.assertContains(response, "Доцент")
        self.assertContains(response, "Кафедра тестовых результатов")
        self.assertContains(response, "Институт цифровых технологий")
        self.assertTrue(response.context["is_employee_plan_preview"])
