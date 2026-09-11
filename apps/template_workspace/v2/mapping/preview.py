from __future__ import annotations

from collections import Counter
from typing import Any

from apps.template_workspace.v2.models.template_profile import ArticleStructure, MappingPreview, TemplateProfile


class RoleMatcher:
    """Builds a non-mutating mapping preview from independent article/template analysis."""

    def build_preview(self, article: ArticleStructure, template: TemplateProfile, *, review_threshold: float = 0.74) -> MappingPreview:
        mappings: list[dict[str, Any]] = []
        for block in article.blocks:
            if block["kind"] != "paragraph":
                mappings.append(self._object_mapping(block, template))
                continue
            role = block.get("detected_role") or "body"
            confidence = float(block.get("confidence") or 0)
            template_role = role if role in template.roles else ("body" if "body" in template.roles else None)
            needs_review = bool(block.get("needs_review")) or confidence < review_threshold or template_role is None
            mappings.append(
                {
                    "article_block": block["id"],
                    "detected_role": role,
                    "template_role": template_role,
                    "confidence": round(confidence, 3),
                    "planned_action": "apply_format" if template_role else "preserve",
                    "needs_review": needs_review,
                    "reason": "role matched by name" if template_role == role else "role missing in template; fallback to body/preserve",
                }
            )
        counts = Counter(item["planned_action"] for item in mappings)
        return MappingPreview(
            source_path=article.source_path,
            template_path=template.source_path,
            mappings=mappings,
            summary={
                "total_mappings": len(mappings),
                "needs_review": sum(1 for item in mappings if item.get("needs_review")),
                "planned_actions": dict(sorted(counts.items())),
                "template_roles_available": sorted(template.roles),
            },
            warnings=[
                "Mapping preview does not compare ARTICLE and TEMPLATE as two versions of the same text.",
                "No DOCX edits are applied at this stage.",
            ],
        )

    @staticmethod
    def _object_mapping(block: dict[str, Any], template: TemplateProfile) -> dict[str, Any]:
        kind = block["kind"]
        if kind == "table":
            template_role = "table_caption" if "table_caption" in template.roles else None
            action = "preserve_table_object"
        else:
            template_role = None
            action = "preserve_object"
        return {
            "article_block": block["id"],
            "detected_role": kind,
            "template_role": template_role,
            "confidence": 1.0,
            "planned_action": action,
            "needs_review": False,
            "reason": "non-paragraph OOXML object is preserved for SafeWordEditor",
        }

