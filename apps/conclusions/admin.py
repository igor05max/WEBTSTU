from django.contrib import admin

from apps.conclusions.models import ConclusionDocument, ConclusionSignature


@admin.register(ConclusionDocument)
class ConclusionDocumentAdmin(admin.ModelAdmin):
    list_display = (
        "registration_number",
        "submission",
        "source_version",
        "document_sha256",
        "created_at",
    )
    search_fields = ("registration_number", "submission__title", "document_sha256")
    readonly_fields = (
        "workflow_run",
        "submission",
        "source_version",
        "registration_number",
        "document_file",
        "document_sha256",
        "created_at",
        "sealed_at",
        "is_sealed",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ConclusionSignature)
class ConclusionSignatureAdmin(admin.ModelAdmin):
    list_display = (
        "document",
        "signer_name",
        "signer_role",
        "submission_version_number",
        "signed_at",
    )
    list_select_related = ("document", "signer", "submission_version")
    search_fields = (
        "document__registration_number",
        "signer_name",
        "document_sha256",
        "submission_version_sha256",
        "event_hash",
    )
    readonly_fields = tuple(
        field.name for field in ConclusionSignature._meta.fields
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
