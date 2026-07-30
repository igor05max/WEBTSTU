from __future__ import annotations

import re
import os
import shutil
import subprocess
from pathlib import Path


class LatexCompiler:
    def __init__(self, timeout_seconds: int = 240) -> None:
        self.timeout_seconds = timeout_seconds

    def compile(self, project_dir: Path, log_path: Path) -> tuple[Path | None, list[str]]:
        warnings: list[str] = []
        project_dir = Path(project_dir).resolve()
        main_tex = project_dir / "main.tex"
        if not main_tex.exists():
            return None, ["main.tex не найден; компиляция невозможна."]

        engine = self._select_engine()
        if engine is None:
            warnings.append(
                "Не найден LaTeX-компилятор. Установите Tectonic либо "
                "MiKTeX/TeX Live с latexmk и XeLaTeX."
            )
            return None, warnings
        name, executable = engine
        command = self._command(name, executable)
        outputs: list[str] = []
        return_code = 0
        attempts = 2 if name in {"xelatex", "lualatex"} else 1
        process_environment = os.environ.copy()
        fontconfig_file = project_dir / "fonts.conf"
        if fontconfig_file.exists():
            process_environment["FONTCONFIG_FILE"] = str(fontconfig_file)
        try:
            for _ in range(attempts):
                process = subprocess.run(
                    command,
                    cwd=project_dir,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout_seconds,
                    check=False,
                    env=process_environment,
                )
                return_code = process.returncode
                outputs.extend([process.stdout, process.stderr])
                if return_code != 0:
                    break
        except subprocess.TimeoutExpired as exc:
            outputs.append(str(exc))
            warnings.append(
                f"Компиляция остановлена по таймауту {self.timeout_seconds} секунд."
            )
            return_code = 124
        except OSError as exc:
            outputs.append(str(exc))
            warnings.append(f"Не удалось запустить {name}: {exc}")
            return_code = 1

        log_text = (
            f"ENGINE={name}\n$ {' '.join(command)}\n\n" + "\n".join(outputs)
        )
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(log_text, encoding="utf-8")
        warnings.extend(self._log_warnings(log_text))

        pdf = project_dir / "main.pdf"
        if return_code != 0 or not pdf.exists():
            warnings.append(f"LaTeX не скомпилирован. Подробности: {log_path}")
            return None, list(dict.fromkeys(warnings))
        return pdf, list(dict.fromkeys(warnings))

    @staticmethod
    def _select_engine() -> tuple[str, str] | None:
        for name in ("latexmk", "tectonic", "xelatex", "lualatex"):
            executable = shutil.which(name)
            if executable:
                return name, executable
        configured = os.getenv("PAPER_FORMATTER_TECTONIC", "").strip()
        candidates = [
            Path(configured) if configured else None,
            Path(__file__).resolve().parent.parent / "tools" / "tectonic" / "tectonic.exe",
        ]
        for candidate in candidates:
            if candidate and candidate.exists() and candidate.is_file():
                return "tectonic", str(candidate)
        return None

    @staticmethod
    def _command(name: str, executable: str) -> list[str]:
        if name == "latexmk":
            return [
                executable,
                "-xelatex",
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-file-line-error",
                "main.tex",
            ]
        if name == "tectonic":
            return [
                executable,
                "--keep-logs",
                "--keep-intermediates",
                "--synctex",
                "main.tex",
            ]
        return [
            executable,
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-file-line-error",
            "main.tex",
        ]

    @staticmethod
    def _log_warnings(text: str) -> list[str]:
        result: list[str] = []
        overfull = [
            float(value)
            for value in re.findall(
                r"Overfull \\[hv]box \(([0-9.]+)pt too wide\)",
                text,
                re.IGNORECASE,
            )
        ]
        if overfull and max(overfull) >= 5.0:
            result.append(
                f"В PDF есть существенное переполнение до {max(overfull):.2f} pt."
            )
        patterns = [
            (
                r"Missing character:",
                "В PDF отсутствуют глифы: часть символов не отображена.",
            ),
            (r"Underfull \\[hv]box", "В PDF есть заметно разреженные строки или блоки."),
            (
                r"(?:undefined references|Reference .* undefined)",
                "В LaTeX остались неразрешённые перекрёстные ссылки.",
            ),
            (
                r"(?:undefined citations|Citation .* undefined)",
                "В LaTeX остались неразрешённые цитаты.",
            ),
            (r"LaTeX Error:", "Журнал компиляции содержит LaTeX Error."),
            (r"Emergency stop", "Компиляция завершилась аварийно."),
        ]
        for pattern, message in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                result.append(message)
        return result
