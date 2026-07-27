import json
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.submissions.document_ai import analyze_document
from apps.submissions.document_analysis import SUPPORTED_EXTENSIONS


class Command(BaseCommand):
    help = (
        "Безопасно анализирует папку или ZIP-корпус проблемных документов и создаёт "
        "JSON-отчёт по качеству извлечения."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "archive",
            help="Путь к папке или ZIP с примерами документов.",
        )
        parser.add_argument(
            "--output",
            help="Путь для JSON-отчёта. По умолчанию отчёт выводится в stdout.",
        )
        parser.add_argument(
            "--with-ai",
            action="store_true",
            help="Уточнять неоднозначные поля через настроенную локальную модель.",
        )
        parser.add_argument(
            "--max-files",
            type=int,
            default=500,
            help="Максимальное количество анализируемых документов.",
        )

    def handle(self, *args, **options):
        archive_path = Path(options["archive"]).resolve()
        if not archive_path.exists():
            raise CommandError(f"Корпус не найден: {archive_path}")
        is_directory = archive_path.is_dir()
        if not is_directory and archive_path.suffix.casefold() != ".zip":
            raise CommandError("Корпус должен быть папкой или ZIP-архивом.")

        maximum_file_size = int(
            getattr(settings, "SUBMISSION_FILE_MAX_SIZE", 50 * 1024 * 1024)
        )
        maximum_total_size = 500 * 1024 * 1024
        max_files = max(1, int(options["max_files"]))
        reports = []
        skipped = []
        total_unpacked = 0

        archive = None
        candidates = []
        if is_directory:
            for path in archive_path.rglob("*"):
                resolved_path = path.resolve()
                if (
                    not resolved_path.is_file()
                    or not resolved_path.is_relative_to(archive_path)
                    or resolved_path.suffix.casefold() not in SUPPORTED_EXTENSIONS
                ):
                    continue
                size = resolved_path.stat().st_size
                if size > maximum_file_size:
                    skipped.append(
                        {
                            "file_name": str(resolved_path.relative_to(archive_path)),
                            "reason": "unsafe_or_too_large",
                        }
                    )
                    continue
                total_unpacked += max(0, size)
                if total_unpacked > maximum_total_size:
                    raise CommandError(
                        "Суммарный размер корпуса превышает 500 МБ."
                    )
                candidates.append(
                    {
                        "name": str(resolved_path.relative_to(archive_path)),
                        "source": resolved_path,
                    }
                )
        else:
            try:
                archive = zipfile.ZipFile(archive_path)
            except (OSError, zipfile.BadZipFile) as exc:
                raise CommandError(f"ZIP повреждён или не читается: {exc}") from exc
            for info in archive.infolist():
                member_path = PurePosixPath(info.filename.replace("\\", "/"))
                suffix = member_path.suffix.casefold()
                if info.is_dir() or suffix not in SUPPORTED_EXTENSIONS:
                    continue
                if (
                    member_path.is_absolute()
                    or ".." in member_path.parts
                    or info.file_size > maximum_file_size
                    or info.file_size / max(info.compress_size, 1) > 150
                ):
                    skipped.append(
                        {
                            "file_name": info.filename,
                            "reason": "unsafe_or_too_large",
                        }
                    )
                    continue
                total_unpacked += max(0, info.file_size)
                if total_unpacked > maximum_total_size:
                    raise CommandError(
                        "Суммарный распакованный размер корпуса превышает 500 МБ."
                    )
                candidates.append({"name": info.filename, "source": info})

        try:
            for candidate in candidates[:max_files]:
                try:
                    source = candidate["source"]
                    data = (
                        source.read_bytes()
                        if is_directory
                        else archive.read(source)
                    )
                    snapshot = analyze_document(
                        data,
                        candidate["name"],
                        use_ai=options["with_ai"],
                    )
                except Exception as exc:
                    reports.append(
                        {
                            "file_name": candidate["name"],
                            "status": "failed",
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    continue

                metadata = snapshot.get("metadata") or {}
                article = snapshot.get("article") or {}
                reports.append(
                    {
                        "file_name": candidate["name"],
                        "status": (
                            "parse_error"
                            if snapshot.get("parse_error")
                            else "needs_review"
                            if article.get("needs_review")
                            else "ready"
                        ),
                        "format": snapshot.get("suffix"),
                        "parse_error": snapshot.get("parse_error") or "",
                        "requires_ocr": bool(snapshot.get("requires_ocr")),
                        "metadata": {
                            key: metadata.get(key)
                            for key in (
                                "title",
                                "authors",
                                "organizations",
                                "abstract",
                                "keywords",
                            )
                        },
                        "confidence": metadata.get("confidence") or {},
                        "needs_review": article.get("needs_review") or [],
                        "counts": {
                            "paragraphs": len(snapshot.get("paragraphs") or []),
                            "sections": len(article.get("sections") or []),
                            "tables": len(snapshot.get("tables") or []),
                            "figures": len(snapshot.get("figures") or []),
                            "formulas": len(snapshot.get("formulas") or []),
                            "references": len(article.get("references") or []),
                        },
                        "semantic_refinement": snapshot.get(
                            "semantic_refinement"
                        )
                        or {},
                    }
                )
        finally:
            if archive is not None:
                archive.close()

        statuses = Counter(report["status"] for report in reports)
        result = {
            "schema_version": "1.0",
            "source": archive_path.name,
            "summary": {
                "analyzed": len(reports),
                "ready": statuses["ready"],
                "needs_review": statuses["needs_review"],
                "parse_error": statuses["parse_error"],
                "failed": statuses["failed"],
                "skipped": len(skipped)
                + max(0, len(candidates) - max_files),
            },
            "documents": reports,
            "skipped": skipped,
        }
        serialized = json.dumps(result, ensure_ascii=False, indent=2)
        output_path = options.get("output")
        if output_path:
            target = Path(output_path).resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(serialized, encoding="utf-8")
            self.stdout.write(
                self.style.SUCCESS(
                    f"Проанализировано документов: {len(reports)}. Отчёт: {target}"
                )
            )
        else:
            self.stdout.write(serialized)
