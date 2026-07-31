from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from lxml import etree


MATHML_NS = "http://www.w3.org/1998/Math/MathML"
OMML_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"


class OmmlConversionError(RuntimeError):
    """Raised when a LaTeX formula cannot be converted to native Word OMML."""


def _omml(tag: str, *, text: str | None = None) -> etree._Element:
    element = etree.Element(f"{{{OMML_NS}}}{tag}", nsmap={"m": OMML_NS})
    if text is not None:
        element.text = text
    return element


def _append_argument(parent: etree._Element, name: str, children: list[etree._Element]) -> None:
    argument = _omml(name)
    argument.extend(children)
    parent.append(argument)


def _run(value: str, *, normal: bool = False) -> etree._Element:
    run = _omml("r")
    if normal:
        properties = _omml("rPr")
        properties.append(_omml("nor"))
        run.append(properties)
    text = _omml("t", text=value)
    if value[:1].isspace() or value[-1:].isspace():
        text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    run.append(text)
    return run


def _mathml_children(node: etree._Element) -> list[etree._Element]:
    return [
        rendered
        for child in node
        for rendered in _mathml_to_omml(child)
    ]


def _script(
    name: str,
    node: etree._Element,
    arguments: tuple[str, ...],
) -> list[etree._Element]:
    result = _omml(name)
    children = list(node)
    for index, argument in enumerate(arguments):
        rendered = _mathml_to_omml(children[index]) if index < len(children) else []
        _append_argument(result, argument, rendered)
    return [result]


def _accent(node: etree._Element) -> list[etree._Element]:
    children = list(node)
    if len(children) < 2:
        return _mathml_children(node)
    mark = "".join(children[1].itertext()).strip()
    accent = _omml("acc")
    properties = _omml("accPr")
    char = _omml("chr")
    char.set(f"{{{OMML_NS}}}val", mark or "^")
    properties.append(char)
    accent.append(properties)
    _append_argument(accent, "e", _mathml_to_omml(children[0]))
    return [accent]


def _matrix(node: etree._Element) -> list[etree._Element]:
    matrix = _omml("m")
    for source_row in node:
        if etree.QName(source_row).localname not in {"mtr", "mlabeledtr"}:
            continue
        row = _omml("mr")
        for source_cell in source_row:
            if etree.QName(source_cell).localname != "mtd":
                continue
            _append_argument(row, "e", _mathml_children(source_cell))
        matrix.append(row)
    return [matrix]


def _mathml_to_omml(node: etree._Element) -> list[etree._Element]:
    """Convert the MathML subset emitted by latex2mathml to editable OMML."""

    name = etree.QName(node).localname
    if name in {"math", "mrow", "mstyle", "semantics", "mtd"}:
        return _mathml_children(node)
    if name in {"mi", "mn", "mo", "mtext", "ms"}:
        value = "".join(node.itertext())
        if not value:
            return []
        return [_run(value, normal=name in {"mo", "mtext", "ms"} or len(value) > 1)]
    if name in {"mspace", "mpadded"}:
        return [_run("\u2009", normal=True)] if name == "mspace" else _mathml_children(node)
    if name == "mfrac":
        return _script("f", node, ("num", "den"))
    if name == "msqrt":
        radical = _omml("rad")
        properties = _omml("radPr")
        degree_hidden = _omml("degHide")
        degree_hidden.set(f"{{{OMML_NS}}}val", "1")
        properties.append(degree_hidden)
        radical.append(properties)
        _append_argument(radical, "e", _mathml_children(node))
        return [radical]
    if name == "mroot":
        return _script("rad", node, ("e", "deg"))
    if name == "msub":
        return _script("sSub", node, ("e", "sub"))
    if name == "msup":
        return _script("sSup", node, ("e", "sup"))
    if name == "msubsup":
        return _script("sSubSup", node, ("e", "sub", "sup"))
    if name == "mover" and node.get("accent") == "true":
        return _accent(node)
    if name == "munder":
        return _script("limLow", node, ("e", "lim"))
    if name == "mover":
        return _script("limUpp", node, ("e", "lim"))
    if name == "munderover":
        children = list(node)
        if len(children) >= 3:
            upper = _omml("limUpp")
            lower = _omml("limLow")
            _append_argument(lower, "e", _mathml_to_omml(children[0]))
            _append_argument(lower, "lim", _mathml_to_omml(children[1]))
            _append_argument(upper, "e", [lower])
            _append_argument(upper, "lim", _mathml_to_omml(children[2]))
            return [upper]
        return _mathml_children(node)
    if name == "mtable":
        return _matrix(node)
    if name in {"mtr", "mlabeledtr"}:
        return _mathml_children(node)
    if name == "menclose":
        return _mathml_children(node)
    if name == "annotation":
        return []
    return _mathml_children(node)


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
            transformer = self._transformer()
            if transformer is not None:
                result = transformer(mathml_root)
                root = result.getroot()
            else:
                root = _omml("oMath")
                root.extend(_mathml_to_omml(mathml_root))
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
            from latex2mathml.converter import convert as _convert  # noqa: F401
        except ImportError:
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
    def _transformer(cls) -> etree.XSLT | None:
        stylesheet = cls._find_stylesheet()
        if stylesheet is None:
            return None
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
