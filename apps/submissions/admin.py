from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from apps.submissions.models import Submission, SubmissionVersion


class HiddenFromAdminIndexMixin:
    def get_model_perms(self, request):
        return {}


class SubmissionVersionInline(admin.TabularInline):
    model = SubmissionVersion
    extra = 0
    autocomplete_fields = ("uploaded_by",)


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "title",
        "author",
        "authors_display",
        "journal",
        "publication_topic",
        "article_type",
        "formatting_template",
        "direction",
        "route_template",
        "status",
        "current_workflow_run_link",
        "submitted_at",
    )
    list_filter = ("status", "journal", "article_type", "direction", "route_template")
    search_fields = (
        "title",
        "author__username",
        "author__first_name",
        "author__last_name",
        "authors__username",
        "authors__first_name",
        "authors__last_name",
    )
    autocomplete_fields = (
        "author",
        "journal",
        "publication_topic",
        "article_type",
        "formatting_template",
        "direction",
        "route_template",
        "current_version",
    )
    filter_horizontal = ("authors",)
    readonly_fields = ("created_at", "updated_at", "submitted_at", "current_workflow_run_link")
    fields = (
        "title",
        "abstract",
        "document_authors",
        "organizations",
        "contact_emails",
        "keywords",
        "author",
        "authors",
        "journal",
        "publication_topic",
        "article_type",
        "formatting_template",
        "formatting_rules_snapshot",
        "formatting_check_requested",
        "direction",
        "route_template",
        "status",
        "current_version",
        "current_workflow_run_link",
        "created_at",
        "updated_at",
        "submitted_at",
    )
    inlines = (SubmissionVersionInline,)

    def authors_display(self, obj):
        return obj.get_authors_display() or "-"

    authors_display.short_description = "Авторы"

    def current_workflow_run_link(self, obj):
        workflow_run = obj.workflow_runs.order_by("-created_at", "-id").first()
        if workflow_run is None:
            return "Маршрут ещё не запускался"
        url = reverse("admin:workflow_workflowrun_change", args=[workflow_run.id])
        return format_html(
            '<a href="{}">Запуск маршрута #{}</a> ({})',
            url,
            workflow_run.id,
            workflow_run.get_status_display(),
        )

    current_workflow_run_link.short_description = "Индивидуальный маршрут заявки"


@admin.register(SubmissionVersion)
class SubmissionVersionAdmin(HiddenFromAdminIndexMixin, admin.ModelAdmin):
    list_display = ("id", "submission", "version_number", "uploaded_by", "created_at")
    search_fields = ("submission__title", "uploaded_by__username")
    autocomplete_fields = ("submission", "uploaded_by")
    readonly_fields = ("created_at",)
