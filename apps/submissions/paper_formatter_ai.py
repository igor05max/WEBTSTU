from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from django.conf import settings as django_settings
from pydantic import ValidationError

from apps.checks.ai_client import (
    AIProviderError,
    extract_response_text,
    generate_content,
    normalize_model_id,
)
from paper_formatter.config import SemanticSettings
from paper_formatter.semantic.base import SemanticProvider
from paper_formatter.semantic.models import (
    SemanticAnalysis,
    SemanticBlock,
    SemanticDecision,
)


ALLOWED_ROLES = (
    "title",
    "subtitle",
    "author",
    "affiliation",
    "abstract_heading",
    "abstract",
    "keywords",
    "section",
    "subsection",
    "subsubsection",
    "list_item",
    "figure_caption",
    "table_caption",
    "references_heading",
    "reference",
    "paragraph",
    "unknown",
)


class QwenSemanticProvider(SemanticProvider):
    """Классифицирует только структуру спорных блоков через локальный Qwen."""

    name = "qwen"

    def __init__(
        self,
        settings: SemanticSettings,
        cache_dir: Path | None = None,
    ) -> None:
        self.settings = settings
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def analyze_document(
        self,
        blocks: list[SemanticBlock],
        *,
        document_name: str,
        context: dict[str, Any] | None = None,
    ) -> SemanticAnalysis:
        if not blocks:
            return SemanticAnalysis(provider=self.name)

        payload_blocks = [self._serialize_block(block) for block in blocks]
        model = self.settings.model or normalize_model_id(
            getattr(django_settings, "AI_MODEL", "")
        )
        request_key = self._cache_key(document_name, model, payload_blocks)
        cached = self._read_cache(request_key)
        if cached is not None:
            cached.provider = f"{self.name}:cache"
            return cached

        request_body = {
            "systemInstruction": {
                "parts": [
                    {
                        "text": (
                            "Ты классификатор структуры научных документов. "
                            "Не переписывай текст, не исправляй факты и не придумывай блоки. "
                            "Определи только роль каждого переданного block_id и верни JSON."
                        )
                    }
                ]
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": self._build_prompt(
                                document_name,
                                payload_blocks,
                                context or {},
                            )
                        }
                    ],
                }
            ],
            "generationConfig": {
                "maxOutputTokens": 16384,
                "temperature": 0,
                "responseMimeType": "application/json",
            },
        }

        try:
            response, used_model = generate_content(
                request_body,
                model=model,
                timeout=self.settings.timeout_seconds,
            )
            raw_response = extract_response_text(response)
            analysis = self._parse_analysis(raw_response, blocks)
            analysis.provider = f"{self.name}:{used_model}"
            self._write_cache(request_key, analysis)
            return analysis
        except (AIProviderError, OSError, ValueError, ValidationError) as exc:
            return SemanticAnalysis(
                provider=self.name,
                warnings=[
                    "Qwen не применён; структурная классификация продолжена "
                    f"локальными правилами. {exc}"
                ],
            )

    def _serialize_block(self, block: SemanticBlock) -> dict[str, Any]:
        data = block.model_dump(mode="json")
        data["text"] = data["text"][: self.settings.max_text_chars]
        data["previous_text"] = data["previous_text"][:220]
        data["next_text"] = data["next_text"][:220]
        return data

    @staticmethod
    def _build_prompt(
        document_name: str,
        blocks: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> str:
        return json.dumps(
            {
                "task": "Определи структурную роль каждого блока научной рукописи.",
                "document_name": document_name,
                "allowed_roles": list(ALLOWED_ROLES),
                "response": {
                    "decisions": [
                        {
                            "block_id": "исходный block_id",
                            "role": "одно значение из allowed_roles",
                            "confidence": "число от 0 до 1",
                            "heading_level": "1-6 или null",
                            "normalized_text": "null; текст не переписывать",
                            "reason": "краткое объяснение",
                        }
                    ]
                },
                "rules": [
                    "Не изменяй и не дополняй содержание текста.",
                    "Нумерованный абзац может быть пунктом списка, а не заголовком.",
                    "Учитывай стиль Word, форматирование, соседние блоки и порядок.",
                    "После заголовка списка литературы элементы являются reference.",
                    "Для обычного основного текста используй paragraph.",
                    "Верни строго один JSON-объект без Markdown.",
                ],
                "context": context,
                "blocks": blocks,
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _parse_analysis(raw: str, blocks: list[SemanticBlock]) -> SemanticAnalysis:
        text = str(raw or "").strip()
        if not text:
            raise ValueError("Qwen вернул пустой ответ")
        if text.startswith("```"):
            first = text.find("{")
            last = text.rfind("}")
            if first >= 0 and last > first:
                text = text[first : last + 1]
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("Qwen вернул JSON неверного типа")

        valid_ids = {block.block_id for block in blocks}
        decisions: list[SemanticDecision] = []
        seen: set[str] = set()
        for item in data.get("decisions") or []:
            if not isinstance(item, dict):
                continue
            try:
                decision = SemanticDecision.model_validate(
                    {**item, "normalized_text": None, "source": "local_ai"}
                )
            except ValidationError:
                continue
            if decision.block_id not in valid_ids or decision.block_id in seen:
                continue
            seen.add(decision.block_id)
            decisions.append(decision)
        if not decisions:
            raise ValueError("В ответе Qwen нет решений для известных block_id")
        return SemanticAnalysis(
            provider="qwen",
            decisions=decisions,
            raw_response=raw,
        )

    def _cache_key(
        self,
        document_name: str,
        model: str,
        blocks: list[dict[str, Any]],
    ) -> str:
        payload = json.dumps(
            {"model": model, "document": document_name, "blocks": blocks},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _cache_path(self, key: str) -> Path | None:
        return self.cache_dir / f"{key}.json" if self.cache_dir else None

    def _read_cache(self, key: str) -> SemanticAnalysis | None:
        path = self._cache_path(key)
        if path is None or not path.exists():
            return None
        try:
            return SemanticAnalysis.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, ValidationError):
            return None

    def _write_cache(self, key: str, analysis: SemanticAnalysis) -> None:
        path = self._cache_path(key)
        if path is None:
            return
        path.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
