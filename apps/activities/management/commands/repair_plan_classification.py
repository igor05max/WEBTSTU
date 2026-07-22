from django.conf import settings
from django.core.management.base import BaseCommand

from apps.activities.plan_import import repair_imported_plan_classification


class Command(BaseCommand):
    help = "Исправляет ошибочно продублированные категории в импортированных планах."

    def add_arguments(self, parser):
        parser.add_argument("--academic-year", default=settings.PLANNING_ROSTER_ACADEMIC_YEAR)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        stats = repair_imported_plan_classification(
            options["academic_year"],
            dry_run=options["dry_run"],
        )
        mode = "Проверка" if options["dry_run"] else "Исправление"
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode} классификации: просмотрено {stats['examined']}, "
                f"ошибочных {stats['obsolete']}, удалено {stats['deleted']}; "
                f"исправлено количеств {stats['quantity_changes']}; "
                f"по типам: {stats['deleted_by_type'] or 'нет'}."
            )
        )
