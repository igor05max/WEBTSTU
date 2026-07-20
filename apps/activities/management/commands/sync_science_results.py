from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.activities.science_import import extract_scientific_results, sync_scientific_results


class Command(BaseCommand):
    help = "Загружает подтверждённые фактические результаты из выгрузки science."

    def add_arguments(self, parser):
        parser.add_argument("source", nargs="?", default=Path(settings.BASE_DIR) / "science.txt")
        parser.add_argument("--academic-year", default="2025/2026")
        parser.add_argument("--years", nargs="+", default=("2025", "2026"))
        parser.add_argument(
            "--prune",
            action="store_true",
            help="Удалить ранее импортированные факты выбранного учебного года, которых больше нет в выгрузке.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Только проверить выгрузку и показать количество подтверждённых записей.",
        )

    def handle(self, *args, **options):
        source = Path(options["source"])
        if not source.is_file():
            raise CommandError(f"Файл science не найден: {source}")

        records, errors = extract_scientific_results(source, years=options["years"])
        if errors:
            preview = "\n".join(errors[:20])
            suffix = f"\n... ещё ошибок: {len(errors) - 20}" if len(errors) > 20 else ""
            raise CommandError(f"Подтверждённые записи содержат ошибки:\n{preview}{suffix}")
        if not records:
            raise CommandError("В выгрузке нет подтверждённых результатов за выбранные годы.")

        if options["dry_run"]:
            authors = len({record.external_author_id for record in records})
            self.stdout.write(
                self.style.SUCCESS(
                    f"Проверка пройдена: подтверждённых результатов {len(records)}, авторов {authors}. "
                    "База данных не изменена."
                )
            )
            return

        stats = sync_scientific_results(
            records,
            options["academic_year"],
            prune=options["prune"],
        )
        self.stdout.write(
            self.style.SUCCESS(
                "Фактические результаты синхронизированы: "
                f"найдено {stats['records']}, создано {stats['created']}, "
                f"обновлено {stats['updated']}, удалено {stats['deleted']}, "
                f"связано с планом {stats['linked']}, вне плана {stats['unplanned']}."
            )
        )
        self.stdout.write(
            "Пункты плана: "
            f"выполнено {stats['completed_plan_items']}, "
            f"в работе {stats['in_progress_plan_items']}, "
            f"запланировано {stats['planned_plan_items']}."
        )
        if stats["unmatched"]:
            self.stdout.write(self.style.WARNING("Не найдены сотрудники по кадровому ID:"))
            for item in stats["unmatched"]:
                self.stdout.write(
                    f"  ID сотрудника {item['external_author_id']}, результат {item['source_id']}: {item['title']}"
                )
