from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.staff_directory import parse_staff_directory_html, sync_staff_directory_entries


class Command(BaseCommand):
    help = "Импортирует сотрудников и должности из одной или нескольких HTML-страниц со списком работников."

    def add_arguments(self, parser):
        parser.add_argument("html_paths", nargs="+", help="Путь к HTML-файлу со страницей сотрудников.")

    def handle(self, *args, **options):
        entries = []
        for raw_path in options["html_paths"]:
            html_path = Path(raw_path).expanduser()
            if not html_path.exists():
                raise CommandError(f"Файл не найден: {html_path}")
            entries.extend(parse_staff_directory_html(html_path.read_text(encoding="utf-8")))

        stats = sync_staff_directory_entries(entries)

        self.stdout.write(self.style.SUCCESS("Синхронизация завершена."))
        for key, value in stats.items():
            self.stdout.write(f"{key}: {value}")
