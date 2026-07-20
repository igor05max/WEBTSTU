from django.contrib import admin

from apps.activities.models import Activity, ActivityType, GrantType, PlanningRosterEntry, ScientificResult


@admin.register(ActivityType)
class ActivityTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "area", "requires_grant_type", "is_active")
    list_filter = ("area", "requires_grant_type", "is_active")
    search_fields = ("name", "code")


@admin.register(GrantType)
class GrantTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "code")


@admin.register(PlanningRosterEntry)
class PlanningRosterEntryAdmin(admin.ModelAdmin):
    list_display = ("full_name", "department_code", "academic_year", "user", "updated_at")
    list_filter = ("academic_year", "department_code")
    search_fields = ("full_name", "user__username", "user__first_name", "user__last_name")
    autocomplete_fields = ("user",)


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "quantity",
        "activity_type",
        "grant_type",
        "owner",
        "academic_year",
        "period",
        "status",
        "source_file",
        "updated_at",
    )
    list_filter = ("activity_type", "grant_type", "academic_year", "period", "status")
    search_fields = (
        "title",
        "source_text",
        "source_file",
        "owner__username",
        "owner__first_name",
        "owner__last_name",
    )
    autocomplete_fields = ("owner", "activity_type", "grant_type")
    filter_horizontal = ("collaborators",)


@admin.register(ScientificResult)
class ScientificResultAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "owner",
        "activity_type",
        "result_year",
        "planned_activity",
        "external_author_id",
        "source_id",
    )
    list_filter = ("academic_year", "result_year", "activity_type")
    search_fields = (
        "title",
        "publication_name",
        "source_id",
        "external_author_id",
        "owner__username",
        "owner__first_name",
        "owner__last_name",
    )
    autocomplete_fields = ("owner", "activity_type", "planned_activity")
    readonly_fields = ("source_key", "source_file", "source_line", "source_payload", "created_at", "updated_at")
