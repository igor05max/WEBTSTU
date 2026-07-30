from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


def prepare_run_directory(path: Path) -> None:
    """Очищает только служебные результаты предыдущего запуска.

    Произвольные пользовательские файлы в output не удаляются.
    """
    path.mkdir(parents=True, exist_ok=True)
    for name in ("input", "parsed", "generated", "validation", "result"):
        target = path / name
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
    for name in ("run.json",):
        target = path / name
        if target.exists():
            target.unlink()


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
