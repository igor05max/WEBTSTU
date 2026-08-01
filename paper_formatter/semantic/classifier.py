from __future__ import annotations

from pathlib import Path

from paper_formatter.config import SemanticSettings
from paper_formatter.semantic.base import SemanticProvider
from paper_formatter.semantic.models import SemanticAnalysis, SemanticBlock, SemanticDecision
from paper_formatter.semantic.rules import RuleSemanticClassifier


class HybridSemanticClassifier:
    """Локальные правила сначала, внедрённый AI только для спорных блоков."""

    def __init__(
        self,
        settings: SemanticSettings,
        cache_dir: Path | None = None,
        provider: SemanticProvider | None = None,
    ) -> None:
        self.settings = settings
        self.rules = RuleSemanticClassifier()
        self.cache_dir = cache_dir
        self.provider = provider

    def analyze_document(
        self,
        blocks: list[SemanticBlock],
        *,
        document_name: str,
    ) -> SemanticAnalysis:
        rule_analysis = self.rules.analyze_document(blocks, document_name=document_name)
        merged = {decision.block_id: decision for decision in rule_analysis.decisions}
        warnings = list(rule_analysis.warnings)

        if not self.settings.enabled or self.provider is None:
            return SemanticAnalysis(
                provider="rules-only",
                decisions=list(merged.values()),
                warnings=warnings,
            )

        candidates = self._select_candidates(blocks, merged)
        if not candidates:
            return SemanticAnalysis(
                provider="rules-only:no-candidates",
                decisions=list(merged.values()),
                warnings=warnings,
            )

        ai_decisions: list[SemanticDecision] = []
        providers: list[str] = []
        for batch in self._batches(candidates, self.settings.max_blocks_per_request):
            result = self.provider.analyze_document(
                batch,
                document_name=document_name,
                context={
                    "rule_confidence_threshold": self.settings.min_rule_confidence,
                    "total_document_blocks": len(blocks),
                },
            )
            providers.append(result.provider)
            warnings.extend(result.warnings)
            ai_decisions.extend(result.decisions)

        for ai in ai_decisions:
            rule = merged.get(ai.block_id)
            if rule is not None and self._should_accept_ai(rule, ai):
                merged[ai.block_id] = ai

        self._enforce_document_invariants(blocks, merged, warnings)
        return SemanticAnalysis(
            provider="hybrid(" + ",".join(dict.fromkeys(providers or ["rules"])) + ")",
            decisions=list(merged.values()),
            warnings=self._unique(warnings),
        )

    def _select_candidates(
        self,
        blocks: list[SemanticBlock],
        decisions: dict[str, SemanticDecision],
    ) -> list[SemanticBlock]:
        non_empty = [block for block in blocks if block.text.strip()]
        front_ids = {block.block_id for block in non_empty[:35]}
        result: list[SemanticBlock] = []
        for block in non_empty:
            decision = decisions.get(block.block_id)
            if decision is None:
                continue
            important = decision.role in {
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
                "references_heading",
                "reference",
                "unknown",
            }
            ambiguous = decision.confidence < self.settings.min_rule_confidence
            if block.block_id in front_ids or important or ambiguous or block.numbered_prefix:
                result.append(block)
        return result

    @staticmethod
    def _batches(items: list[SemanticBlock], size: int) -> list[list[SemanticBlock]]:
        size = max(10, size)
        return [items[index : index + size] for index in range(0, len(items), size)]

    @staticmethod
    def _should_accept_ai(rule: SemanticDecision, ai: SemanticDecision) -> bool:
        if ai.confidence < 0.58:
            return False
        if rule.confidence >= 0.97 and ai.role != rule.role:
            return False
        if ai.role == rule.role:
            return ai.confidence >= rule.confidence - 0.1
        return ai.confidence >= max(0.70, rule.confidence + 0.04)

    @staticmethod
    def _enforce_document_invariants(
        blocks: list[SemanticBlock],
        decisions: dict[str, SemanticDecision],
        warnings: list[str],
    ) -> None:
        ordered = [block for block in blocks if block.block_id in decisions]
        title_items = [
            (block, decisions[block.block_id])
            for block in ordered
            if decisions[block.block_id].role == "title"
        ]
        if len(title_items) > 1:
            title_items.sort(key=lambda pair: (-pair[1].confidence, pair[0].order))
            winner = title_items[0][0].block_id
            for block, decision in title_items[1:]:
                if block.block_id != winner:
                    decision.role = "subtitle" if block.order < 20 else "paragraph"
                    decision.reason += "; понижен из-за единственного основного title"
            warnings.append("Несколько кандидатов title сведены к одному основному названию.")

        for block in ordered:
            decision = decisions[block.block_id]
            if block.is_in_numbered_sequence and decision.role in {
                "section",
                "subsection",
                "subsubsection",
            }:
                decision.role = "list_item"
                decision.heading_level = None
                decision.confidence = max(decision.confidence, 0.9)
                decision.reason += "; последовательность пунктов принудительно сохранена списком"

    @staticmethod
    def _unique(values: list[str]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value))
