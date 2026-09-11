from __future__ import annotations

from collections import Counter
from typing import Any

from apps.template_workspace.v2.models.template_profile import ArticleStructure, MappingPreview, TemplateProfile


class RoleMatcher:
    """Builds a non-mutating, safety-first mapping preview.

    No semantic role is silently downgraded to BODY.  Exact or explicitly
    synthesised template roles are used; otherwise source formatting is preserved
    and the block is marked for review.
    """

    def build_preview(self, article: ArticleStructure, template: TemplateProfile, *, review_threshold: float = 0.74) -> MappingPreview:
        mappings: list[dict[str, Any]] = []
        for block in article.blocks:
            if block["kind"] != "paragraph":
                mappings.append(self._object_mapping(block, template))
                continue

            text = (block.get("text_preview") or "").strip()
            role = str(block.get("detected_role") or "body")
            confidence = float(block.get("confidence") or 0)

            if not text:
                mappings.append(
                    {
                        "article_block": block["id"],
                        "detected_role": role,
                        "template_role": None,
                        "confidence": round(confidence, 3),
                        "planned_action": "preserve_blank",
                        "needs_review": False,
                        "reason": "blank paragraph is preserved; layout layer may handle it later",
                    }
                )
                continue

            profile = template.roles.get(role)
            if profile is not None:
                needs_review = bool(block.get("needs_review")) or confidence < review_threshold
                mappings.append(
                    {
                        "article_block": block["id"],
                        "detected_role": role,
                        "template_role": role,
                        "confidence": round(confidence, 3),
                        "planned_action": "apply_format",
                        "needs_review": needs_review,
                        "template_role_is_synthetic": bool(profile.synthetic),
                        "reason": "exact role matched by name" + (" (synthetic profile)" if profile.synthetic else ""),
                    }
                )
                continue

            mappings.append(
                {
                    "article_block": block["id"],
                    "detected_role": role,
                    "template_role": None,
                    "confidence": round(confidence, 3),
                    "planned_action": "preserve_formatting",
                    "needs_review": True,
                    "reason": "role is absent in TEMPLATE profile; unsafe BODY fallback is forbidden",
                }
            )

        counts = Counter(item["planned_action"] for item in mappings)
        unsafe_fallbacks = sum(1 for item in mappings if item.get("reason", "").startswith("role is absent") and item.get("planned_action") == "apply_format")
        return MappingPreview(
            source_path=article.source_path,
            template_path=template.source_path,
            mappings=mappings,
            summary={
                "total_mappings": len(mappings),
                "needs_review": sum(1 for item in mappings if item.get("needs_review")),
                "planned_actions": dict(sorted(counts.items())),
                "template_roles_available": sorted(template.roles),
                "unsafe_body_fallback_count": unsafe_fallbacks,
            },
            warnings=[
                "ARTICLE and TEMPLATE are analysed independently; no content diff is used in user mode.",
                "Missing roles preserve source formatting instead of falling back to BODY.",
            ],
        )

    @staticmethod
    def _object_mapping(block: dict[str, Any], template: TemplateProfile) -> dict[str, Any]:
        kind = block["kind"]
        if kind == "table":
            action = "preserve_table_object"
        else:
            action = "preserve_object"
        return {
            "article_block": block["id"],
            "detected_role": kind,
            "template_role": None,
            "confidence": 1.0,
            "planned_action": action,
            "needs_review": False,
            "reason": "native OOXML object is preserved by SafeWordEditor",
        }
