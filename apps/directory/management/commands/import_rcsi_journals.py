import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.directory.journal_search import (
    build_journal_search_index,
    normalize_issns,
    normalize_titles,
)
from apps.directory.models import Journal


class Command(BaseCommand):
    help = "Import journals from the RCSI white-list JSON export."

    def add_arguments(self, parser):
        parser.add_argument(
            "json_path",
            nargs="?",
            default=str(settings.BASE_DIR / "journals_rcsi.json"),
            help="Path to journals_rcsi.json.",
        )

    def handle(self, *args, **options):
        json_path = Path(options["json_path"])
        if not json_path.exists():
            raise CommandError(f"File not found: {json_path}")

        with json_path.open("r", encoding="utf-8") as source:
            payload = json.load(source)
        if not isinstance(payload, list):
            raise CommandError("RCSI journal export must be a JSON array.")

        grouped = {}
        skipped = 0
        for item in payload:
            titles = normalize_titles(item.get("title"))
            if not titles:
                skipped += 1
                continue

            name = titles[0]
            issns = normalize_issns(item.get("issns"))
            level = item.get("level_2023")
            try:
                level = int(level) if level not in (None, "") else None
            except (TypeError, ValueError):
                level = None

            entry = grouped.setdefault(
                name,
                {
                    "titles": [],
                    "issns": [],
                    "level": level,
                },
            )
            entry["titles"].extend(titles)
            entry["issns"].extend(issns)
            if level is not None:
                if entry["level"] is None:
                    entry["level"] = level
                else:
                    entry["level"] = min(entry["level"], level)

        existing = {journal.name: journal for journal in Journal.objects.filter(name__in=grouped.keys())}
        to_create = []
        to_update = []
        for name, entry in grouped.items():
            titles = normalize_titles(entry["titles"])
            issns = normalize_issns(entry["issns"])
            defaults = {
                "issn": ", ".join(issns),
                "search_index": build_journal_search_index(titles, issns),
                "white_list_level": entry["level"],
                "is_active": True,
            }

            journal = existing.get(name)
            if journal is None:
                to_create.append(Journal(name=name, **defaults))
                continue

            changed = False
            for field_name, value in defaults.items():
                if getattr(journal, field_name) != value:
                    setattr(journal, field_name, value)
                    changed = True
            if changed:
                to_update.append(journal)

        with transaction.atomic():
            if to_create:
                Journal.objects.bulk_create(to_create, batch_size=1000)
            if to_update:
                Journal.objects.bulk_update(
                    to_update,
                    ["issn", "search_index", "white_list_level", "is_active"],
                    batch_size=1000,
                )

        self.stdout.write(
            self.style.SUCCESS(
                "Imported RCSI journals: "
                f"created={len(to_create)}, updated={len(to_update)}, skipped={skipped}, total={len(grouped)}"
            )
        )
