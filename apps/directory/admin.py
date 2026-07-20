from django.contrib import admin

from apps.directory.models import ArticleType, Direction, Journal, OrgUnit, Position
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
