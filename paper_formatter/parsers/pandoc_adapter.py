from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any


class PandocAdapter:
    """Независимый аудит источника через Pandoc AST."""

    @staticmethod
    def available() -> bool:
        return shutil.which("pandoc") is not None

    def analyze(
        self,
        source: Path,
        media_dir: Path,
        *,
        timeout_seconds: int = 180,
    ) -> dict[str, Any]:
        executable = shutil.which("pandoc")
        if executable is None:
            return {
                "available": False,
                "warnings": ["Pandoc не найден; независимый второй канал не выполнен."],
            }
        source = Path(source).resolve()
        media_dir = Path(media_dir).resolve()
        media_dir.mkdir(parents=True, exist_ok=True)
        command = [
            executable,
            "--sandbox",
            "--to=json",
            f"--extract-media={media_dir}",
            str(source),
        ]
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        if process.returncode != 0:
            return {
                "available": True,
                "success": False,
                "command": command,
                "warnings": [process.stderr[-3000:] or "Pandoc завершился с ошибкой."],
            }
        ast = json.loads(process.stdout)
        counts: Counter[str] = Counter()
        strings: list[str] = []
        links: list[str] = []
        images: list[str] = []
        citations: list[str] = []

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                node_type = value.get("t")
                if isinstance(node_type, str):
                    counts[node_type] += 1
                    content = value.get("c")
                    if node_type == "Str" and isinstance(content, str):
                        strings.append(content)
                    elif node_type == "Space":
                        strings.append(" ")
                    elif node_type == "SoftBreak":
                        strings.append("\n")
                    elif node_type == "Link" and isinstance(content, list) and content:
                        target = content[-1]
                        if isinstance(target, list) and target:
                            links.append(str(target[0]))
                    elif node_type == "Image" and isinstance(content, list) and content:
                        target = content[-1]
                        if isinstance(target, list) and target:
                            images.append(str(target[0]))
                    elif node_type == "Cite" and isinstance(content, list) and content:
                        for item in content[0]:
                            if isinstance(item, dict) and item.get("citationId"):
                                citations.append(str(item["citationId"]))
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(ast)
        text = re.sub(r"\s+", " ", "".join(strings)).strip()
        return {
            "available": True,
            "success": True,
            "pandoc_api_version": ast.get("pandoc-api-version"),
            "counts": dict(sorted(counts.items())),
            "text_characters": len(text),
            "text_sha256": self._text_hash(text),
            "links": list(dict.fromkeys(links)),
            "images": list(dict.fromkeys(images)),
            "citations": list(dict.fromkeys(citations)),
            "warnings": [
                line
                for line in process.stderr.splitlines()
                if "warning" in line.lower()
            ],
        }

    @staticmethod
    def _text_hash(text: str) -> str:
        import hashlib

        return hashlib.sha256(text.encode("utf-8")).hexdigest()
