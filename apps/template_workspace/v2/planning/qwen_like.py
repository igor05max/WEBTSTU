from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from apps.template_workspace.v2.models.document_info import DocumentReport
from apps.template_workspace.v2.models.template_profile import ArticleStructure, TemplateProfile


@dataclass
class PlanningResult:
    """High-level document decisions, intentionally independent from OOXML mutation.

    The payload is deliberately JSON-shaped so a real Qwen provider can replace the
    deterministic simulator without changing SafeWordEditor.  The planner never
    writes document text or XML; it only describes structure/layout intent.
    """

    provider: str = "qwen-like-local-simulator"
    front: dict[str, Any] = field(default_factory=dict)
    flow: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "front": self.front,
            "flow": self.flow,
            "warnings": list(self.warnings),
        }


class QwenLikePlanningEngine:
    """Deterministic stand-in for the future server-side Qwen structure planner.

    A real provider can be injected later.  It receives a compact semantic snapshot
    and must return the same JSON contract.  During local regression testing this
    class performs the same *kind* of reasoning with conservative heuristics.
    """

    def __init__(self, provider: Callable[[dict[str, Any]], dict[str, Any]] | None = None):
        self.provider = provider

    def plan(
        self,
        *,
        article_report: DocumentReport,
        template_report: DocumentReport,
        article_structure: ArticleStructure,
        template_profile: TemplateProfile,
    ) -> PlanningResult:
        snapshot = self._snapshot(article_report, template_report, article_structure, template_profile)
        local = self._local_plan(snapshot)
        if self.provider is None:
            return local
        try:
            patch = self.provider(snapshot) or {}
            return self._merge_provider(local, patch)
        except Exception as exc:
            local.warnings.append(f"Qwen planning provider skipped: {exc}")
            return local

    def _snapshot(self, article_report, template_report, article_structure, template_profile) -> dict[str, Any]:
        by_id = {b["id"]: b for b in article_structure.blocks}
        template_roles = template_profile.roles
        return {
            "article": {
                "blocks": [
                    {
                        "id": b.id,
                        "kind": b.kind,
                        "text": b.text_preview,
                        "role": by_id.get(b.id, {}).get("detected_role"),
                        "zone": by_id.get(b.id, {}).get("zone"),
                    }
                    for b in article_report.flow
                ],
                "tables": [
                    {
                        "id": t.id,
                        "classification": t.classification,
                        "rows": t.row_count,
                        "cols": t.logical_column_count,
                        "grid": list(t.grid),
                    }
                    for t in article_report.tables
                ],
            },
            "template": {
                "front_sequence": list(template_profile.front_role_sequence),
                "front_language_order": list(template_profile.front_language_order),
                "roles": {
                    role: {
                        "examples_count": profile.examples_count,
                        "paragraph": profile.typical_paragraph_formatting,
                        "run": profile.typical_run_formatting,
                    }
                    for role, profile in template_roles.items()
                },
                "paragraphs": [
                    {"id": p.id, "text": p.normalized_text, "style": p.style_id}
                    for p in template_report.paragraphs if p.normalized_text
                ],
                "layout": template_profile.to_dict().get("layout", {}),
            },
        }

    def _local_plan(self, snapshot: dict[str, Any]) -> PlanningResult:
        template_paragraphs = snapshot["template"]["paragraphs"]
        article_blocks = snapshot["article"]["blocks"]

        # Template front-matter grammar.  We reason over the entire first zone rather
        # than treating each paragraph independently, which is the important Qwen-like
        # behaviour needed for unusual journal headers.
        metadata_row = False
        citation_chars = []
        for p in template_paragraphs[:40]:
            text = p["text"]
            low = text.casefold()
            if ("удк" in low or "udc" in low) and "doi" in low:
                metadata_row = True
            if low.startswith("for citation") or low.startswith("для цитирования"):
                citation_chars.append(len(text))

        # Estimate how many lines the journal normally reserves for citation.  This
        # is only used when ARTICLE has an explicit editorial placeholder instead of
        # final citation metadata, so later sections do not jump upward by 2-3 lines.
        citation_expected_lines = 0
        if citation_chars:
            citation_expected_lines = max(1, min(4, math.ceil(max(citation_chars) / 105)))

        has_placeholder_citation = any(
            b.get("role") == "citation" and _is_placeholder_citation(b.get("text") or "")
            for b in article_blocks
        )

        # Large figure policy.  Figures that span both columns are visually much more
        # stable when started on a fresh page/column band than when squeezed after a
        # few prose lines.  Data tables are allowed to flow naturally.
        figure_tables = [t for t in snapshot["article"]["tables"] if t["classification"] == "FIGURE_CONTAINER"]
        large_figure_ids = []
        for t in figure_tables:
            width = sum(int(x or 0) for x in t.get("grid") or [])
            if t["cols"] >= 3 or width >= 7000:
                large_figure_ids.append(t["id"])

        return PlanningResult(
            front={
                "metadata_row": metadata_row,
                "citation_expected_lines": citation_expected_lines,
                "reserve_placeholder_citation_slot": bool(has_placeholder_citation and citation_expected_lines >= 2),
                "role_sequence": list(snapshot["template"]["front_sequence"]),
                "language_order": list(snapshot["template"]["front_language_order"]),
            },
            flow={
                "page_break_before_large_full_width_figures": False,
                "large_figure_block_ids": large_figure_ids,
                "keep_figure_containers_atomic": True,
                "allow_safe_prose_relocation": True,
                "float_compact_tables_forward": True,
                "max_float_body_blocks": 2,
                "max_float_chars": 1800,
                "max_relocation_chars": 900,
                "min_relocation_chars": 120,
            },
        )

    def _merge_provider(self, local: PlanningResult, patch: dict[str, Any]) -> PlanningResult:
        # Strictly whitelist planner sections.  The provider cannot inject text or
        # arbitrary editor operations.
        front = dict(local.front)
        flow = dict(local.flow)
        if isinstance(patch.get("front"), dict):
            for key in front:
                if key in patch["front"]:
                    front[key] = patch["front"][key]
        if isinstance(patch.get("flow"), dict):
            for key in flow:
                if key in patch["flow"]:
                    flow[key] = patch["flow"][key]
        return PlanningResult(provider="qwen-like-local+provider", front=front, flow=flow, warnings=list(local.warnings))


def _is_placeholder_citation(text: str) -> bool:
    low = re.sub(r"\s+", " ", text or "").casefold()
    return any(x in low for x in (
        "данные предоставляются редакцией",
        "provided by the editorial office",
        "provided by editorial",
        "to be provided by",
    ))
