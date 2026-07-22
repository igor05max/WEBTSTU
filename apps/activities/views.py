from collections import OrderedDict, defaultdict
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.db.models import Count, Q, Sum
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.activities.forms import ActivityForm
from apps.activities.models import (
    Activity,
    ActivityArea,
    ActivityStatus,
    ActivityType,
    PlanningRosterEntry,
    ScientificResult,
    get_current_academic_year,
)


TYPE_CODE_ORDER = (
    "article",
    "monograph",
    "grant",
    "research_project",
    "contract_research",
    "conference",
    "patent",
    "software_registration",
    "dissertation",
    "textbook",
    "teaching_aid",
    "methodical_material",
    "work_program",
    "online_course",
    "student_research",
    "olympiad",
    "career_guidance",
    "educational_event",
    "advanced_training",
    "professional_retraining",
    "other",
)
TYPE_CODE_POSITION = {code: position for position, code in enumerate(TYPE_CODE_ORDER)}
AREA_ORDER = tuple(ActivityArea.values)


def get_ordered_activity_types():
    activity_types = list(
        ActivityType.objects.filter(Q(is_active=True) | Q(activities__isnull=False)).distinct()
    )
    return sorted(
        activity_types,
        key=lambda activity_type: (
            AREA_ORDER.index(activity_type.area) if activity_type.area in AREA_ORDER else len(AREA_ORDER),
            TYPE_CODE_POSITION.get(activity_type.code, len(TYPE_CODE_ORDER)),
            activity_type.name,
        ),
    )


def build_text_search_filter(query, *fields):
    """Build a Cyrillic-safe contains filter for SQLite and production databases."""
    search_values = {
        query,
        query.lower(),
        query.capitalize(),
        query.title(),
        query.upper(),
    }
    search_filter = Q()
    for search_value in search_values:
        for field in fields:
            search_filter |= Q(**{f"{field}__icontains": search_value})
    return search_filter


@login_required
def activity_list(request):
    scope = request.GET.get("scope", "all")
    if scope not in {"all", "mine"}:
        scope = "all"
    # The personal page is a direct view of the employee's plan.  Filters make
    # it look like a search screen and can accidentally hide planned records,
    # so they are available only in the common registry.
    query = (request.GET.get("q") or "").strip() if scope == "all" else ""
    selected_type = request.GET.get("type", "").strip() if scope == "all" else ""
    selected_year = request.GET.get("year", "").strip()
    # Plans are already visible to authenticated users in the common registry.
    # Keep the same data available as a focused employee plan when a person is
    # opened from the matrix or statistics pages.
    can_view_employee_plan = scope == "mine"
    selected_owner = (
        request.GET.get("owner", "").strip()
        if scope == "all" or can_view_employee_plan
        else ""
    )
    selected_owner_object = None
    if selected_owner.isdigit():
        selected_owner_object = (
            get_user_model()
            .objects.select_related("position", "org_unit", "chair_org_unit")
            .filter(pk=int(selected_owner))
            .first()
        )
        if selected_owner_object is None:
            selected_owner = ""
    else:
        selected_owner = ""

    activities = Activity.objects.select_related(
        "owner__org_unit",
        "owner__position",
        "activity_type",
        "grant_type",
    ).prefetch_related("collaborators")
    scientific_results = ScientificResult.objects.select_related(
        "owner__org_unit",
        "activity_type",
        "planned_activity",
    )
    if scope == "mine":
        if selected_owner_object is not None:
            activities = activities.filter(owner=selected_owner_object)
            scientific_results = scientific_results.filter(owner=selected_owner_object)
        else:
            activities = activities.filter(Q(owner=request.user) | Q(collaborators=request.user)).distinct()
            scientific_results = scientific_results.filter(owner=request.user)
    elif selected_owner_object is not None:
        activities = activities.filter(owner=selected_owner_object)
        scientific_results = scientific_results.filter(owner=selected_owner_object)

    available_years = sorted(
        set(activities.values_list("academic_year", flat=True))
        | set(scientific_results.values_list("academic_year", flat=True)),
        reverse=True,
    )
    if scope == "mine" and not selected_year:
        selected_year = available_years[0] if available_years else get_current_academic_year()
    if selected_year:
        activities = activities.filter(academic_year=selected_year)
        scientific_results = scientific_results.filter(academic_year=selected_year)

    if query:
        activities = activities.filter(
            Q(title__icontains=query)
            | Q(owner__first_name__icontains=query)
            | Q(owner__last_name__icontains=query)
            | Q(owner__username__icontains=query)
            | Q(collaborators__first_name__icontains=query)
            | Q(collaborators__last_name__icontains=query)
        ).distinct()
    if selected_type.isdigit():
        selected_type_id = int(selected_type)
        activities = activities.filter(activity_type_id=selected_type_id)
        scientific_results = scientific_results.filter(
            Q(activity_type_id=selected_type_id)
            | Q(planned_activity__activity_type_id=selected_type_id)
        ).distinct()
    else:
        selected_type = ""
    visible_activities = list(
        activities.order_by("period", "activity_type__name", "title", "pk")
    )
    visible_scientific_results = list(
        scientific_results.order_by("-result_year", "activity_type__name", "title", "pk")
    )
    visible_activity_ids = {activity.pk for activity in visible_activities}
    results_by_activity = defaultdict(list)
    unplanned_scientific_results = []
    for result in visible_scientific_results:
        if result.planned_activity_id in visible_activity_ids:
            results_by_activity[result.planned_activity_id].append(result)
        else:
            unplanned_scientific_results.append(result)
    for activity in visible_activities:
        activity.can_manage = activity.can_be_managed_by(request.user)
        activity.scientific_results_list = results_by_activity.get(activity.pk, [])
        activity.actual_count = len(activity.scientific_results_list)

    scientific_result_groups_by_type = OrderedDict()
    for result in visible_scientific_results:
        group = scientific_result_groups_by_type.setdefault(
            result.activity_type_id,
            {
                "name": result.activity_type.name,
                "is_article": result.activity_type.code == "article",
                "results": [],
            },
        )
        group["results"].append(result)
    scientific_result_groups = sorted(
        scientific_result_groups_by_type.values(),
        key=lambda group: (-len(group["results"]), group["name"]),
    )

    total_quantity = sum(activity.quantity for activity in visible_activities)
    completed_quantity = planned_quantity = in_progress_quantity = 0
    for activity in visible_activities:
        if activity.status == ActivityStatus.COMPLETED:
            completed_quantity += activity.quantity
        elif activity.actual_count:
            actual_units = min(activity.actual_count, activity.quantity)
            completed_quantity += actual_units
            in_progress_quantity += activity.quantity - actual_units
        elif activity.status == ActivityStatus.IN_PROGRESS:
            in_progress_quantity += activity.quantity
        else:
            planned_quantity += activity.quantity
    summary = {
        "items": len(visible_activities),
        "total": total_quantity,
        "planned": planned_quantity,
        "in_progress": in_progress_quantity,
        "completed": completed_quantity,
        "remaining": max(total_quantity - completed_quantity, 0),
        "progress_percent": (
            min(100, round((completed_quantity / total_quantity) * 100))
            if total_quantity
            else 0
        ),
    }

    type_totals = OrderedDict()
    for activity in visible_activities:
        type_totals.setdefault(activity.activity_type.name, 0)
        type_totals[activity.activity_type.name] += activity.quantity
    type_breakdown = [
        {
            "name": name,
            "total": total,
            "percent": round((total / total_quantity) * 100) if total_quantity else 0,
        }
        for name, total in sorted(type_totals.items(), key=lambda item: (-item[1], item[0]))
    ]

    if scope == "mine":
        years = available_years
        if selected_year and selected_year not in years:
            years = sorted([*years, selected_year], reverse=True)
    else:
        years = sorted(
            set(Activity.objects.values_list("academic_year", flat=True))
            | set(ScientificResult.objects.values_list("academic_year", flat=True)),
            reverse=True,
        )

    plan_subject = selected_owner_object or request.user
    plan_subject_name = plan_subject.get_full_name().strip() or plan_subject.username
    plan_subject_initials = "".join(
        part[0] for part in plan_subject_name.split()[:2] if part
    ).upper()
    plan_subject_department = getattr(plan_subject, "chair_org_unit", None)
    if plan_subject_department is None:
        general_unit = getattr(plan_subject, "org_unit", None)
        if general_unit is not None and general_unit.name.startswith("Кафедра"):
            plan_subject_department = general_unit

    return render(
        request,
        "activities/list.html",
        {
            "activities": visible_activities,
            "activity_types": get_ordered_activity_types(),
            "years": years,
            "summary": summary,
            "current_scope": scope,
            "query": query,
            "selected_type": selected_type,
            "selected_year": selected_year,
            "selected_owner": selected_owner,
            "selected_owner_object": selected_owner_object,
            "type_breakdown": type_breakdown,
            "has_imported_plan": any(activity.imported_from_plan for activity in visible_activities),
            "scientific_results": visible_scientific_results,
            "scientific_result_groups": scientific_result_groups,
            "unplanned_scientific_results": unplanned_scientific_results,
            "scientific_result_count": len(visible_scientific_results),
            "plan_subject": plan_subject,
            "plan_subject_profile": {
                "name": plan_subject_name,
                "initials": plan_subject_initials or "С",
                "position": getattr(getattr(plan_subject, "position", None), "name", "")
                or "Не указана",
                "department": getattr(plan_subject_department, "name", "")
                or "Не указана",
                "workplace": getattr(getattr(plan_subject, "org_unit", None), "name", "")
                or "Не указано",
            },
            "is_employee_plan_preview": bool(scope == "mine" and selected_owner_object),
        },
    )


@login_required
def activity_matrix(request):
    selected_year = request.GET.get("year", "").strip()
    matrix_query = (request.GET.get("q") or "").strip()
    all_years = sorted(
        set(Activity.objects.values_list("academic_year", flat=True))
        | set(PlanningRosterEntry.objects.values_list("academic_year", flat=True))
        | set(ScientificResult.objects.values_list("academic_year", flat=True)),
        reverse=True,
    )
    if not selected_year and get_current_academic_year() in all_years:
        selected_year = get_current_academic_year()
    elif not selected_year and all_years:
        selected_year = all_years[0]

    activities = Activity.objects.all()
    if selected_year:
        activities = activities.filter(academic_year=selected_year)

    counts = defaultdict(int)
    matched_counts = defaultdict(int)
    unplanned_counts = defaultdict(int)
    grant_summaries = defaultdict(list)
    for row in activities.values("owner_id", "activity_type_id", "grant_type__name").annotate(total=Sum("quantity")):
        key = (row["owner_id"], row["activity_type_id"])
        counts[key] += row["total"]
        if row["grant_type__name"]:
            grant_summaries[key].append(f"{row['grant_type__name']} — {row['total']}")

    scientific_results = ScientificResult.objects.filter(owner__isnull=False)
    if selected_year:
        scientific_results = scientific_results.filter(academic_year=selected_year)
    for row in scientific_results.filter(planned_activity__isnull=False).values(
        "owner_id", "activity_type_id"
    ).annotate(total=Count("id")):
        matched_counts[(row["owner_id"], row["activity_type_id"])] = row["total"]
    for row in scientific_results.filter(planned_activity__isnull=True).values(
        "owner_id", "activity_type_id"
    ).annotate(total=Count("id")):
        unplanned_counts[(row["owner_id"], row["activity_type_id"])] = row["total"]

    activity_types = get_ordered_activity_types()
    type_groups_by_area = OrderedDict()
    for activity_type in activity_types:
        type_groups_by_area.setdefault(activity_type.area, []).append(activity_type)
    type_groups = [
        {
            "name": ActivityArea(area).label if area in ActivityArea.values else area,
            "types": types,
        }
        for area, types in type_groups_by_area.items()
    ]

    departments = OrderedDict()
    roster_entries = PlanningRosterEntry.objects.select_related("user__position").order_by(
        "department_code", "full_name"
    )
    if selected_year:
        roster_entries = roster_entries.filter(academic_year=selected_year)
    roster_count = roster_entries.count()
    if matrix_query:
        roster_entries = roster_entries.filter(
            build_text_search_filter(
                matrix_query,
                "full_name",
                "user__first_name",
                "user__last_name",
                "user__username",
            )
        ).distinct()
    roster_entries = list(roster_entries)
    if roster_entries:
        matrix_people = [
            {"person": entry.user, "department_name": entry.department_code, "source_files": entry.source_files}
            for entry in roster_entries
        ]
    else:
        User = get_user_model()
        matrix_people = []
        people = User.objects.filter(is_active=True, is_superuser=False)
        if matrix_query:
            people = people.filter(
                build_text_search_filter(
                    matrix_query,
                    "first_name",
                    "last_name",
                    "username",
                )
            )
        for person in people.select_related(
            "chair_org_unit", "org_unit", "position"
        ).order_by("chair_org_unit__name", "org_unit__name", "last_name", "first_name", "username"):
            department_name = person.get_chair_name()
            if not department_name and person.org_unit_id:
                department_name = person.org_unit.name
                if department_name.startswith("Кафедра "):
                    department_name = department_name[len("Кафедра ") :].strip().strip('"')
            matrix_people.append({"person": person, "department_name": department_name or "Без кафедры", "source_files": []})

    for matrix_person in matrix_people:
        person = matrix_person["person"]
        department_name = matrix_person["department_name"]
        cells = []
        for activity_type in activity_types:
            key = (person.pk, activity_type.pk)
            planned_count = counts[key]
            matched_count = matched_counts[key]
            unplanned_count = unplanned_counts[key]
            # The matrix shows the employee's complete factual output for a
            # result type. Results beyond the plan therefore increase the
            # numerator instead of being rendered as a separate text label.
            actual_count = matched_count + unplanned_count
            extra_count = unplanned_count if planned_count else 0
            detail_params = {"owner": person.pk, "type": activity_type.pk}
            if selected_year:
                detail_params["year"] = selected_year
            cells.append(
                {
                    "count": planned_count,
                    "planned_count": planned_count,
                    "actual_count": actual_count,
                    "extra_count": extra_count,
                    "has_value": bool(planned_count or actual_count or extra_count),
                    "ratio_state": (
                        "is-complete"
                        if planned_count and actual_count >= planned_count
                        else "is-progress"
                        if actual_count
                        else "is-planned"
                    ),
                    "type_name": activity_type.name,
                    "grant_summary": grant_summaries[key],
                    "url": f"{reverse('activities:list')}?{urlencode({'scope': 'all', **detail_params})}",
                }
            )
        departments.setdefault(department_name, []).append(
            {
                "person": person,
                "cells": cells,
                "source_files": matrix_person["source_files"],
                "plan_url": f"{reverse('activities:list')}?{urlencode({'scope': 'mine', 'owner': person.pk, **({'year': selected_year} if selected_year else {})})}",
            }
        )

    return render(
        request,
        "activities/matrix.html",
        {
            "departments": departments.items(),
            "type_groups": type_groups,
            "years": all_years,
            "selected_year": selected_year,
            "total_type_count": len(activity_types),
            "roster_count": roster_count,
            "uses_plan_roster": bool(roster_count),
            "matrix_query": matrix_query,
            "visible_people_count": len(matrix_people),
        },
    )


@login_required
def activity_statistics(request):
    selected_year = request.GET.get("year", "").strip()
    all_years = sorted(
        set(Activity.objects.values_list("academic_year", flat=True))
        | set(PlanningRosterEntry.objects.values_list("academic_year", flat=True))
        | set(ScientificResult.objects.values_list("academic_year", flat=True)),
        reverse=True,
    )
    if not selected_year and get_current_academic_year() in all_years:
        selected_year = get_current_academic_year()
    elif not selected_year and all_years:
        selected_year = all_years[0]

    roster_entries = PlanningRosterEntry.objects.select_related(
        "user__position", "user__org_unit", "user__chair_org_unit"
    )
    if selected_year:
        roster_entries = roster_entries.filter(academic_year=selected_year)
    roster_entries = list(roster_entries.order_by("department_code", "full_name"))

    departments = OrderedDict()
    owner_names = {}
    owner_objects = {}
    for entry in roster_entries:
        # A person may occur in more than one imported roster file. Statistics
        # must still count their plan and factual results exactly once.
        if entry.user_id in owner_objects:
            continue
        department_people = departments.setdefault(entry.department_code, OrderedDict())
        department_people[entry.user_id] = entry
        owner_names[entry.user_id] = entry.full_name
        owner_objects[entry.user_id] = entry.user

    selected_department = (request.GET.get("department") or "all").strip()
    if selected_department != "all" and selected_department not in departments:
        selected_department = "all"

    all_owner_ids = set(owner_objects)
    selected_owner_ids = (
        all_owner_ids
        if selected_department == "all"
        else set(departments.get(selected_department, ()))
    )

    planned_by_owner = defaultdict(int)
    planned_by_owner_type = defaultdict(int)
    activity_rows = Activity.objects.none()
    if all_owner_ids:
        activity_rows = Activity.objects.filter(owner_id__in=all_owner_ids)
        if selected_year:
            activity_rows = activity_rows.filter(academic_year=selected_year)
        for row in activity_rows.values("owner_id", "activity_type_id").annotate(
            total=Sum("quantity")
        ):
            key = (row["owner_id"], row["activity_type_id"])
            planned_by_owner_type[key] = row["total"]
            planned_by_owner[row["owner_id"]] += row["total"]

    confirmed_by_owner = defaultdict(int)
    confirmed_by_owner_type = defaultdict(int)
    extra_by_owner = defaultdict(int)
    extra_by_owner_type = defaultdict(int)
    result_rows = ScientificResult.objects.none()
    if all_owner_ids:
        result_rows = ScientificResult.objects.filter(owner_id__in=all_owner_ids)
        if selected_year:
            result_rows = result_rows.filter(academic_year=selected_year)
        for row in result_rows.filter(planned_activity__isnull=False).values(
            "owner_id", "activity_type_id"
        ).annotate(total=Count("id")):
            key = (row["owner_id"], row["activity_type_id"])
            confirmed_by_owner_type[key] = row["total"]
            confirmed_by_owner[row["owner_id"]] += row["total"]
        for row in result_rows.filter(planned_activity__isnull=True).values(
            "owner_id", "activity_type_id"
        ).annotate(total=Count("id")):
            key = (row["owner_id"], row["activity_type_id"])
            extra_by_owner_type[key] = row["total"]
            extra_by_owner[row["owner_id"]] += row["total"]

    def completion_percent(confirmed, planned):
        return min(100, round((confirmed / planned) * 100)) if planned else 0

    def progress_state(percent, planned):
        if not planned:
            return "Без плана", "is-neutral"
        if percent >= 100:
            return "Выполнено", "is-complete"
        if percent >= 60:
            return "В работе", "is-progress"
        if percent:
            return "Требует внимания", "is-attention"
        return "Не начато", "is-empty"

    department_rows = []
    for department_code, people in departments.items():
        owner_ids = set(people)
        planned = sum(planned_by_owner[owner_id] for owner_id in owner_ids)
        confirmed = sum(confirmed_by_owner[owner_id] for owner_id in owner_ids)
        extra = sum(extra_by_owner[owner_id] for owner_id in owner_ids)
        percent = completion_percent(confirmed, planned)
        department_rows.append(
            {
                "code": department_code,
                "people_count": len(owner_ids),
                "planned": planned,
                "confirmed": confirmed,
                "extra": extra,
                "percent": percent,
                "is_selected": selected_department == department_code,
                "url": f"{reverse('activities:statistics')}?{urlencode({'year': selected_year, 'department': department_code})}",
            }
        )

    employee_rows = []
    for owner_id in selected_owner_ids:
        person = owner_objects[owner_id]
        planned = planned_by_owner[owner_id]
        confirmed = confirmed_by_owner[owner_id]
        extra = extra_by_owner[owner_id]
        percent = completion_percent(confirmed, planned)
        status_label, status_class = progress_state(percent, planned)
        display_name = owner_names.get(owner_id) or person.get_full_name() or person.username
        initials = "".join(part[0] for part in display_name.split()[:2]).upper()
        employee_rows.append(
            {
                "person": person,
                "name": display_name,
                "initials": initials or "С",
                "planned": planned,
                "confirmed": confirmed,
                "remaining": max(planned - confirmed, 0),
                "extra": extra,
                "percent": percent,
                "status_label": status_label,
                "status_class": status_class,
                "plan_url": f"{reverse('activities:list')}?{urlencode({'scope': 'mine', 'owner': owner_id, 'year': selected_year})}",
            }
        )
    employee_rows.sort(key=lambda row: (-row["confirmed"], -row["percent"], row["name"]))

    type_rows = []
    for activity_type in get_ordered_activity_types():
        planned = sum(
            planned_by_owner_type[(owner_id, activity_type.pk)]
            for owner_id in selected_owner_ids
        )
        confirmed = sum(
            confirmed_by_owner_type[(owner_id, activity_type.pk)]
            for owner_id in selected_owner_ids
        )
        extra = sum(
            extra_by_owner_type[(owner_id, activity_type.pk)]
            for owner_id in selected_owner_ids
        )
        if not (planned or confirmed or extra):
            continue
        type_rows.append(
            {
                "name": activity_type.name,
                "area": activity_type.get_area_display(),
                "planned": planned,
                "confirmed": confirmed,
                "extra": extra,
                "remaining": max(planned - confirmed, 0),
                "percent": completion_percent(confirmed, planned),
            }
        )
    type_rows.sort(key=lambda row: (-row["planned"], -row["confirmed"], row["name"]))

    planned_total = sum(planned_by_owner[owner_id] for owner_id in selected_owner_ids)
    confirmed_total = sum(confirmed_by_owner[owner_id] for owner_id in selected_owner_ids)
    extra_total = sum(extra_by_owner[owner_id] for owner_id in selected_owner_ids)
    progress_percent = completion_percent(confirmed_total, planned_total)
    completed_people = sum(
        1
        for row in employee_rows
        if row["planned"] and row["confirmed"] >= row["planned"]
    )

    return render(
        request,
        "activities/statistics.html",
        {
            "years": all_years,
            "selected_year": selected_year,
            "selected_department": selected_department,
            "selected_department_label": (
                "Все кафедры" if selected_department == "all" else selected_department
            ),
            "department_rows": department_rows,
            "employee_rows": employee_rows,
            "type_rows": type_rows,
            "summary": {
                "people": len(selected_owner_ids),
                "planned": planned_total,
                "confirmed": confirmed_total,
                "remaining": max(planned_total - confirmed_total, 0),
                "extra": extra_total,
                "progress_percent": progress_percent,
                "completed_people": completed_people,
            },
        },
    )


@login_required
def activity_create(request):
    if request.method == "POST":
        form = ActivityForm(request.POST)
        if form.is_valid():
            activity = form.save(commit=False)
            activity.owner = request.user
            activity.save()
            form.save_m2m()
            messages.success(request, "Результат добавлен в общий реестр.")
            return redirect(f"{reverse('activities:list')}?scope=mine")
    else:
        form = ActivityForm()
    return render(
        request,
        "activities/form.html",
        {"form": form, "page_title": "Добавить планируемый результат", "activity": None},
    )


@login_required
def activity_edit(request, pk):
    activity = get_object_or_404(Activity, pk=pk)
    if not activity.can_be_managed_by(request.user):
        return HttpResponseForbidden("Изменять результат может только ответственный сотрудник.")

    if request.method == "POST":
        form = ActivityForm(request.POST, instance=activity)
        if form.is_valid():
            activity = form.save(commit=False)
            if activity.imported_from_plan:
                activity.source_is_overridden = True
            activity.save()
            form.save_m2m()
            messages.success(
                request,
                "Результат обновлен. Правка сохранена и не будет затерта повторным импортом плана."
                if activity.imported_from_plan
                else "Результат обновлен.",
            )
            return redirect(f"{reverse('activities:list')}?scope=mine")
    else:
        form = ActivityForm(instance=activity)
    return render(
        request,
        "activities/form.html",
        {"form": form, "page_title": "Изменить планируемый результат", "activity": activity},
    )


@login_required
def activity_delete(request, pk):
    activity = get_object_or_404(Activity, pk=pk)
    if not activity.can_be_managed_by(request.user):
        return HttpResponseForbidden("Удалять результат может только ответственный сотрудник.")
    if request.method == "POST":
        activity.delete()
        messages.success(request, "Результат удален из реестра.")
    return redirect(f"{reverse('activities:list')}?scope=mine")
