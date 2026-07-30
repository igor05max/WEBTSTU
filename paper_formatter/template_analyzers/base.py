from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from paper_formatter.models import TemplateProfile


class TemplateAnalyzer(ABC):
    @abstractmethod
    def analyze(self, source: Path) -> TemplateProfile:
        """Построить профиль оформления по документу-образцу."""
