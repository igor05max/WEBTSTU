from django import forms
from django.contrib import messages
from django.contrib import admin
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html

from apps.accounts.models import User
from apps.directory.models import OrgUnit
from apps.workflow.models import (
    ApprovalTask,
    AssigneeKind,
    ApprovalTaskStatus,
    RouteStepDirectionAssignment,
    RouteStepTemplate,
    RouteTemplate,
    TaskDecision,
    WorkflowRun,
    WorkflowStep,
)
from apps.workflow.services import insert_manual_step


class HiddenFromAdminIndexMixin:
    def get_model_perms(self, request):
        return {}


SUPPORTED_ROUTE_STEP_ASSIGNEE_KINDS = (
    (AssigneeKind.FIXED_UNIT_GROUP, AssigneeKind.FIXED_UNIT_GROUP.label),
    (AssigneeKind.AUTHOR_CHAIR_HEAD, AssigneeKind.AUTHOR_CHAIR_HEAD.label),
)


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]


def _sync_step_open_tasks(step):
    ApprovalTask.objects.filter(
        workflow_step=step,
        status__in=[ApprovalTaskStatus.PENDING, ApprovalTaskStatus.ACTIVE],
    ).update(
        assigned_group=step.assigned_group,
        assigned_unit=step.assigned_unit,
        assigned_user=step.assigned_user,
    )


class RouteStepTemplateAdminForm(forms.ModelForm):
    class Meta:
        model = RouteStepTemplate
        fields = (
            "order",
            "name",
            "assignee_kind",
            "target_unit",
            "target_group",
            "target_user",
            "can_reject",
            "can_request_revision",
        )

    class Media:
        js = ("workflow/admin_assignment_filters.js",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assignee_kind"].choices = SUPPORTED_ROUTE_STEP_ASSIGNEE_KINDS
        self.fields["assignee_kind"].label = "Тип исполнителя"
        self.fields["assignee_kind"].help_text = (
            'Выберите обычную роль в указанной группе или автоматический вариант "Заведующий кафедрой автора".'
        )
        target_unit_value = self.data.get(self.add_prefix("target_unit")) or getattr(
            self.instance, "target_unit_id", None
        )
        groups_queryset = Group.objects.none()
        if target_unit_value:
            groups_queryset = Group.objects.filter(org_units__id=target_unit_value).order_by("name").distinct()
        self.fields["target_group"].queryset = groups_queryset
        self.fields["target_unit"].required = False
        self.fields["target_group"].required = False
        self.fields["target_user"].required = False
        self.fields["target_unit"].label = "Группа по умолчанию"
        self.fields["target_group"].label = "Роль по умолчанию"
        self.fields["target_unit"].help_text = (
            "Необязательно. Если поле пустое, исполнители должны быть настроены через назначения по областям."
        )
        self.fields["target_group"].help_text = (
            "Необязательно. Используется как базовое назначение, если для области нет отдельного правила."
        )

        target_group_value = self.data.get(self.add_prefix("target_group")) or getattr(
            self.instance, "target_group_id", None
        )
        user_queryset = self.fields["target_user"].queryset.none()
        if target_unit_value and target_group_value:
            user_queryset = (
                self.fields["target_user"]
                .queryset.filter(org_unit_id=target_unit_value, groups__id=target_group_value)
                .order_by("last_name", "first_name", "username")
                .distinct()
            )
        self.fields["target_user"].queryset = user_queryset
        self.fields["target_user"].label = "Проверяющий по умолчанию"

    def clean(self):
        cleaned_data = super().clean()
        assignee_kind = cleaned_data.get("assignee_kind")
        target_unit = cleaned_data.get("target_unit")
        target_group = cleaned_data.get("target_group")
        target_user = cleaned_data.get("target_user")

        if assignee_kind == AssigneeKind.AUTHOR_CHAIR_HEAD:
            return cleaned_data

        if not target_unit and not target_group and not target_user:
            return cleaned_data

        if target_unit is None:
            self.add_error("target_unit", "Нужно выбрать группу.")
        if target_group is None:
            self.add_error("target_group", "Нужно выбрать роль.")

        if target_unit is None or target_group is None or target_user is None:
            return cleaned_data

        if target_user.org_unit_id != target_unit.id:
            self.add_error("target_user", "Пользователь должен относиться к выбранной группе.")

        if not target_user.groups.filter(id=target_group.id).exists():
            self.add_error("target_user", "У пользователя нет выбранной роли внутри этой группы.")

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        if instance.assignee_kind == AssigneeKind.AUTHOR_CHAIR_HEAD:
            instance.target_unit = None
            instance.target_group = None
            instance.target_user = None
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class RouteStepDirectionAssignmentAdminForm(forms.ModelForm):
    class Meta:
        model = RouteStepDirectionAssignment
        fields = ("direction", "target_unit", "target_group", "target_user")

    class Media:
        js = ("workflow/admin_assignment_filters.js",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        target_unit_value = self.data.get(self.add_prefix("target_unit")) or getattr(
            self.instance, "target_unit_id", None
        )
        groups_queryset = Group.objects.none()
        if target_unit_value:
            groups_queryset = Group.objects.filter(org_units__id=target_unit_value).order_by("name").distinct()
        self.fields["target_group"].queryset = groups_queryset
        self.fields["target_unit"].required = True
        self.fields["target_group"].required = True
        self.fields["target_user"].required = False
        self.fields["target_unit"].label = "Группа"
        self.fields["target_group"].label = "Роль"
        self.fields["target_user"].label = "Проверяющий"

        target_group_value = self.data.get(self.add_prefix("target_group")) or getattr(
            self.instance, "target_group_id", None
        )
        user_queryset = self.fields["target_user"].queryset.none()
        if target_unit_value and target_group_value:
            user_queryset = (
                self.fields["target_user"]
                .queryset.filter(org_unit_id=target_unit_value, groups__id=target_group_value)
                .order_by("last_name", "first_name", "username")
                .distinct()
            )
        self.fields["target_user"].queryset = user_queryset

    def clean(self):
        cleaned_data = super().clean()
        step_template = getattr(self.instance, "step_template", None)
        if step_template is not None and step_template.assignee_kind == AssigneeKind.AUTHOR_CHAIR_HEAD:
            raise forms.ValidationError(
                "Для шага 'Заведующий кафедрой автора' назначения по областям не используются."
            )
        return cleaned_data


class WorkflowStepAdminForm(forms.ModelForm):
    class Meta:
        model = WorkflowStep
        fields = (
            "order",
            "name",
            "assigned_unit",
            "assigned_group",
            "assigned_user",
            "can_reject",
            "can_request_revision",
        )

    class Media:
        js = ("workflow/admin_assignment_filters.js",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        is_chair_head_step = getattr(self.instance, "assignee_kind", None) == AssigneeKind.AUTHOR_CHAIR_HEAD
        assigned_unit_value = self.data.get(self.add_prefix("assigned_unit")) or getattr(
            self.instance, "assigned_unit_id", None
        )
        role_queryset = Group.objects.none()
        if assigned_unit_value:
            role_queryset = Group.objects.filter(org_units__id=assigned_unit_value).order_by("name").distinct()
        elif is_chair_head_step and getattr(self.instance, "assigned_group_id", None):
            role_queryset = Group.objects.filter(pk=self.instance.assigned_group_id)
        self.fields["assigned_group"].queryset = role_queryset
        self.fields["assigned_unit"].required = not is_chair_head_step
        self.fields["assigned_group"].required = True
        self.fields["assigned_user"].required = is_chair_head_step
        self.fields["assigned_unit"].label = "Группа"
        self.fields["assigned_group"].label = "Роль"
        self.fields["assigned_user"].label = "Проверяющий"

        assigned_group_value = self.data.get(self.add_prefix("assigned_group")) or getattr(
            self.instance, "assigned_group_id", None
        )
        user_queryset = self.fields["assigned_user"].queryset.none()
        if assigned_unit_value and assigned_group_value:
            user_queryset = (
                self.fields["assigned_user"]
                .queryset.filter(org_unit_id=assigned_unit_value, groups__id=assigned_group_value)
                .order_by("last_name", "first_name", "username")
                .distinct()
            )
        elif is_chair_head_step and getattr(self.instance, "assigned_user_id", None):
            user_queryset = self.fields["assigned_user"].queryset.filter(pk=self.instance.assigned_user_id)
        self.fields["assigned_user"].queryset = user_queryset

    def clean(self):
        cleaned_data = super().clean()
        is_chair_head_step = getattr(self.instance, "assignee_kind", None) == AssigneeKind.AUTHOR_CHAIR_HEAD
        assigned_unit = cleaned_data.get("assigned_unit")
        assigned_group = cleaned_data.get("assigned_group")
        assigned_user = cleaned_data.get("assigned_user")

        if is_chair_head_step:
            if not assigned_group:
                self.add_error("assigned_group", "Нужно выбрать роль.")
            if not assigned_user:
                self.add_error("assigned_user", "Нужно выбрать проверяющего.")
            if assigned_group and assigned_user and not assigned_user.groups.filter(id=assigned_group.id).exists():
                self.add_error("assigned_user", "У пользователя нет выбранной роли.")
            return cleaned_data

        if not assigned_unit or not assigned_group:
            return cleaned_data

        if not assigned_unit.available_roles.filter(id=assigned_group.id).exists():
            self.add_error("assigned_group", "Эта роль не входит в список ролей выбранной группы.")

        if not assigned_user:
            return cleaned_data

        if assigned_user.org_unit_id != assigned_unit.id:
            self.add_error("assigned_user", "Пользователь должен относиться к выбранной группе.")

        if not assigned_user.groups.filter(id=assigned_group.id).exists():
            self.add_error("assigned_user", "У пользователя нет выбранной роли внутри этой группы.")

        return cleaned_data


class ManualWorkflowStepForm(forms.Form):
    insert_after = forms.ModelChoiceField(
        queryset=WorkflowStep.objects.none(),
        required=False,
        label="Вставить после этапа",
        help_text="Оставьте пустым, если новый этап должен стать первым в индивидуальном маршруте.",
    )
    name = forms.CharField(label="Название этапа", max_length=255)
    assigned_unit = forms.ModelChoiceField(queryset=OrgUnit.objects.none(), label="Группа")
    assigned_group = forms.ModelChoiceField(queryset=Group.objects.none(), label="Роль")
    assigned_user = forms.ModelChoiceField(
        queryset=User.objects.none(),
        label="Проверяющий",
        required=False,
    )
    can_reject = forms.BooleanField(required=False, initial=True, label="Можно отклонить")
    can_request_revision = forms.BooleanField(required=False, initial=True, label="Можно вернуть на доработку")

    class Media:
        js = ("workflow/admin_assignment_filters.js",)

    def __init__(self, *args, workflow_run, **kwargs):
        super().__init__(*args, **kwargs)
        self.workflow_run = workflow_run
        self.fields["assigned_unit"].queryset = OrgUnit.objects.order_by("name")
        self.fields["insert_after"].queryset = workflow_run.steps.order_by("order", "id")
        self.fields["insert_after"].empty_label = "В начало маршрута"

        current_step = workflow_run.current_step
        if current_step is not None:
            self.initial.setdefault("insert_after", current_step.pk)
        elif workflow_run.steps.exists():
            self.initial.setdefault("insert_after", workflow_run.steps.order_by("order", "id").last().pk)

        assigned_unit_value = self.data.get("assigned_unit") or self.initial.get("assigned_unit")
        role_queryset = Group.objects.none()
        if assigned_unit_value:
            role_queryset = Group.objects.filter(org_units__id=assigned_unit_value).order_by("name").distinct()
        self.fields["assigned_group"].queryset = role_queryset

        assigned_group_value = self.data.get("assigned_group") or self.initial.get("assigned_group")
        user_queryset = User.objects.none()
        if assigned_unit_value and assigned_group_value:
            user_queryset = (
                User.objects.filter(org_unit_id=assigned_unit_value, groups__id=assigned_group_value)
                .order_by("last_name", "first_name", "username")
                .distinct()
            )
        self.fields["assigned_user"].queryset = user_queryset

    def clean(self):
        cleaned_data = super().clean()
        assigned_unit = cleaned_data.get("assigned_unit")
        assigned_group = cleaned_data.get("assigned_group")
        assigned_user = cleaned_data.get("assigned_user")
        insert_after = cleaned_data.get("insert_after")

        if insert_after and insert_after.workflow_run_id != self.workflow_run.id:
            self.add_error("insert_after", "Этап вставки должен относиться к этому маршруту.")

        if not assigned_unit or not assigned_group:
            return cleaned_data

        if not assigned_unit.available_roles.filter(id=assigned_group.id).exists():
            self.add_error("assigned_group", "Эта роль не входит в список ролей выбранной группы.")

        if not assigned_user:
            return cleaned_data

        if assigned_user.org_unit_id != assigned_unit.id:
            self.add_error("assigned_user", "Пользователь должен относиться к выбранной группе.")

        if not assigned_user.groups.filter(id=assigned_group.id).exists():
            self.add_error("assigned_user", "У пользователя нет выбранной роли внутри этой группы.")

        return cleaned_data


class RouteStepTemplateInline(admin.TabularInline):
    model = RouteStepTemplate
    form = RouteStepTemplateAdminForm
    extra = 0
    fields = (
        "order",
        "name",
        "assignee_kind",
        "target_unit",
        "target_group",
        "target_user",
        "can_reject",
        "can_request_revision",
    )


class RouteStepDirectionAssignmentInline(admin.TabularInline):
    model = RouteStepDirectionAssignment
    form = RouteStepDirectionAssignmentAdminForm
    extra = 0
    fields = ("direction", "target_unit", "target_group", "target_user")


class WorkflowStepInline(admin.TabularInline):
    model = WorkflowStep
    form = WorkflowStepAdminForm
    extra = 0
    can_delete = False
    show_change_link = True
    fields = (
        "order",
        "name",
        "assigned_unit",
        "assigned_group",
        "assigned_user",
        "status",
        "can_reject",
        "can_request_revision",
    )
    readonly_fields = ("status",)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(RouteTemplate)
class RouteTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "article_type", "priority", "revision_strategy", "is_active", "direction")
    list_filter = ("is_active", "revision_strategy", "article_type", "direction")
    search_fields = ("name",)
    autocomplete_fields = ("article_type",)
    fields = ("name", "article_type", "priority", "revision_strategy", "is_active")
    inlines = (RouteStepTemplateInline,)


@admin.register(RouteStepTemplate)
class RouteStepTemplateAdmin(HiddenFromAdminIndexMixin, admin.ModelAdmin):
    form = RouteStepTemplateAdminForm
    list_display = ("route_template", "order", "name", "assignee_kind", "target_unit", "target_group", "target_user")
    list_filter = ("assignee_kind", "target_unit", "target_group", "can_reject", "can_request_revision")
    search_fields = ("name", "route_template__name")
    autocomplete_fields = ("route_template",)
    fields = (
        "route_template",
        "order",
        "name",
        "assignee_kind",
        "target_unit",
        "target_group",
        "target_user",
        "can_reject",
        "can_request_revision",
    )
    inlines = (RouteStepDirectionAssignmentInline,)


@admin.register(RouteStepDirectionAssignment)
class RouteStepDirectionAssignmentAdmin(HiddenFromAdminIndexMixin, admin.ModelAdmin):
    form = RouteStepDirectionAssignmentAdminForm
    list_display = ("step_template", "direction", "target_unit", "target_group", "target_user")
    list_filter = ("direction", "target_unit", "target_group")
    search_fields = ("step_template__name", "step_template__route_template__name", "direction__name")
    autocomplete_fields = ("step_template", "direction")
    fields = ("step_template", "direction", "target_unit", "target_group", "target_user")


@admin.register(WorkflowRun)
class WorkflowRunAdmin(HiddenFromAdminIndexMixin, admin.ModelAdmin):
    list_display = (
        "id",
        "submission",
        "route_template",
        "status",
        "awaiting_route_approval",
        "current_step",
        "started_at",
        "finished_at",
    )
    list_filter = ("status", "route_template")
    search_fields = ("submission__title", "route_template__name")
    autocomplete_fields = ("submission", "route_template", "current_step")
    readonly_fields = (
        "submission",
        "route_template",
        "status",
        "awaiting_route_approval",
        "current_step",
        "started_at",
        "finished_at",
        "created_at",
        "add_manual_step_link",
    )
    fields = (
        "submission",
        "route_template",
        "status",
        "awaiting_route_approval",
        "current_step",
        "started_at",
        "finished_at",
        "created_at",
        "add_manual_step_link",
    )
    inlines = (WorkflowStepInline,)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        custom_urls = [
            path(
                "<path:object_id>/add-step/",
                self.admin_site.admin_view(self.add_step_view),
                name="workflow_workflowrun_add_step",
            ),
        ]
        return custom_urls + super().get_urls()

    def add_manual_step_link(self, obj):
        if obj is None or obj.pk is None:
            return "Сначала сохраните запуск маршрута."
        url = reverse("admin:workflow_workflowrun_add_step", args=[obj.pk])
        return format_html('<a class="button" href="{}">Добавить индивидуальный этап</a>', url)

    add_manual_step_link.short_description = "Индивидуальная настройка"

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for instance in instances:
            if instance.pk is None and instance.step_template_id is None:
                continue
            instance.assignee_kind = (
                instance.step_template.assignee_kind
                if instance.step_template_id is not None
                else AssigneeKind.FIXED_UNIT_GROUP
            )
            instance.save()
            _sync_step_open_tasks(instance)
        formset.save_m2m()

    def add_step_view(self, request, object_id):
        workflow_run = get_object_or_404(
            WorkflowRun.objects.select_related("submission", "route_template", "current_step"),
            pk=object_id,
        )
        if not self.has_change_permission(request, workflow_run):
            raise PermissionDenied

        form = ManualWorkflowStepForm(request.POST or None, workflow_run=workflow_run)
        if request.method == "POST" and form.is_valid():
            try:
                insert_manual_step(
                    workflow_run,
                    name=form.cleaned_data["name"],
                    assigned_unit=form.cleaned_data["assigned_unit"],
                    assigned_group=form.cleaned_data["assigned_group"],
                    assigned_user=form.cleaned_data["assigned_user"],
                    insert_after_step=form.cleaned_data["insert_after"],
                    can_reject=form.cleaned_data["can_reject"],
                    can_request_revision=form.cleaned_data["can_request_revision"],
                )
            except ValueError as exc:
                form.add_error(None, str(exc))
            else:
                self.message_user(request, "Индивидуальный этап добавлен в маршрут заявки.", messages.SUCCESS)
                return redirect(reverse("admin:workflow_workflowrun_change", args=[workflow_run.pk]))

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "original": workflow_run,
            "workflow_run": workflow_run,
            "title": "Добавить индивидуальный этап",
            "form": form,
            "media": self.media + form.media,
        }
        return TemplateResponse(request, "admin/workflow/workflowrun/add_step.html", context)


@admin.register(WorkflowStep)
class WorkflowStepAdmin(HiddenFromAdminIndexMixin, admin.ModelAdmin):
    form = WorkflowStepAdminForm
    list_display = ("id", "workflow_run", "order", "name", "status", "assigned_user", "assigned_group", "assigned_unit")
    list_filter = ("status", "assignee_kind")
    search_fields = ("name", "workflow_run__submission__title")
    autocomplete_fields = ("workflow_run", "step_template", "assigned_user", "assigned_group", "assigned_unit")
    readonly_fields = ("workflow_run", "step_template", "status", "started_at", "finished_at", "created_at")
    fields = (
        "workflow_run",
        "step_template",
        "order",
        "name",
        "assigned_unit",
        "assigned_group",
        "assigned_user",
        "status",
        "can_reject",
        "can_request_revision",
        "started_at",
        "finished_at",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj.assignee_kind = (
            obj.step_template.assignee_kind
            if obj.step_template_id is not None
            else AssigneeKind.FIXED_UNIT_GROUP
        )
        super().save_model(request, obj, form, change)
        _sync_step_open_tasks(obj)


@admin.register(ApprovalTask)
class ApprovalTaskAdmin(HiddenFromAdminIndexMixin, ReadOnlyAdmin):
    list_display = ("id", "workflow_step", "status", "assigned_user", "assigned_group", "assigned_unit", "activated_at", "decided_at")
    list_filter = ("status",)
    search_fields = ("workflow_step__workflow_run__submission__title",)
    autocomplete_fields = ("workflow_step", "assigned_user", "assigned_group", "assigned_unit")


@admin.register(TaskDecision)
class TaskDecisionAdmin(HiddenFromAdminIndexMixin, ReadOnlyAdmin):
    list_display = ("id", "task", "actor", "decision", "created_at")
    list_filter = ("decision",)
    search_fields = ("task__workflow_step__workflow_run__submission__title", "actor__username")
    autocomplete_fields = ("task", "actor")
