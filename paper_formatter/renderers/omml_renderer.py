from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from lxml import etree


class OmmlConversionError(RuntimeError):
    """Raised when a LaTeX formula cannot be converted to native Word OMML."""


class LatexToOmmlConverter:
    """Convert mathematical LaTeX to editable Word equations."""

    def convert(self, latex: str, *, display: bool = False) -> etree._Element:
        value = self._normalize(latex)
        if not value:
            raise OmmlConversionError("Пустая формула.")
        try:
            from latex2mathml.converter import convert as latex_to_mathml
        except ImportError as exc:
            raise OmmlConversionError(
                "Не установлена зависимость latex2mathml."
            ) from exc

        try:
            mathml = latex_to_mathml(
                value,
                display="block" if display else "inline",
            )
            mathml_root = etree.fromstring(mathml.encode("utf-8"))
            result = self._transformer()(mathml_root)
            root = result.getroot()
        except Exception as exc:
            raise OmmlConversionError(
                f"Не удалось преобразовать формулу в OMML: {exc}"
            ) from exc
        if root is None:
            raise OmmlConversionError("Конвертер OMML вернул пустой результат.")
        return root

    @classmethod
    def available(cls) -> bool:
        try:
            cls._transformer()
            from latex2mathml.converter import convert as _convert  # noqa: F401
        except (ImportError, OmmlConversionError):
            return False
        return True

    @staticmethod
    def _normalize(latex: str) -> str:
        value = latex.strip()
        wrappers = (("$", "$"), (r"\(", r"\)"), (r"\[", r"\]"))
        for left, right in wrappers:
            if value.startswith(left) and value.endswith(right):
                value = value[len(left) : -len(right)].strip()
                break
        return value

    @classmethod
    @lru_cache(maxsize=1)
    def _transformer(cls) -> etree.XSLT:
        stylesheet = cls._find_stylesheet()
        if stylesheet is None:
            raise OmmlConversionError(
                "Не найден MML2OMML.XSL. Установите Microsoft Word или задайте "
                "PAPER_FORMATTER_MML2OMML_XSL."
            )
        try:
            return etree.XSLT(etree.parse(str(stylesheet)))
        except Exception as exc:
            raise OmmlConversionError(
                f"Не удалось загрузить {stylesheet}: {exc}"
            ) from exc

    @staticmethod
    def _find_stylesheet() -> Path | None:
        configured = os.getenv("PAPER_FORMATTER_MML2OMML_XSL")
        candidates: list[Path] = []
        if configured:
            candidates.append(Path(configured))
        for variable in ("ProgramFiles", "ProgramFiles(x86)"):
            root = os.getenv(variable)
            if not root:
                continue
            office_root = Path(root) / "Microsoft Office"
            candidates.extend(
                [
                    office_root / "root" / "Office16" / "MML2OMML.XSL",
                    office_root / "Office16" / "MML2OMML.XSL",
                    office_root / "Office15" / "MML2OMML.XSL",
                    office_root / "Office14" / "MML2OMML.XSL",
                ]
            )
        return next((path for path in candidates if path.is_file()), None)
