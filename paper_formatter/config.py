from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SemanticSettings:
    """Settings for deterministic structural classification.

    The web application deliberately keeps document conversion local.  Qwen is
    used by the application's review services, while the formatter relies on
    reproducible rules and never sends document blocks to an external API.
    """

    enabled: bool = False
    provider: str = "rules"
    min_rule_confidence: float = 0.86
    max_text_chars: int = 700


def load_semantic_settings() -> SemanticSettings:
    return SemanticSettings()
