from django.apps import AppConfig


class ChecksConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.checks"
    label = "checks"
    verbose_name = "Автопроверки"
