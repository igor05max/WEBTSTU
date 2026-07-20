from django.contrib import admin

from apps.checks.models import CheckDefinition, CheckRun, GeminiConfiguration


class HiddenFromAdminIndexMixin:
    def get_model_perms(self, request):
        return {}


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]


@admin.register(CheckDefinition)
class CheckDefinitionAdmin(HiddenFromAdminIndexMixin, admin.ModelAdmin):
    list_display = ("code", "name", "order", "is_blocking", "backend_code", "is_active")
    list_filter = ("is_active", "is_blocking", "backend_code")
    search_fields = ("code", "name")


@admin.register(CheckRun)
class CheckRunAdmin(HiddenFromAdminIndexMixin, ReadOnlyAdmin):
    list_display = ("id", "submission", "version", "check_definition", "status", "created_at")
    list_filter = ("status", "check_definition")
    search_fields = ("submission__title", "check_definition__code")
    autocomplete_fields = ("submission", "version", "check_definition")


@admin.register(GeminiConfiguration)
class GeminiConfigurationAdmin(HiddenFromAdminIndexMixin, ReadOnlyAdmin):
    list_display = ("model_name", "models_refreshed_at", "last_test_status", "updated_at")
