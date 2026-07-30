from __future__ import annotations

from abc import ABC, abstractmethod

from paper_formatter.models import ArticleIR


class SourceParser(ABC):
    @abstractmethod
    def parse(self) -> ArticleIR:
        """Преобразовать исходный документ в ArticleIR."""
