from urllib.parse import urlencode

from django.conf import settings
from django.contrib import admin
from django.contrib.admin.sites import NotRegistered
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import Group
from django.db.models import Count, Q
from django.urls import reverse
from django.utils.html import format_html

from apps.accounts.forms import UserAdminChangeForm, UserAdminCreationForm
from apps.accounts.models import User
from apps.submissions.models import SubmissionStatus


try:
    admin.site.unregister(Group)
except NotRegistered:
    pass


@admin.register(Group)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)
    fields = ("name",)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    form = UserAdminChangeForm
    add_form = UserAdminCreationForm
    list_display = (
        "username",
        "first_name",
        "position",
        "org_unit",
        "chair_name_display",
        "authored_submissions_count_display",
        "approved_authored_submissions_count_display",
        "is_active",
    )
    list_filter = ("is_active", "groups", "org_unit", "chair_org_unit", "position")
    search_fields = ("username", "first_name", "last_name", "email", "external_directory_id")
    autocomplete_fields = ("position", "org_unit", "chair_org_unit")
    filter_horizontal = ("groups",)

    @admin.display(description="Кафедра", ordering="chair_org_unit__name")
    def chair_name_display(self, obj):
        return obj.get_chair_name() or "-"

    @admin.display(description="Материалы", ordering="authored_submissions_count")
    def authored_submissions_count_display(self, obj):
        return getattr(obj, "authored_submissions_count", 0)

    @admin.display(description="Согласовано", ordering="approved_authored_submissions_count")
    def approved_authored_submissions_count_display(self, obj):
        return getattr(obj, "approved_authored_submissions_count", 0)

    @admin.display(description="Материалы автора")
    def authored_submissions_summary(self, obj):
        if obj is None or obj.pk is None:
            return "Сохраните пользователя, чтобы увидеть связанные материалы."

        total_count = obj.authored_submissions.distinct().count()
        sent_count = obj.authored_submissions.exclude(submitted_at__isnull=True).distinct().count()
        approved_count = obj.authored_submissions.filter(status=SubmissionStatus.APPROVED).distinct().count()

        total_url = self._build_submission_changelist_url(obj)
        sent_url = self._build_submission_changelist_url(obj, submitted_at__isnull="False")
        approved_url = self._build_submission_changelist_url(
            obj,
            status__exact=SubmissionStatus.APPROVED,
        )
        return format_html(
            '<a href="{}">Все: {}</a> | <a href="{}">Отправленные: {}</a> | <a href="{}">Согласованные: {}</a>',
            total_url,
            total_count,
            sent_url,
            sent_count,
            approved_url,
            approved_count,
        )

    def _build_submission_changelist_url(self, obj, **extra_filters):
        query = {"authors__id__exact": obj.pk}
        query.update(extra_filters)
        return f'{reverse("admin:submissions_submission_changelist")}?{urlencode(query)}'

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            authored_submissions_count=Count("authored_submissions", distinct=True),
            approved_authored_submissions_count=Count(
                "authored_submissions",
                filter=Q(authored_submissions__status=SubmissionStatus.APPROVED),
                distinct=True,
            ),
        )

    def get_readonly_fields(self, request, obj=None):
        return (*super().get_readonly_fields(request, obj), "authored_submissions_summary")

    def get_fieldsets(self, request, obj=None):
        base_fieldsets = [
            ("Учетная запись", {"fields": ("username", "password")}),
            ("Личные данные", {"fields": ("first_name", "last_name", "email")}),
            ("Рабочие данные", {"fields": ("position", "org_unit", "chair_org_unit", "groups")}),
            ("Внешний справочник", {"fields": ("external_directory_id",)}),
            ("Доступ в систему", {"fields": ("is_active",)}),
            ("Важные даты", {"fields": ("last_login", "date_joined")}),
        ]
        if obj:
            base_fieldsets.insert(
                4,
                ("Материалы", {"fields": ("authored_submissions_summary",)}),
            )
        if obj and obj.username == settings.ROOT_ADMIN_USERNAME:
            base_fieldsets.insert(
                3,
                ("Root-доступ", {"fields": ("is_staff", "is_superuser")}),
            )
        return base_fieldsets

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "description": (
                    "Для обычных пользователей пароль устанавливается автоматически: 1234. "
                    "Root-пользователь создается отдельно."
                ),
                "fields": (
                    "username",
                    "first_name",
                    "last_name",
                    "email",
                    "position",
                    "org_unit",
                    "chair_org_unit",
                    "groups",
                    "is_active",
                ),
            },
        ),
    )

    def save_model(self, request, obj, form, change):
        if obj.username != settings.ROOT_ADMIN_USERNAME:
            obj.is_staff = False
            obj.is_superuser = False
        super().save_model(request, obj, form, change)
