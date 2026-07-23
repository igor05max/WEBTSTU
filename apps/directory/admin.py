from django.contrib import admin

from apps.directory.publication_topics import merge_publication_topics
from apps.directory.models import (
    ArticleType,
    Direction,
    FormattingTemplate,
    Journal,
    OrgUnit,
    Position,
    PublicationTopic,
)
@admin.register(OrgUnit)
class OrgUnitAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "type", "parent", "is_active")
    list_filter = ("type", "is_active")
    search_fields = ("name", "code")
    autocomplete_fields = ("parent",)
    filter_horizontal = ("available_roles",)
    fields = ("name", "code", "type", "parent", "available_roles", "is_active")


@admin.register(Journal)
class JournalAdmin(admin.ModelAdmin):
    list_display = ("name", "issn", "white_list_level", "is_active")
    list_filter = ("white_list_level", "is_active")
    search_fields = ("name", "issn", "search_index")
    fields = ("name", "issn", "white_list_level", "editorial_policy", "is_active")


@admin.register(Position)
class PositionAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(ArticleType)
class ArticleTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "min_word_count", "max_word_count", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "code")


@admin.register(Direction)
class DirectionAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "code", "description")


@admin.register(PublicationTopic)
class PublicationTopicAdmin(admin.ModelAdmin):
    list_display = ("name", "last_used_at", "is_active", "merged_into")
    list_filter = ("is_active",)
    search_fields = ("name", "normalized_name", "search_index")
    autocomplete_fields = ("created_by", "merged_into")
    readonly_fields = ("normalized_name", "search_index", "created_at", "updated_at", "last_used_at")
    actions = ("merge_into_most_recent",)

    @admin.action(description="Объединить выбранные дубли с самой недавно использованной записью")
    def merge_into_most_recent(self, request, queryset):
        topics = list(queryset.filter(merged_into__isnull=True))
        if len(topics) < 2:
            self.message_user(request, "Выберите не менее двух записей.", level="warning")
            return
        target = max(
            topics,
            key=lambda topic: (topic.last_used_at or topic.updated_at, topic.pk),
        )
        for source in topics:
            if source.pk != target.pk:
                merge_publication_topics(source, target)
        self.message_user(
            request,
            f"Записи объединены с «{target.name}». Старые связи и шаблоны сохранены.",
        )


@admin.register(FormattingTemplate)
class FormattingTemplateAdmin(admin.ModelAdmin):
    list_display = (
        "target_name",
        "article_type",
        "version_number",
        "analysis_status",
        "uploaded_by",
        "created_at",
    )
    list_filter = ("analysis_status", "article_type")
    search_fields = ("journal__name", "publication_topic__name", "file")
    autocomplete_fields = ("journal", "publication_topic", "article_type", "uploaded_by")
    readonly_fields = (
        "version_number",
        "analysis_status",
        "analysis_message",
        "source_text",
        "extracted_rules",
        "rule_conflicts",
        "created_at",
    )
