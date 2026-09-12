from __future__ import annotations

from typing import Any

from lxml import etree

from apps.template_workspace.v2.models.document_info import StyleInfo
from apps.template_workspace.v2.ooxml.namespaces import NS, qn


def clean(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, {}, [], "")}


def merge_format(base: dict[str, Any], patch: dict[str, Any]) -> None:
    """OOXML attributes inherit individually (e.g. after does not reset line)."""
    for key, value in clean(patch).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = {**base[key], **value}
        else:
            base[key] = value


class EffectiveFormattingResolver:
    """Resolve Word's visible formatting cascade deterministically.

    Word paragraphs without an explicit ``w:pStyle`` still use the document's
    default paragraph style (usually Normal).  The previous V2 resolver skipped
    that style and therefore reported Calibri/docDefaults for paragraphs that Word
    actually rendered as Times New Roman.  This resolver includes default styles
    and also honours paragraph-style run properties before character/run overrides.
    """

    def __init__(self, styles_root: etree._Element | None, styles: list[StyleInfo]):
        self.styles_root = styles_root
        self.styles = {style.style_id: style for style in styles}
        self.doc_defaults = self._doc_defaults(styles_root)
        self.default_paragraph_style = next((s.style_id for s in styles if s.type == "paragraph" and s.default), None)
        self.default_character_style = next((s.style_id for s in styles if s.type == "character" and s.default), None)

    def paragraph(self, style_id: str | None, direct: dict[str, Any] | None) -> dict[str, Any]:
        paragraph: dict[str, Any] = {"alignment": "left"}
        run: dict[str, Any] = {"size": "20", "bold": False, "italic": False}
        paragraph.update(self.doc_defaults.get("paragraph", {}))
        run.update(self.doc_defaults.get("run", {}))

        resolved_style_id = style_id or self.default_paragraph_style
        for style in self._style_chain(resolved_style_id):
            if style.type != "paragraph":
                continue
            merge_format(paragraph, style.paragraph_properties)
            merge_format(run, style.run_properties)

        direct = clean(direct or {})
        merge_format(paragraph, direct)
        return {"paragraph": clean(paragraph), "run": clean(run)}

    def run(self, paragraph_style_id: str | None, character_style_id: str | None, direct: dict[str, Any] | None) -> dict[str, Any]:
        resolved = dict(self.paragraph(paragraph_style_id, {}).get("run", {}))
        char_style_id = character_style_id or self.default_character_style
        for style in self._style_chain(char_style_id):
            if style.type == "character":
                merge_format(resolved, style.run_properties)
        merge_format(resolved, direct or {})
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
