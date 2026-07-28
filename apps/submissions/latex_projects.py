from __future__ import annotations

import io
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile, ZipInfo

from django.conf import settings
from django.core.files.base import ContentFile


LATEX_ARCHIVE_EXTENSION = ".zip"
LATEX_MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
LATEX_MAX_UNCOMPRESSED_BYTES = 250 * 1024 * 1024
LATEX_MAX_MEMBERS = 5000
LATEX_MAX_SOURCE_BYTES = 4 * 1024 * 1024
LATEX_ALLOWED_EXTENSIONS = {
    ".bbx",
    ".bib",
    ".bst",
    ".cbx",
    ".cfg",
    ".clo",
    ".cls",
    ".csv",
    ".dat",
    ".def",
    ".enc",
    ".eps",
    ".fd",
    ".jpeg",
    ".jpg",
    ".json",
    ".lbx",
    ".map",
    ".pdf",
    ".png",
    ".sty",
    ".svg",
    ".tex",
    ".tsv",
    ".txt",
}
LATEX_IMAGE_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg", ".eps", ".svg")
LATEX_MAIN_NAMES = ("article.tex", "main.tex", "manuscript.tex", "paper.tex")

_DOCUMENT_RE = re.compile(r"\\begin\s*\{\s*document\s*\}", re.IGNORECASE)
_DOCUMENT_CLASS_RE = re.compile(
    r"\\documentclass(?:\s*\[[^\]]*\])?\s*\{\s*([^}]+?)\s*\}",
    re.IGNORECASE,
)
_DEPENDENCY_RE = re.compile(
    r"\\(?P<command>includegraphics|input|include|bibliography|addbibresource)"
    r"(?:\s*\[[^\]]*\])?\s*\{\s*(?P<value>[^}]+?)\s*\}",
    re.IGNORECASE,
)
_DANGEROUS_COMMAND_RE = re.compile(
    r"\\(?:write18|openin|openout|read|usepackage\s*\{\s*shellesc|immediate\s*\\write)",
    re.IGNORECASE,
)


class LatexProjectError(ValueError):
    pass


@dataclass(frozen=True)
class PreparedLatexUpload:
    main_file: ContentFile
    archive_file: ContentFile | None
    main_path: str
    manifest: dict


def _strip_tex_comments(source: str) -> str:
    lines = []
    for line in source.splitlines():
        output = []
        escaped = False
        for character in line:
            if character == "%" and not escaped:
                break
            output.append(character)
            if character == "\\":
                escaped = not escaped
            else:
                escaped = False
        lines.append("".join(output))
    return "\n".join(lines)


def decode_latex_source(data: bytes) -> str:
    if len(data) > LATEX_MAX_SOURCE_BYTES:
        raise LatexProjectError("Главный TEX-файл превышает ограничение 4 МБ.")
    for encoding in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def encode_latex_source(source: str) -> bytes:
    payload = source.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    if len(payload) > LATEX_MAX_SOURCE_BYTES:
        raise LatexProjectError("Главный TEX-файл превышает ограничение 4 МБ.")
    return payload


def _safe_member_path(name: str) -> str:
    normalized = name.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or normalized.endswith("/"):
        return ""
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise LatexProjectError(f"Недопустимый путь внутри архива: {name}")
    if ":" in path.parts[0] or "\x00" in normalized:
        raise LatexProjectError(f"Недопустимый путь внутри архива: {name}")
    return path.as_posix()


def _is_symlink(info: ZipInfo) -> bool:
    return ((info.external_attr >> 16) & 0o170000) == 0o120000


def _validated_members(archive: ZipFile) -> list[tuple[ZipInfo, str]]:
    infos = archive.infolist()
    if len(infos) > LATEX_MAX_MEMBERS:
        raise LatexProjectError("В LaTeX-проекте слишком много файлов.")
    if sum(max(0, info.file_size) for info in infos) > LATEX_MAX_UNCOMPRESSED_BYTES:
        raise LatexProjectError("Распакованный LaTeX-проект превышает 250 МБ.")

    members = []
    casefold_paths = {}
    for info in infos:
        path = _safe_member_path(info.filename)
        if not path:
            continue
        if info.flag_bits & 0x1:
            raise LatexProjectError("Зашифрованные ZIP-архивы не поддерживаются.")
        if _is_symlink(info):
            raise LatexProjectError("Символические ссылки внутри ZIP запрещены.")
        suffix = PurePosixPath(path).suffix.casefold()
        if suffix not in LATEX_ALLOWED_EXTENSIONS:
            raise LatexProjectError(
                f"Файл «{path}» имеет неподдерживаемый тип внутри LaTeX-проекта."
            )
        folded = path.casefold()
        previous = casefold_paths.get(folded)
        if previous is not None and previous != path:
            raise LatexProjectError(
                f"Архив содержит пути, различающиеся только регистром: «{previous}» и «{path}»."
            )
        casefold_paths[folded] = path
        members.append((info, path))
    if not members:
        raise LatexProjectError("ZIP-архив не содержит файлов LaTeX-проекта.")
    return members


def _main_candidate_score(path: str, source: str) -> tuple:
    lowered_name = PurePosixPath(path).name.casefold()
    try:
        preferred_index = LATEX_MAIN_NAMES.index(lowered_name)
    except ValueError:
        preferred_index = len(LATEX_MAIN_NAMES)
    return (
        0 if _DOCUMENT_CLASS_RE.search(source) and _DOCUMENT_RE.search(source) else 1,
        preferred_index,
        len(PurePosixPath(path).parts),
        len(path),
        path.casefold(),
    )


def _choose_main_tex(files: dict[str, bytes], requested_path: str = "") -> tuple[str, str]:
    tex_paths = sorted(path for path in files if path.casefold().endswith(".tex"))
    if not tex_paths:
        raise LatexProjectError("В ZIP-архиве нет TEX-файлов.")
    if requested_path:
        requested = requested_path.replace("\\", "/").lstrip("./")
        exact = next((path for path in tex_paths if path == requested), None)
        if exact is None:
            folded_matches = [path for path in tex_paths if path.casefold() == requested.casefold()]
            if len(folded_matches) == 1:
                exact = folded_matches[0]
        if exact is None:
            raise LatexProjectError("Указанный главный TEX-файл не найден в архиве.")
        source = decode_latex_source(files[exact])
        if not _DOCUMENT_RE.search(_strip_tex_comments(source)):
            raise LatexProjectError("В выбранном TEX-файле нет окружения document.")
        return exact, source

    candidates = []
    for path in tex_paths:
        source = decode_latex_source(files[path])
        stripped = _strip_tex_comments(source)
        candidates.append((_main_candidate_score(path, stripped), path, source))
    _score, path, source = min(candidates, key=lambda item: item[0])
    if not _DOCUMENT_RE.search(_strip_tex_comments(source)):
        raise LatexProjectError(
            "Не удалось автоматически определить главный TEX-файл: нет окружения document."
        )
    return path, source


def _dependency_target(command: str, value: str) -> tuple[str, tuple[str, ...]]:
    clean = value.strip().replace("\\", "/")
    if command.casefold() == "includegraphics":
        return clean, LATEX_IMAGE_EXTENSIONS if not PurePosixPath(clean).suffix else ("",)
    if command.casefold() in {"input", "include"}:
        return clean, (".tex",) if not PurePosixPath(clean).suffix else ("",)
    if command.casefold() in {"bibliography", "addbibresource"}:
        return clean, (".bib",) if not PurePosixPath(clean).suffix else ("",)
    return clean, ("",)


def _resolve_dependency(
    *,
    main_path: str,
    command: str,
    value: str,
    files: dict[str, bytes],
) -> tuple[str | None, str]:
    target, extensions = _dependency_target(command, value)
    main_directory = posixpath.dirname(main_path)
    candidates = []
    for extension in extensions:
        candidate = target if extension == "" else f"{target}{extension}"
        candidates.append(posixpath.normpath(posixpath.join(main_directory, candidate)))
    for candidate in candidates:
        if candidate in files:
            return candidate, "exact"
    folded = {path.casefold(): path for path in files}
    for candidate in candidates:
        actual = folded.get(candidate.casefold())
        if actual:
            return actual, "case_mismatch"
    return None, "missing"


def _rewrite_case_mismatches(
    source: str,
    *,
    main_path: str,
    files: dict[str, bytes],
) -> tuple[str, list[dict], list[str]]:
    stripped = _strip_tex_comments(source)
    dependencies = []
    warnings = []
    replacements = {}
    for match in _DEPENDENCY_RE.finditer(stripped):
        command = match.group("command")
        value = match.group("value").strip()
        actual, status = _resolve_dependency(
            main_path=main_path,
            command=command,
            value=value,
            files=files,
        )
        dependency = {
            "command": command,
            "requested": value,
            "resolved": actual or "",
            "status": status,
        }
        dependencies.append(dependency)
        if status == "case_mismatch" and actual:
            relative = posixpath.relpath(actual, posixpath.dirname(main_path) or ".")
            replacements[(command.casefold(), value)] = relative
            warnings.append(
                f"Исправлен регистр пути «{value}» → «{relative}» для Linux-сервера."
            )
        elif status == "missing":
            warnings.append(f"Не найден дополнительный файл «{value}» ({command}).")

    if not replacements:
        return source, dependencies, warnings

    def replace(match: re.Match) -> str:
        key = (match.group("command").casefold(), match.group("value").strip())
        replacement = replacements.get(key)
        if not replacement:
            return match.group(0)
        raw = match.group(0)
        start, end = match.span("value")
        relative_start = start - match.start()
        relative_end = end - match.start()
        return f"{raw[:relative_start]}{replacement}{raw[relative_end:]}"

    return _DEPENDENCY_RE.sub(replace, source), dependencies, warnings


def _document_class_dependency(
    source: str,
    *,
    main_path: str,
    files: dict[str, bytes],
) -> list[dict]:
    match = _DOCUMENT_CLASS_RE.search(_strip_tex_comments(source))
    if match is None:
        return []
    requested = match.group(1).strip()
    if "/" not in requested and "\\" not in requested:
        return [
            {
                "command": "documentclass",
                "requested": requested,
                "resolved": "",
                "status": "system",
            }
        ]
    candidate = requested if requested.casefold().endswith(".cls") else f"{requested}.cls"
    actual, status = _resolve_dependency(
        main_path=main_path,
        command="input",
        value=candidate,
        files=files,
    )
    return [
        {
            "command": "documentclass",
            "requested": requested,
            "resolved": actual or "",
            "status": status,
        }
    ]


def prepare_latex_archive(
    data: bytes,
    *,
    filename: str,
    requested_main_path: str = "",
) -> PreparedLatexUpload:
    if len(data) > LATEX_MAX_ARCHIVE_BYTES:
        raise LatexProjectError("ZIP-архив превышает ограничение 100 МБ.")
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            members = _validated_members(archive)
            files = {path: archive.read(info) for info, path in members}
    except BadZipFile as exc:
        raise LatexProjectError("Файл не является корректным ZIP-архивом.") from exc

    main_path, source = _choose_main_tex(files, requested_main_path)
    corrected_source, dependencies, warnings = _rewrite_case_mismatches(
        source,
        main_path=main_path,
        files=files,
    )
    class_dependencies = _document_class_dependency(
        corrected_source,
        main_path=main_path,
        files=files,
    )
    dependencies = [*class_dependencies, *dependencies]
    if _DANGEROUS_COMMAND_RE.search(_strip_tex_comments(corrected_source)):
        warnings.append(
            "Найдены потенциально опасные команды; серверный просмотр будет отключён."
        )

    corrected_bytes = encode_latex_source(corrected_source)
    files[main_path] = corrected_bytes
    output = io.BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(files):
            archive.writestr(path, files[path])

    tex_files = [path for path in sorted(files) if path.casefold().endswith(".tex")]
    asset_files = [
        path
        for path in sorted(files)
        if PurePosixPath(path).suffix.casefold() in LATEX_IMAGE_EXTENSIONS
    ]
    manifest = {
        "kind": "latex_project",
        "archive_name": Path(filename).name,
        "main_path": main_path,
        "file_count": len(files),
        "total_uncompressed_bytes": sum(len(value) for value in files.values()),
        "tex_files": tex_files,
        "asset_files": asset_files,
        "dependencies": dependencies,
        "warnings": warnings,
        "safe_to_compile": not bool(_DANGEROUS_COMMAND_RE.search(_strip_tex_comments(corrected_source))),
    }
    return PreparedLatexUpload(
        main_file=ContentFile(corrected_bytes, name=PurePosixPath(main_path).name),
        archive_file=ContentFile(output.getvalue(), name=Path(filename).name),
        main_path=main_path,
        manifest=manifest,
    )


def prepare_single_latex(data: bytes, *, filename: str) -> PreparedLatexUpload:
    source = decode_latex_source(data)
    stripped = _strip_tex_comments(source)
    if not _DOCUMENT_RE.search(stripped):
        raise LatexProjectError("В TEX-файле не найдено окружение document.")
    warnings = []
    if _DANGEROUS_COMMAND_RE.search(stripped):
        warnings.append(
            "Найдены потенциально опасные команды; серверный просмотр будет отключён."
        )
    main_name = Path(filename).name
    manifest = {
        "kind": "latex_source",
        "archive_name": "",
        "main_path": main_name,
        "file_count": 1,
        "total_uncompressed_bytes": len(data),
        "tex_files": [main_name],
        "asset_files": [],
        "dependencies": [],
        "warnings": warnings,
        "safe_to_compile": not bool(_DANGEROUS_COMMAND_RE.search(stripped)),
    }
    return PreparedLatexUpload(
        main_file=ContentFile(encode_latex_source(source), name=main_name),
        archive_file=None,
        main_path=main_name,
        manifest=manifest,
    )


def prepare_material_upload(uploaded_file, *, requested_main_path: str = ""):
    suffix = Path(uploaded_file.name).suffix.casefold()
    if suffix not in {".tex", LATEX_ARCHIVE_EXTENSION}:
        return None
    data = uploaded_file.read()
    if suffix == LATEX_ARCHIVE_EXTENSION:
        return prepare_latex_archive(
            data,
            filename=uploaded_file.name,
            requested_main_path=requested_main_path,
        )
    return prepare_single_latex(data, filename=uploaded_file.name)


def replace_project_main_source(version, source: str) -> PreparedLatexUpload:
    source_bytes = encode_latex_source(source)
    main_path = str(version.project_main_path or Path(version.file.name).name)
    if version.project_archive:
        with version.project_archive.open("rb") as source_archive:
            archive_bytes = source_archive.read()
        try:
            with ZipFile(io.BytesIO(archive_bytes)) as archive:
                members = _validated_members(archive)
                files = {path: archive.read(info) for info, path in members}
        except BadZipFile as exc:
            raise LatexProjectError("Архив текущей версии повреждён.") from exc
        if main_path not in files:
            raise LatexProjectError("Главный TEX-файл не найден в архиве текущей версии.")
        files[main_path] = source_bytes
        output = io.BytesIO()
        with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(files):
                archive.writestr(path, files[path])
        manifest = dict(version.project_manifest or {})
        manifest["total_uncompressed_bytes"] = sum(len(value) for value in files.values())
        manifest["warnings"] = [
            warning
            for warning in manifest.get("warnings", [])
            if "потенциально опасные команды" not in warning
        ]
        safe_to_compile = not bool(_DANGEROUS_COMMAND_RE.search(_strip_tex_comments(source)))
        manifest["safe_to_compile"] = safe_to_compile
        if not safe_to_compile:
            manifest["warnings"].append(
                "Найдены потенциально опасные команды; серверный просмотр будет отключён."
            )
        return PreparedLatexUpload(
            main_file=ContentFile(source_bytes, name=PurePosixPath(main_path).name),
            archive_file=ContentFile(
                output.getvalue(),
                name=Path(version.project_archive.name).name,
            ),
            main_path=main_path,
            manifest=manifest,
        )
    return prepare_single_latex(source_bytes, filename=PurePosixPath(main_path).name)


def read_version_latex_source(version) -> str:
    with version.file.open("rb") as source:
        return decode_latex_source(source.read())


def _write_project_tree(version, destination: Path) -> Path:
    main_path = str(version.project_main_path or Path(version.file.name).name)
    if version.project_archive:
        with version.project_archive.open("rb") as source_archive:
            archive_bytes = source_archive.read()
        try:
            with ZipFile(io.BytesIO(archive_bytes)) as archive:
                members = _validated_members(archive)
                for info, relative_path in members:
                    target = destination.joinpath(*PurePosixPath(relative_path).parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(info))
        except BadZipFile as exc:
            raise LatexProjectError("Архив LaTeX-проекта повреждён.") from exc
    source_text = read_version_latex_source(version)
    main_target = destination.joinpath(*PurePosixPath(main_path).parts)
    main_target.parent.mkdir(parents=True, exist_ok=True)
    main_target.write_bytes(encode_latex_source(source_text))
    return main_target


def _latex_executable() -> tuple[str, bool] | None:
    configured = str(getattr(settings, "LATEXMK_BINARY", "") or "").strip()
    latexmk = configured or shutil.which("latexmk")
    if latexmk:
        return latexmk, True
    configured_pdf = str(getattr(settings, "PDFLATEX_BINARY", "") or "").strip()
    pdflatex = configured_pdf or shutil.which("pdflatex")
    if pdflatex:
        return pdflatex, False
    return None


def build_latex_project_pdf(version, *, force: bool = False) -> Path:
    if Path(version.file.name).suffix.casefold() != ".tex":
        raise LatexProjectError("Эта версия не является LaTeX-статьёй.")
    if version.rendered_pdf and not force:
        try:
            path = Path(version.rendered_pdf.path)
            if path.exists() and path.read_bytes()[:5] == b"%PDF-":
                return path
        except (NotImplementedError, OSError):
            pass
    manifest = version.project_manifest or {}
    if not manifest.get("safe_to_compile", True):
        version.latex_compile_status = "blocked"
        version.latex_compile_message = (
            "Компиляция отключена: исходник содержит потенциально опасные команды."
        )
        version.save(update_fields=["latex_compile_status", "latex_compile_message"])
        raise LatexProjectError(version.latex_compile_message)
    executable = _latex_executable()
    if executable is None:
        version.latex_compile_status = "unavailable"
        version.latex_compile_message = "На сервере не установлен latexmk или pdflatex."
        version.save(update_fields=["latex_compile_status", "latex_compile_message"])
        raise LatexProjectError(version.latex_compile_message)

    timeout = max(10, int(getattr(settings, "LATEX_COMPILE_TIMEOUT_SECONDS", 90)))
    with tempfile.TemporaryDirectory(prefix="latex-preview-") as temporary:
        project_root = Path(temporary)
        main_path = _write_project_tree(version, project_root)
        working_directory = main_path.parent
        binary, is_latexmk = executable
        if is_latexmk:
            command = [
                binary,
                "-pdf",
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-file-line-error",
                "-no-shell-escape",
                main_path.name,
            ]
        else:
            command = [
                binary,
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-file-line-error",
                "-no-shell-escape",
                main_path.name,
            ]
        environment = os.environ.copy()
        environment.update(
            {
                "HOME": str(project_root),
                "TMPDIR": str(project_root),
                "openin_any": "p",
                "openout_any": "p",
                "max_print_line": "200",
            }
        )
        try:
            result = subprocess.run(
                command,
                cwd=working_directory,
                env=environment,
                capture_output=True,
                check=False,
                timeout=timeout,
            )
            if not is_latexmk and result.returncode == 0:
                result = subprocess.run(
                    command,
                    cwd=working_directory,
                    env=environment,
                    capture_output=True,
                    check=False,
                    timeout=timeout,
                )
        except (OSError, subprocess.TimeoutExpired) as exc:
            version.latex_compile_status = "error"
            version.latex_compile_message = "LaTeX-компиляция не завершилась за отведённое время."
            version.save(update_fields=["latex_compile_status", "latex_compile_message"])
            raise LatexProjectError(version.latex_compile_message) from exc

        pdf_path = main_path.with_suffix(".pdf")
        if result.returncode != 0 or not pdf_path.exists() or pdf_path.read_bytes()[:5] != b"%PDF-":
            combined_log = (result.stdout or b"") + b"\n" + (result.stderr or b"")
            message = combined_log.decode("utf-8", errors="replace")[-6000:].strip()
            version.latex_compile_status = "error"
            version.latex_compile_message = message or "LaTeX не смог собрать PDF."
            version.save(update_fields=["latex_compile_status", "latex_compile_message"])
            raise LatexProjectError("LaTeX не смог собрать PDF. Подробности показаны в редакторе.")

        version.rendered_pdf.save(
            f"submission-{version.submission_id}-v{version.version_number}.pdf",
            ContentFile(pdf_path.read_bytes()),
            save=False,
        )
        version.latex_compile_status = "ready"
        version.latex_compile_message = "PDF успешно собран из текущего LaTeX-проекта."
        version.save(
            update_fields=["rendered_pdf", "latex_compile_status", "latex_compile_message"]
        )
        return Path(version.rendered_pdf.path)
