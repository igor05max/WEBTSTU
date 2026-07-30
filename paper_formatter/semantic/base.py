from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from paper_formatter.semantic.models import SemanticAnalysis, SemanticBlock


class SemanticProvider(ABC):
    """Заменяемый интерфейс семантического анализа документа.

    Провайдер не читает DOCX и не создаёт LaTeX. Он получает уже извлечённые
    текстовые блоки с форматными признаками и возвращает только структурные роли.
    """

    name: str = "semantic-provider"

    @abstractmethod
    def analyze_document(
        self,
        blocks: list[SemanticBlock],
        *,
        document_name: str,
        context: dict[str, Any] | None = None,
    ) -> SemanticAnalysis:
        raise NotImplementedError
