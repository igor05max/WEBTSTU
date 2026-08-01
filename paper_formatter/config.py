from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class SemanticSettings:
    """Настройки гибридной структурной классификации.

    Локальные правила всегда выполняются первыми. Внешний провайдер получает
    только неоднозначные структурные блоки и не имеет права переписывать текст.
    Сам пакет остаётся независимым от конкретного AI-клиента: провайдер
    внедряется приложением через ``SemanticProvider``.
    """

    enabled: bool = False
    provider: str = "rules"
    model: str = ""
    timeout_seconds: int = 90
    min_rule_confidence: float = 0.86
    max_blocks_per_request: int = 70
    max_text_chars: int = 700


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def load_semantic_settings() -> SemanticSettings:
    return SemanticSettings(
        enabled=_env_bool("PAPER_FORMATTER_AI", False),
        provider=os.getenv("PAPER_FORMATTER_AI_PROVIDER", "rules").strip().lower(),
        model=os.getenv("PAPER_FORMATTER_AI_MODEL", "").strip(),
        timeout_seconds=int(os.getenv("PAPER_FORMATTER_AI_TIMEOUT", "90")),
        min_rule_confidence=float(os.getenv("SEMANTIC_RULE_CONFIDENCE", "0.86")),
        max_blocks_per_request=int(os.getenv("PAPER_FORMATTER_AI_MAX_BLOCKS", "70")),
        max_text_chars=int(os.getenv("PAPER_FORMATTER_AI_MAX_TEXT_CHARS", "700")),
    )
