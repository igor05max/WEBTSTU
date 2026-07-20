from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    label = "accounts"
    verbose_name = "Пользователи"

    def ready(self):
        from django.contrib import admin
        from django.contrib.auth.models import Group

        from apps.accounts.access import is_root_admin
        from apps.accounts.forms import RootAdminAuthenticationForm
        from apps.accounts.models import User

        Group._meta.verbose_name = "Роль"
        Group._meta.verbose_name_plural = "Роли"

        groups_field = User._meta.get_field("groups")
        groups_field.verbose_name = "Роли"
        groups_field.help_text = (
            "Выберите одну или несколько ролей пользователя в процессе согласования. "
            "Именно роли определяют, какие этапы и проверки будут доступны пользователю."
        )

        admin.site.has_permission = lambda request: is_root_admin(request.user)
        admin.site.login_form = RootAdminAuthenticationForm
        admin.site.site_header = "Root-администрирование"
        admin.site.site_title = "Root-администрирование"
        admin.site.index_title = "Управление справочниками и маршрутами"
