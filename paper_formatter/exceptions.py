class PaperFormatterError(Exception):
    """Базовая контролируемая ошибка конвертера."""


class UnsupportedInputError(PaperFormatterError):
    """Формат входного файла пока не поддерживается."""


class DocxParseError(PaperFormatterError):
    """DOCX повреждён или не может быть разобран."""


class LatexCompilationError(PaperFormatterError):
    """Ошибка компиляции LaTeX-проекта."""


class PackageSecurityError(PaperFormatterError):
    """Пакет небезопасен для распаковки."""


class TemplateAnalysisError(PaperFormatterError):
    """Документ-образец не удалось проанализировать."""
