from __future__ import annotations

import re
from pathlib import Path

from paper_formatter.models import ReferenceEntry


class BibliographyParser:
    """Консервативно читает BibTeX/BibLaTeX без выполнения макросов."""

    def parse(self, source: Path) -> list[ReferenceEntry]:
        text = Path(source).read_text(encoding="utf-8", errors="replace")
        return self.parse_text(text)

    def parse_text(self, text: str) -> list[ReferenceEntry]:
        result: list[ReferenceEntry] = []
        index = 0
        while True:
            match = re.search(r"@([A-Za-z]+)\s*([\{\(])", text[index:])
            if not match:
                break
            entry_type = match.group(1).lower()
            opening = match.group(2)
            start = index + match.end()
            closing = "}" if opening == "{" else ")"
            end = self._balanced_end(text, start, opening, closing)
            if end is None:
                break
            content = text[start:end].strip()
            index = end + 1
            if entry_type in {"comment", "preamble", "string"}:
                continue
            key, separator, fields_text = content.partition(",")
            if not separator or not key.strip():
                continue
            fields = self._fields(fields_text)
            reference_text = self._format_reference(fields, key.strip())
            result.append(
                ReferenceEntry(
                    id=f"ref-{len(result) + 1}",
                    citation_key=key.strip(),
                    text=reference_text,
                    doi=self._clean_value(fields.get("doi", "")) or None,
                    metadata={
                        "entry_type": entry_type,
                        **{
                            name: self._clean_value(value)
                            for name, value in fields.items()
                            if self._clean_value(value)
                        },
                    },
                )
            )
        return result

    def _fields(self, text: str) -> dict[str, str]:
        fields: dict[str, str] = {}
        index = 0
        while index < len(text):
            match = re.search(r"([A-Za-z][A-Za-z0-9_-]*)\s*=", text[index:])
            if not match:
                break
            name = match.group(1).lower()
            value_start = index + match.end()
            while value_start < len(text) and text[value_start].isspace():
                value_start += 1
            if value_start >= len(text):
                break
            if text[value_start] in '{"':
                opening = text[value_start]
                closing = "}" if opening == "{" else '"'
                if opening == '"':
                    value_end = self._quoted_end(text, value_start + 1)
                else:
                    value_end = self._balanced_end(text, value_start + 1, "{", "}")
                if value_end is None:
                    break
                value = text[value_start : value_end + 1]
                index = value_end + 1
            else:
                comma = text.find(",", value_start)
                value_end = len(text) if comma < 0 else comma
                value = text[value_start:value_end]
                index = value_end + 1
            fields[name] = value.strip()
        return fields

    @staticmethod
    def _balanced_end(
        text: str, start: int, opening: str, closing: str
    ) -> int | None:
        depth = 1
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == opening:
                depth += 1
            elif char == closing:
                depth -= 1
                if depth == 0:
                    return index
        return None

    @staticmethod
    def _quoted_end(text: str, start: int) -> int | None:
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                return index
        return None

    def _format_reference(self, fields: dict[str, str], fallback: str) -> str:
        values = [
            self._clean_value(fields.get(name, ""))
            for name in ("author", "title", "journal", "booktitle", "publisher", "year")
        ]
        values = [value for value in values if value]
        return ". ".join(values) if values else fallback

    @staticmethod
    def _clean_value(value: str) -> str:
        value = value.strip().strip(",")
        if len(value) >= 2 and (
            (value[0] == "{" and value[-1] == "}")
            or (value[0] == '"' and value[-1] == '"')
        ):
            value = value[1:-1]
        value = re.sub(r"[{}]", "", value)
        value = re.sub(r"\\(?:textit|textbf|emph)\s*\{([^{}]*)\}", r"\1", value)
        return re.sub(r"\s+", " ", value).strip()
