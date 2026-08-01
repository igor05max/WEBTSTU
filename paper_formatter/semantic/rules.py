from __future__ import annotations

import re
from dataclasses import dataclass

from paper_formatter.semantic.models import (
    SemanticAnalysis,
    SemanticBlock,
    SemanticDecision,
    SemanticRole,
)


_HEADING_STYLE = re.compile(r"(?:heading|заголовок)\s*(\d+)", re.IGNORECASE)
_NUMBERED_HEADING = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s+(.+)$")
_CAPTION = re.compile(
    r"^\s*(рисунок|рис\.?|figure|fig\.?|таблица|table)\s*\d+",
    re.IGNORECASE,
)
_REFERENCES = {
    "литература",
    "список литературы",
    "references",
    "bibliography",
    "библиографический список",
}


@dataclass(frozen=True)
class _RuleResult:
    role: SemanticRole
    confidence: float
    reason: str
    heading_level: int | None = None


class RuleSemanticClassifier:
    """Консервативный классификатор структурных ролей DOCX.

    Он намеренно не принимает каждый абзац с номером за заголовок. Спорные случаи
    получают низкую уверенность и затем могут быть переданы нейросети.
    """

    name = "rules"

    def analyze_document(
        self,
        blocks: list[SemanticBlock],
        *,
        document_name: str,
        context: dict | None = None,
    ) -> SemanticAnalysis:
        decisions: list[SemanticDecision] = []
        abstract_open = False
        references_open = False
        first_content_order = min((b.order for b in blocks if b.text.strip()), default=10**9)

        for block in blocks:
            result = self._classify(
                block,
                first_content_order=first_content_order,
                abstract_open=abstract_open,
                references_open=references_open,
            )

            if result.role == "abstract_heading":
                abstract_open = True
            elif result.role in {"keywords", "section", "subsection", "subsubsection", "references_heading"}:
                abstract_open = False
            if result.role == "references_heading":
                references_open = True
            elif references_open and result.role in {"section", "subsection", "subsubsection"}:
                references_open = False

            block.rule_role = result.role
            block.rule_confidence = result.confidence
            block.rule_reason = result.reason
            decisions.append(
                SemanticDecision(
                    block_id=block.block_id,
                    role=result.role,
                    confidence=result.confidence,
                    heading_level=result.heading_level,
                    reason=result.reason,
                    source="rules",
                )
            )

        self._promote_front_matter(blocks, decisions)
        final_by_id = {decision.block_id: decision for decision in decisions}
        for block in blocks:
            decision = final_by_id[block.block_id]
            block.rule_role = decision.role
            block.rule_confidence = decision.confidence
            block.rule_reason = decision.reason
        return SemanticAnalysis(provider=self.name, decisions=decisions)

    def _classify(
        self,
        block: SemanticBlock,
        *,
        first_content_order: int,
        abstract_open: bool,
        references_open: bool,
    ) -> _RuleResult:
        text = self._clean(block.text)
        lower = text.lower()
        style = block.style.lower().strip()
        normalized_style = re.sub(r"\s+", " ", style)

        # В реальных рукописях авторы нередко случайно оставляют стиль Heading
        # на целом абзаце. Длинный законченный текст не должен из-за этого
        # превращаться в заголовок и ломать журнальную верстку.
        heading_style = _HEADING_STYLE.search(style)
        if (
            len(text) > 240
            and (heading_style or block.outline_level is not None)
            and text.endswith((".", "!", "?"))
        ):
            return _RuleResult(
                "paragraph",
                0.98,
                "Длинный законченный абзац ошибочно размечен стилем заголовка",
            )

        if not text:
            return _RuleResult("paragraph", 1.0, "Пустой служебный блок")

        if lower in _REFERENCES or self._without_number(lower) in _REFERENCES:
            return _RuleResult("references_heading", 0.99, "Явный заголовок библиографии", 1)

        if re.match(r"^(аннотация|abstract)\s*[:.]?\s*$", text, re.IGNORECASE):
            return _RuleResult("abstract_heading", 0.99, "Явный заголовок аннотации")
        if re.match(r"^(аннотация|abstract)\s*[:.]\s*\S", text, re.IGNORECASE):
            return _RuleResult("abstract", 0.99, "Аннотация записана в одном абзаце")
        if re.match(r"^(ключевые\s+слова|keywords|key\s+words)\s*[:.]", text, re.IGNORECASE):
            return _RuleResult("keywords", 0.99, "Явная строка ключевых слов")

        if _CAPTION.match(text):
            role: SemanticRole = "table_caption" if re.match(r"^\s*(таблица|table)", text, re.I) else "figure_caption"
            return _RuleResult(role, 0.98, "Явная подпись объекта")

        if references_open and re.match(r"^\s*\d+[.)]\s+", text):
            return _RuleResult("reference", 0.98, "Нумерованный источник после заголовка литературы")

        if normalized_style in {"title", "document title", "название", "заглавие", "название документа"}:
            return _RuleResult("title", 0.99, "Явный стиль названия")

        if "subtitle" in style or "подзаголовок" in style:
            if self.looks_like_author_line(text):
                return _RuleResult("author", 0.88, "Стиль подзаголовка, но текст похож на ФИО")
            return _RuleResult("subtitle", 0.95, "Явный стиль подзаголовка")

        if any(token in style for token in ("author", "автор")):
            return _RuleResult("author", 0.98, "Явный стиль автора")
        if any(token in style for token in ("affiliation", "организац", "аффилиац")):
            return _RuleResult("affiliation", 0.95, "Явный стиль аффилиации")
        if "abstract" in style or "аннотац" in style:
            return _RuleResult("abstract", 0.96, "Стиль аннотации")
        if "keyword" in style or "ключев" in style:
            return _RuleResult("keywords", 0.96, "Стиль ключевых слов")

        if abstract_open:
            if self.looks_like_author_line(text):
                return _RuleResult("author", 0.72, "ФИО рядом с метаданными, но позиция нетипична")
            return _RuleResult("abstract", 0.88, "Абзац между заголовком аннотации и ключевыми словами/разделом")

        numbered_match = _NUMBERED_HEADING.match(text)
        if numbered_match and not block.is_in_numbered_sequence:
            explicit_level = len(numbered_match.group(1).split("."))
            tail = numbered_match.group(2).strip()
            style_heading = heading_style
            if style_heading or block.outline_level is not None:
                return _RuleResult(
                    self._role_for_level(explicit_level),
                    0.99,
                    "Явный стиль заголовка; уровень взят из номера раздела",
                    explicit_level,
                )
            if len(tail) <= 180 and not tail.endswith((".", ";", ",", ":")) and (
                block.bold_ratio >= 0.55 or block.font_size_pt and block.font_size_pt >= 13
            ):
                return _RuleResult(
                    self._role_for_level(explicit_level),
                    0.86,
                    "Нумерованный короткий выделенный заголовок",
                    explicit_level,
                )

        style_heading = heading_style
        if style_heading:
            level = max(1, min(6, int(style_heading.group(1))))
            role = self._role_for_level(level)
            return _RuleResult(role, 0.99, "Явный стиль заголовка", level)

        if block.outline_level is not None:
            level = max(1, min(6, block.outline_level))
            role = self._role_for_level(level)
            return _RuleResult(role, 0.96, "Задан outline level Word", level)

        if block.has_numbering:
            return _RuleResult("list_item", 0.98, "Настоящая нумерация Word")

        if any(token in style for token in ("list", "список", "маркир", "нумер")):
            return _RuleResult("list_item", 0.96, "Стиль списка")

        if any(token in style for token in ("source code", "code", "код")) and block.numbered_prefix:
            return _RuleResult("list_item", 0.97, "Нумерованный текст в стиле кода, а не заголовка")

        if numbered_match:
            level = len(numbered_match.group(1).split("."))
            tail = numbered_match.group(2).strip()
            if block.is_in_numbered_sequence:
                return _RuleResult("list_item", 0.93, "Последовательность коротких нумерованных пунктов")
            if len(tail) <= 180 and not tail.endswith((".", ";", ",", ":")) and (
                block.bold_ratio >= 0.55 or block.font_size_pt and block.font_size_pt >= 13
            ):
                return _RuleResult(self._role_for_level(level), 0.86, "Нумерованный короткий выделенный заголовок", level)
            return _RuleResult("unknown", 0.48, "Номер может обозначать как раздел, так и пункт списка")

        if self.looks_like_author_line(text) and block.order <= first_content_order + 30:
            return _RuleResult("author", 0.78, "Строка похожа на одно или несколько ФИО в начале документа")

        if (
            block.order == first_content_order
            and len(text) <= 500
            and not re.match(r"^(УДК|UDC|DOI|ISSN)\b", text, re.IGNORECASE)
            and not block.numbered_prefix
        ):
            return _RuleResult("title", 0.72, "Первый содержательный абзац похож на название")

        if (
            block.order <= first_content_order + 12
            and len(text) <= 500
            and block.alignment == "center"
            and (block.bold_ratio >= 0.5 or (block.font_size_pt or 0) >= 14)
        ):
            return _RuleResult("unknown", 0.55, "Выделенный центрированный блок во вступительной части")

        return _RuleResult("paragraph", 0.92, "Обычный абзац")

    def _promote_front_matter(
        self,
        blocks: list[SemanticBlock],
        decisions: list[SemanticDecision],
    ) -> None:
        by_id = {d.block_id: d for d in decisions}
        front = [b for b in blocks if b.text.strip()][:35]
        if not front:
            return

        titles = [d for d in decisions if d.role == "title"]
        if not titles:
            candidates = [
                b for b in front[:10]
                if 5 <= len(self._clean(b.text)) <= 500
                and not (
                    len(self._clean(b.text)) > 240
                    and self._clean(b.text).endswith((".", "!", "?"))
                )
                and not b.numbered_prefix
                and not b.has_numbering
                and by_id[b.block_id].role != "list_item"
                and not self.looks_like_author_line(b.text)
                and not re.match(r"^(аннотация|abstract|ключевые слова|keywords)\b", b.text, re.I)
            ]
            if candidates:
                best = max(
                    candidates,
                    key=lambda b: (
                        2 if b.alignment == "center" else 0,
                        b.bold_ratio,
                        b.font_size_pt or 0,
                        min(len(b.text), 250) / 250,
                        -b.order / 1000,
                    ),
                )
                decision = by_id[best.block_id]
                decision.role = "title"
                decision.confidence = max(decision.confidence, 0.68)
                decision.reason = "Лучший кандидат названия во вступительной части"

        title_blocks = [
            block for block in front if by_id[block.block_id].role == "title"
        ]
        if title_blocks:
            main_title = min(title_blocks, key=lambda block: block.order)
            following = [block for block in front if block.order > main_title.order]
            if following:
                candidate = following[0]
                candidate_decision = by_id[candidate.block_id]
                candidate_text = self._clean(candidate.text)
                if (
                    candidate_decision.role in {"paragraph", "unknown"}
                    and 20 <= len(candidate_text) <= 500
                    and not self.looks_like_author_line(candidate_text)
                    and not re.match(r"^(аннотация|abstract|ключевые слова|keywords)\b", candidate_text, re.I)
                ):
                    candidate_decision.role = "subtitle"
                    candidate_decision.confidence = max(candidate_decision.confidence, 0.74)
                    candidate_decision.reason = "Короткий блок сразу после основного названия"

        # Если абзац после названия явно похож на ФИО, не оставляем его подзаголовком.
        for block in front:
            decision = by_id[block.block_id]
            if self.looks_like_author_line(block.text) and decision.role in {"subtitle", "unknown", "paragraph"}:
                decision.role = "author"
                decision.confidence = max(decision.confidence, 0.82)
                decision.reason = "Формат полного ФИО/инициалов во вступительной части"

    @staticmethod
    def looks_like_author_line(text: str) -> bool:
        value = re.sub(r"\s+", " ", text).strip(" ,;.")
        if not value or len(value) > 350 or any(ch in value for ch in "?!"):
            return False
        if re.search(r"\b(университет|институт|кафедр|лаборатор|department|university|institute)\b", value, re.I):
            return False

        parts = [p.strip() for p in re.split(r"\s*[;,]\s*|\s+и\s+|\s+and\s+", value) if p.strip()]
        if len(parts) > 12:
            return False

        initials_pattern = re.compile(
            r"^(?:[А-ЯЁA-Z][а-яёA-Za-z'’\-]+\s+)?[А-ЯЁA-Z]\.\s*[А-ЯЁA-Z]\.?$|"
            r"^[А-ЯЁA-Z][а-яёA-Za-z'’\-]+\s+[А-ЯЁA-Z]\.\s*[А-ЯЁA-Z]\.?$"
        )
        full_name_pattern = re.compile(
            r"^[А-ЯЁA-Z][а-яёA-Za-z'’\-]+(?:\s+[А-ЯЁA-Z][а-яёA-Za-z'’\-]+){1,3}$"
        )
        compact_initials = re.compile(r"^[А-ЯЁA-Z][а-яёA-Za-z'’\-]+\s+[А-ЯЁA-Z]\.[А-ЯЁA-Z]\.?$")

        matches = 0
        for part in parts:
            part = re.sub(r"\d+$", "", part).strip()
            if initials_pattern.match(part) or full_name_pattern.match(part) or compact_initials.match(part):
                matches += 1
        return matches >= 1 and matches == len(parts)

    @staticmethod
    def _role_for_level(level: int) -> SemanticRole:
        if level <= 1:
            return "section"
        if level == 2:
            return "subsection"
        return "subsubsection"

    @staticmethod
    def _without_number(text: str) -> str:
        return re.sub(r"^\s*\d+(?:\.\d+)*\.?\s*", "", text).strip()

    @staticmethod
    def _clean(text: str) -> str:
        return re.sub(r"\s+", " ", text or "").strip()
