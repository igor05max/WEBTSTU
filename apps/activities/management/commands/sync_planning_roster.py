from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.activities.roster import extract_roster_people, sync_planning_roster


class Command(BaseCommand):
    help = "Загружает состав преподавателей из индивидуальных Excel-планов."

    def add_arguments(self, parser):
        parser.add_argument("source_root", nargs="?", default=settings.PLANNING_ROSTER_SOURCE_ROOT)
        parser.add_argument("--academic-year", default=settings.PLANNING_ROSTER_ACADEMIC_YEAR)
        parser.add_argument(
            "--create-missing",
            action="store_true",
            help="Создать учетную карточку, если преподавателя из плана нет в базе.",
        )

    def handle(self, *args, **options):
        source_root = Path(options["source_root"])
        if not source_root.is_dir():
            raise CommandError(f"Папка с планами не найдена: {source_root}")
        records, extraction_errors = extract_roster_people(source_root)
        if extraction_errors:
            raise CommandError("Не удалось разобрать планы:\n" + "\n".join(extraction_errors))
        if not records:
            raise CommandError("В папке не найдены индивидуальные планы преподавателей.")
        stats = sync_planning_roster(
            records,
            options["academic_year"],
            create_missing=options["create_missing"],
        )
        if stats["unresolved"]:
            lines = [f"{item['department_code']}: {item['full_name']}" for item in stats["unresolved"]]
            raise CommandError("Не удалось однозначно сопоставить сотрудников:\n" + "\n".join(lines))
        self.stdout.write(
            self.style.SUCCESS(
                "Состав преподавателей синхронизирован: "
                f"создано записей {stats['created']}, обновлено {stats['updated']}, "
                f"удалено {stats['deleted']}, создано пользователей {stats['users_created']}."
            )
        )
