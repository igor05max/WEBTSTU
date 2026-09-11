from __future__ import annotations

from typing import Any

from lxml import etree

from apps.template_workspace.v2.models.document_info import StyleInfo
from apps.template_workspace.v2.ooxml.namespaces import NS, qn


def clean(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, {}, [], "")}


class EffectiveFormattingResolver:
    """Resolves deterministic effective paragraph/run formatting from OOXML styles."""

    def __init__(self, styles_root: etree._Element | None, styles: list[StyleInfo]):
        self.styles_root = styles_root
        self.styles = {style.style_id: style for style in styles}
        self.doc_defaults = self._doc_defaults(styles_root)

    def paragraph(self, style_id: str | None, direct: dict[str, Any] | None) -> dict[str, Any]:
        paragraph: dict[str, Any] = {}
        run: dict[str, Any] = {}
        paragraph.update(self.doc_defaults.get("paragraph", {}))
        run.update(self.doc_defaults.get("run", {}))
        for style in self._style_chain(style_id):
            paragraph.update(clean(style.paragraph_properties))
            run.update(clean(style.run_properties))
        paragraph.update(clean(direct or {}))
        return {"paragraph": clean(paragraph), "run": clean(run)}

    def run(self, paragraph_style_id: str | None, character_style_id: str | None, direct: dict[str, Any] | None) -> dict[str, Any]:
        resolved = dict(self.paragraph(paragraph_style_id, {}).get("run", {}))
        for style in self._style_chain(character_style_id):
            if style.type == "character":
                resolved.update(clean(style.run_properties))
        resolved.update(clean(direct or {}))
        return clean(resolved)

    def _style_chain(self, style_id: str | None) -> list[StyleInfo]:
        if not style_id:
            return []
        chain: list[StyleInfo] = []
        seen: set[str] = set()
        current = style_id
        while current and current not in seen and current in self.styles:
            seen.add(current)
            style = self.styles[current]
            chain.append(style)
            current = style.based_on
        chain.reverse()
        return chain

    @staticmethod
    def _doc_defaults(root: etree._Element | None) -> dict[str, dict[str, Any]]:
        if root is None:
            return {"paragraph": {}, "run": {}}
        paragraph = root.find(".//w:docDefaults/w:pPrDefault/w:pPr", namespaces=NS)
        run = root.find(".//w:docDefaults/w:rPrDefault/w:rPr", namespaces=NS)
        return {
            "paragraph": _paragraph_defaults(paragraph),
            "run": _run_defaults(run),
        }


def _paragraph_defaults(p_pr: etree._Element | None) -> dict[str, Any]:
    if p_pr is None:
        return {}
    spacing = p_pr.find("w:spacing", namespaces=NS)
    indentation = p_pr.find("w:ind", namespaces=NS)
    alignment = p_pr.find("w:jc", namespaces=NS)
    return clean(
        {
            "alignment": alignment.get(qn("w:val")) if alignment is not None else None,
            "spacing": dict(spacing.attrib) if spacing is not None else {},
            "indentation": dict(indentation.attrib) if indentation is not None else {},
        }
    )


def _run_defaults(r_pr: etree._Element | None) -> dict[str, Any]:
    if r_pr is None:
        return {}
    fonts = r_pr.find("w:rFonts", namespaces=NS)
    size = r_pr.find("w:sz", namespaces=NS)
    size_cs = r_pr.find("w:szCs", namespaces=NS)
    color = r_pr.find("w:color", namespaces=NS)
    return clean(
        {
            "fonts": dict(fonts.attrib) if fonts is not None else {},
            "size": size.get(qn("w:val")) if size is not None else None,
            "size_cs": size_cs.get(qn("w:val")) if size_cs is not None else None,
            "color": color.get(qn("w:val")) if color is not None else None,
        }
    )

