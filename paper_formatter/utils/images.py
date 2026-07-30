from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def convert_metafile_to_png(source: Path, target: Path, dpi: int = 300) -> tuple[bool, str | None]:
    """Convert WMF/EMF to PNG using the first available local backend.

    On Windows Pillow can use the native GDI renderer. On other systems Inkscape
    is usually the most reliable fallback for Word/MathType preview images.
    """
    source = Path(source)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    try:
        from PIL import Image

        with Image.open(source) as image:
            try:
                image.load(dpi=dpi)
            except TypeError:
                image.load()
            converted = image.convert("RGBA")
            background = Image.new("RGBA", converted.size, "white")
            background.alpha_composite(converted)
            background.convert("RGB").save(target, "PNG")
        if target.exists() and target.stat().st_size > 0:
            return True, None
    except Exception as exc:  # pragma: no cover - depends on OS/Pillow build
        errors.append(f"Pillow: {exc}")

    inkscape = shutil.which("inkscape")
    if inkscape:
        command = [
            inkscape,
            str(source),
            "--export-type=png",
            f"--export-filename={target}",
            f"--export-dpi={dpi}",
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if completed.returncode == 0 and target.exists() and target.stat().st_size > 0:
                return True, None
            errors.append(f"Inkscape: {completed.stderr.strip() or completed.stdout.strip()}")
        except Exception as exc:  # pragma: no cover - external executable
            errors.append(f"Inkscape: {exc}")

    return False, "; ".join(part for part in errors if part) or "Нет доступного конвертера WMF/EMF"
