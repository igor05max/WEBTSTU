from __future__ import annotations

import hashlib
import re
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

from paper_formatter.exceptions import PackageSecurityError, UnsupportedInputError
from paper_formatter.models import PackageAnalysis, PackageEntry


_DOCUMENT_EXTENSIONS = {".docx", ".tex", ".pdf"}
_SAFE_AUXILIARY_EXTENSIONS = {
    ".bib",
    ".bst",
    ".cls",
    ".sty",
    ".cfg",
    ".def",
    ".clo",
    ".png",
    ".jpg",
    ".jpeg",
    ".pdf",
    ".eps",
    ".svg",
    ".emf",
    ".wmf",
    ".txt",
    ".md",
    ".json",
    ".yaml",
    ".yml",
}


class PackageAnalyzer:
    """Определяет состав входа и безопасно распаковывает ZIP-пакеты."""

    def __init__(
        self,
        *,
        max_entries: int = 5000,
        max_uncompressed_bytes: int = 1_000_000_000,
        max_single_file_bytes: int = 250_000_000,
        max_compression_ratio: float = 200.0,
    ) -> None:
        self.max_entries = max_entries
        self.max_uncompressed_bytes = max_uncompressed_bytes
        self.max_single_file_bytes = max_single_file_bytes
        self.max_compression_ratio = max_compression_ratio

    def analyze(self, source: Path) -> PackageAnalysis:
        source = Path(source).resolve()
        if not source.exists():
            raise UnsupportedInputError(f"Вход не найден: {source}")
        if source.is_dir():
            return self._analyze_directory(source)
        if source.suffix.lower() == ".zip":
            return self._analyze_zip(source)
        if not source.is_file():
            raise UnsupportedInputError(f"Вход не является файлом: {source}")
        extension = source.suffix.lower()
        return PackageAnalysis(
            source_path=str(source),
            source_type="file",
            main_document=source.name,
            document_type=extension.lstrip(".") or None,
            entries=[
                PackageEntry(
                    path=source.name,
                    size=source.stat().st_size,
                    extension=extension,
                    sha256=self._sha256(source),
                )
            ],
        )

    def extract(self, source: Path, destination: Path) -> PackageAnalysis:
        source = Path(source).resolve()
        destination = Path(destination).resolve()
        analysis = self.analyze(source)
        if source.suffix.lower() != ".zip":
            raise UnsupportedInputError("Распаковка применима только к ZIP.")
        destination.mkdir(parents=True, exist_ok=True)
        destination_root = destination.resolve()

        with zipfile.ZipFile(source) as archive:
            self._validate_infos(archive.infolist())
            for info in archive.infolist():
                if info.is_dir():
                    continue
                normalized = self._normalized_member(info.filename)
                target = (destination_root / Path(*normalized.parts)).resolve()
                if destination_root != target and destination_root not in target.parents:
                    raise PackageSecurityError(f"Выход за папку распаковки: {info.filename}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as input_file, target.open("wb") as output_file:
                    shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
        return analysis

    def resolve_main_path(self, analysis: PackageAnalysis, root: Path) -> Path | None:
        if not analysis.main_document:
            return None
        return Path(root) / Path(*PurePosixPath(analysis.main_document).parts)

    def _analyze_zip(self, source: Path) -> PackageAnalysis:
        warnings: list[str] = []
        with zipfile.ZipFile(source) as archive:
            infos = archive.infolist()
            self._validate_infos(infos)
            entries = [
                PackageEntry(
                    path=self._normalized_member(info.filename).as_posix(),
                    size=info.file_size,
                    compressed_size=info.compress_size,
                    extension=PurePosixPath(info.filename).suffix.lower(),
                )
                for info in infos
                if not info.is_dir()
            ]
        main = self._select_main([entry.path for entry in entries])
        dependencies, missing = self._latex_dependencies_from_zip(source, entries, main)
        if not main:
            warnings.append("В ZIP не найден главный DOCX, TEX или PDF.")
        if missing:
            warnings.append(f"Не найдено зависимостей LaTeX: {len(missing)}.")
        return PackageAnalysis(
            source_path=str(source),
            source_type="zip",
            main_document=main,
            document_type=PurePosixPath(main).suffix.lower().lstrip(".") if main else None,
            entries=entries,
            dependencies=dependencies,
            missing_dependencies=missing,
            warnings=warnings,
        )

    def _analyze_directory(self, source: Path) -> PackageAnalysis:
        entries: list[PackageEntry] = []
        for path in sorted(item for item in source.rglob("*") if item.is_file()):
            relative = path.relative_to(source).as_posix()
            entries.append(
                PackageEntry(
                    path=relative,
                    size=path.stat().st_size,
                    extension=path.suffix.lower(),
                )
            )
        main = self._select_main([entry.path for entry in entries])
        dependencies, missing = self._latex_dependencies_from_directory(source, entries, main)
        return PackageAnalysis(
            source_path=str(source),
            source_type="directory",
            main_document=main,
            document_type=PurePosixPath(main).suffix.lower().lstrip(".") if main else None,
            entries=entries,
            dependencies=dependencies,
            missing_dependencies=missing,
            warnings=[] if main else ["В папке не найден главный DOCX, TEX или PDF."],
        )

    def _validate_infos(self, infos: list[zipfile.ZipInfo]) -> None:
        if len(infos) > self.max_entries:
            raise PackageSecurityError(
                f"Слишком много файлов в ZIP: {len(infos)} > {self.max_entries}."
            )
        total = 0
        for info in infos:
            normalized = self._normalized_member(info.filename)
            if self._is_symlink(info):
                raise PackageSecurityError(f"Символическая ссылка запрещена: {info.filename}")
            if info.file_size > self.max_single_file_bytes:
                raise PackageSecurityError(f"Слишком большой файл в ZIP: {info.filename}")
            total += info.file_size
            if total > self.max_uncompressed_bytes:
                raise PackageSecurityError("Суммарный размер распаковки превышает лимит.")
            if (
                info.file_size > 1_000_000
                and info.compress_size > 0
                and info.file_size / info.compress_size > self.max_compression_ratio
            ):
                raise PackageSecurityError(
                    f"Подозрительная степень сжатия: {normalized.as_posix()}"
                )

    @staticmethod
    def _normalized_member(name: str) -> PurePosixPath:
        normalized_name = name.replace("\\", "/")
        path = PurePosixPath(normalized_name)
        if (
            not normalized_name
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
            or re.match(r"^[A-Za-z]:", normalized_name)
        ):
            raise PackageSecurityError(f"Небезопасный путь в ZIP: {name}")
        return path

    @staticmethod
    def _is_symlink(info: zipfile.ZipInfo) -> bool:
        mode = info.external_attr >> 16
        return stat.S_ISLNK(mode)

    def _select_main(self, names: list[str]) -> str | None:
        candidates = [
            name for name in names if PurePosixPath(name).suffix.lower() in _DOCUMENT_EXTENSIONS
        ]
        if not candidates:
            return None

        def score(name: str) -> tuple[int, int, int, str]:
            path = PurePosixPath(name)
            stem = path.stem.lower()
            extension = path.suffix.lower()
            primary = 0
            if stem in {"main", "article", "paper", "manuscript", "template"}:
                primary += 30
            if extension == ".tex" and stem == "main":
                primary += 20
            if extension == ".docx":
                primary += 8
            if extension == ".tex":
                primary += 6
            if any(part.lower() in {"build", "out", "output", "generated"} for part in path.parts):
                primary -= 20
            return (-primary, len(path.parts), len(name), name.lower())

        return sorted(candidates, key=score)[0]

    def _latex_dependencies_from_zip(
        self,
        source: Path,
        entries: list[PackageEntry],
        main: str | None,
    ) -> tuple[list[str], list[str]]:
        if not main or PurePosixPath(main).suffix.lower() != ".tex":
            return [], []
        with zipfile.ZipFile(source) as archive:
            raw = archive.read(main).decode("utf-8", errors="replace")
        return self._resolve_dependencies(raw, PurePosixPath(main).parent, entries)

    def _latex_dependencies_from_directory(
        self,
        source: Path,
        entries: list[PackageEntry],
        main: str | None,
    ) -> tuple[list[str], list[str]]:
        if not main or PurePosixPath(main).suffix.lower() != ".tex":
            return [], []
        raw = (source / Path(*PurePosixPath(main).parts)).read_text(
            encoding="utf-8", errors="replace"
        )
        return self._resolve_dependencies(raw, PurePosixPath(main).parent, entries)

    @staticmethod
    def _resolve_dependencies(
        raw: str,
        parent: PurePosixPath,
        entries: list[PackageEntry],
    ) -> tuple[list[str], list[str]]:
        existing = {PurePosixPath(entry.path).as_posix() for entry in entries}
        dependencies: list[str] = []
        patterns = [
            (r"\\(?:input|include)\s*\{([^}]+)\}", [".tex"]),
            (r"\\includegraphics(?:\[[^\]]*\])?\s*\{([^}]+)\}", [".pdf", ".png", ".jpg", ".jpeg", ".eps", ".svg"]),
            (r"\\bibliography\s*\{([^}]+)\}", [".bib"]),
            (r"\\addbibresource(?:\[[^\]]*\])?\s*\{([^}]+)\}", [".bib"]),
        ]
        for pattern, extensions in patterns:
            for match in re.findall(pattern, raw):
                for value in match.split(","):
                    value = value.strip().replace("\\", "/")
                    candidate = parent / value
                    if PurePosixPath(value).suffix:
                        dependencies.append(candidate.as_posix())
                    else:
                        dependencies.extend((parent / f"{value}{ext}").as_posix() for ext in extensions)
        dependencies = list(dict.fromkeys(dependencies))
        missing: list[str] = []
        resolved: list[str] = []
        for dependency in dependencies:
            if dependency in existing:
                resolved.append(dependency)
                continue
            alternatives = [
                item for item in existing if PurePosixPath(item).with_suffix("").as_posix() == PurePosixPath(dependency).with_suffix("").as_posix()
            ]
            if alternatives:
                resolved.append(alternatives[0])
            else:
                missing.append(dependency)
        return list(dict.fromkeys(resolved)), list(dict.fromkeys(missing))

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
