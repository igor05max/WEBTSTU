from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.activities.plan_import import extract_plan_activities, sync_plan_activities


class Command(BaseCommand):
    help = "Загружает планируемые результаты из индивидуальных Excel-планов."

    def add_arguments(self, parser):
        parser.add_argument("source_root", nargs="?", default=settings.PLANNING_ROSTER_SOURCE_ROOT)
        parser.add_argument("--academic-year", default=settings.PLANNING_ROSTER_ACADEMIC_YEAR)
        parser.add_argument(
            "--prune",
            action="store_true",
            help="Удалить только ранее импортированные записи, которых больше нет в планах.",
        )

    def handle(self, *args, **options):
        source_root = Path(options["source_root"])
        if not source_root.is_dir():
            raise CommandError(f"Папка с планами не найдена: {source_root}")
        records, extraction_errors = extract_plan_activities(source_root)
        if extraction_errors:
            raise CommandError("Не удалось разобрать планы:\n" + "\n".join(extraction_errors))
        if not records:
            raise CommandError("В индивидуальных планах не найдены заполненные результаты.")
        stats = sync_plan_activities(records, options["academic_year"], prune=options["prune"])
        if stats["unmatched"]:
            lines = [
                f"{item['department_code']}: {item['full_name']} ({item['source_file']})"
                for item in stats["unmatched"]
            ]
            raise CommandError("Не удалось сопоставить результаты с сотрудниками:\n" + "\n".join(lines))
        self.stdout.write(
            self.style.SUCCESS(
                "Результаты из планов синхронизированы: "
                f"найдено {stats['records']}, создано {stats['created']}, "
                f"обновлено {stats['updated']}, удалено {stats['deleted']}."
            )
        )
