from __future__ import annotations

import re

from lxml import etree


_SYMBOLS = {
    "×": r"\times ",
    "·": r"\cdot ",
    "⋅": r"\cdot ",
    "≤": r"\leq ",
    "≥": r"\geq ",
    "≠": r"\neq ",
    "≈": r"\approx ",
    "∞": r"\infty ",
    "±": r"\pm ",
    "−": "-",
    "–": "-",
    "→": r"\to ",
    "⇒": r"\Rightarrow ",
    "↔": r"\leftrightarrow ",
    "∈": r"\in ",
    "∉": r"\notin ",
    "∪": r"\cup ",
    "∩": r"\cap ",
    "⊂": r"\subset ",
    "⊆": r"\subseteq ",
    "∅": r"\varnothing ",
    "∀": r"\forall ",
    "∃": r"\exists ",
    "∑": r"\sum ",
    "∏": r"\prod ",
    "∫": r"\int ",
    "√": r"\sqrt{} ",
    "∥": r"\Vert ",
    "‖": r"\Vert ",
    "α": r"\alpha ",
    "β": r"\beta ",
    "γ": r"\gamma ",
    "δ": r"\delta ",
    "λ": r"\lambda ",
    "μ": r"\mu ",
    "π": r"\pi ",
    "σ": r"\sigma ",
    "φ": r"\varphi ",
    "ω": r"\omega ",
    "|": r"\mid ",
}

_FUNCTIONS = {
    "min": r"\min",
    "max": r"\max",
    "ln": r"\ln",
    "log": r"\log",
    "exp": r"\exp",
    "sin": r"\sin",
    "cos": r"\cos",
    "tan": r"\tan",
    "arccos": r"\arccos",
    "clip": r"\operatorname{clip}",
    "lim": r"\lim",
}


def _local_name(node: etree._Element) -> str:
    return etree.QName(node).localname


def _escape_text(value: str) -> str:
    return (
        value.replace("\\", r"\textbackslash{}")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("%", r"\%")
        .replace("#", r"\#")
        .replace("&", r"\&")
        .replace("_", r"\_")
        .replace("^", r"\textasciicircum{}")
        .replace("~", r"\textasciitilde{}")
    )


def _plain_token_to_latex(token: str) -> str:
    if not token:
        return ""
    lower = token.lower()
    if lower in _FUNCTIONS:
        return _FUNCTIONS[lower]
    if re.fullmatch(r"[A-Za-z]", token) or re.fullmatch(r"\d+(?:[.,]\d+)?", token):
        return token.replace(",", "{,}")
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", token):
        return r"\mathrm{" + _escape_text(token) + "}"
    if re.search(r"[А-Яа-яЁё]", token):
        return r"\text{" + _escape_text(token) + "}"
    return _escape_text(token)


def _text_to_latex(text: str) -> str:
    value = (text or "").replace("\u200b", "").replace("\ufeff", "")
    value = value.replace("\u2002", " ").replace("\u2003", " ").replace("\u2001", " ")
    if not value:
        return ""

    result: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        if not buffer:
            return
        raw = "".join(buffer)
        buffer.clear()
        # Текстовые фразы и идентификаторы обрабатываем отдельно от операторов.
        pieces = re.split(r"(\s+)", raw)
        for piece in pieces:
            if not piece:
                continue
            if piece.isspace():
                result.append(r"\,")
            else:
                result.append(_plain_token_to_latex(piece))

    for char in value:
        if char in _SYMBOLS:
            flush()
            result.append(_SYMBOLS[char])
        elif char in {"{", "}"}:
            flush()
            result.append(r"\{" if char == "{" else r"\}")
        else:
            buffer.append(char)
    flush()
    return "".join(result)


def _children_by_name(node: etree._Element, name: str) -> list[etree._Element]:
    return [child for child in node if _local_name(child) == name]


def _first_child(node: etree._Element, name: str) -> etree._Element | None:
    for child in node:
        if _local_name(child) == name:
            return child
    return None


def _render_group(node: etree._Element | None) -> str:
    if node is None:
        return ""
    return "".join(_render(child) for child in node)


def _property_value(node: etree._Element, prop_name: str, default: str = "") -> str:
    prop = _first_child(node, prop_name)
    if prop is None:
        return default
    for value in prop.attrib.values():
        return value
    return default


def _delimiter(value: str, *, opening: bool) -> str:
    mapping = {
        "{": r"\{",
        "}": r"\}",
        "[": "[",
        "]": "]",
        "(": "(",
        ")": ")",
        "|": r"\lvert " if opening else r"\rvert ",
        "‖": r"\lVert " if opening else r"\rVert ",
        "∥": r"\lVert " if opening else r"\rVert ",
        "": ".",
    }
    return mapping.get(value, value or ".")


def _render(node: etree._Element) -> str:
    name = _local_name(node)

    if name == "t":
        return _text_to_latex(node.text or "")

    if name in {"oMath", "oMathPara", "e", "num", "den", "sub", "sup", "deg", "fName"}:
        return _render_group(node)

    if name in {"r", "box", "groupChr", "limLow", "limUpp", "phant"}:
        return _render_group(node)

    if name == "f":
        numerator = _render_group(_first_child(node, "num"))
        denominator = _render_group(_first_child(node, "den"))
        return rf"\frac{{{numerator}}}{{{denominator}}}"

    if name == "sSup":
        base = _render_group(_first_child(node, "e"))
        sup = _render_group(_first_child(node, "sup"))
        return rf"{{{base}}}^{{{sup}}}"

    if name == "sSub":
        base = _render_group(_first_child(node, "e"))
        sub = _render_group(_first_child(node, "sub"))
        return rf"{{{base}}}_{{{sub}}}"

    if name == "sSubSup":
        base = _render_group(_first_child(node, "e"))
        sub = _render_group(_first_child(node, "sub"))
        sup = _render_group(_first_child(node, "sup"))
        return rf"{{{base}}}_{{{sub}}}^{{{sup}}}"

    if name == "rad":
        base = _render_group(_first_child(node, "e"))
        degree = _render_group(_first_child(node, "deg"))
        if degree:
            return rf"\sqrt[{degree}]{{{base}}}"
        return rf"\sqrt{{{base}}}"

    if name == "d":
        props = _first_child(node, "dPr")
        begin_raw = _property_value(props, "begChr", "(") if props is not None else "("
        end_raw = _property_value(props, "endChr", ")") if props is not None else ")"
        begin = _delimiter(begin_raw, opening=True)
        end = _delimiter(end_raw, opening=False)
        body = "".join(_render_group(child) for child in _children_by_name(node, "e"))
        return rf"\left{begin}{body}\right{end}"

    if name == "nary":
        props = _first_child(node, "naryPr")
        char = _property_value(props, "chr", "∑") if props is not None else "∑"
        operator = _SYMBOLS.get(char, _text_to_latex(char)).strip()
        sub = _render_group(_first_child(node, "sub"))
        sup = _render_group(_first_child(node, "sup"))
        body = _render_group(_first_child(node, "e"))
        limits = (rf"_{{{sub}}}" if sub else "") + (rf"^{{{sup}}}" if sup else "")
        return f"{operator}{limits} {body}"

    if name == "func":
        func_name = _render_group(_first_child(node, "fName"))
        argument = _render_group(_first_child(node, "e"))
        if func_name.startswith("\\"):
            return rf"{func_name}\left({argument}\right)"
        return rf"\operatorname{{{func_name}}}\left({argument}\right)"

    if name == "acc":
        body = _render_group(_first_child(node, "e"))
        props = _first_child(node, "accPr")
        char = _property_value(props, "chr", "^") if props is not None else "^"
        command = {"^": "hat", "¯": "bar", "→": "vec", "~": "tilde"}.get(char, "hat")
        return rf"\{command}{{{body}}}"

    if name == "bar":
        body = _render_group(_first_child(node, "e"))
        return rf"\overline{{{body}}}"

    if name == "m":
        rows: list[str] = []
        for row in _children_by_name(node, "mr"):
            cells = [_render_group(cell) for cell in _children_by_name(row, "e")]
            rows.append(" & ".join(cells))
        return r"\begin{matrix}" + r" \\ ".join(rows) + r"\end{matrix}"

    if name == "eqArr":
        rows = [_render_group(child) for child in _children_by_name(node, "e")]
        return r"\begin{aligned}" + r" \\ ".join(rows) + r"\end{aligned}"

    if name.endswith("Pr") or name in {
        "ctrlPr", "rPr", "argPr", "brk", "sty", "scr", "nor", "lit",
        "aln", "alnScr", "grow", "subHide", "supHide", "degHide",
        "sepChr", "begChr", "endChr", "chr", "pos",
    }:
        return ""

    return _render_group(node)


def omml_to_latex(element: etree._Element) -> str:
    r"""Преобразует распространённые конструкции Office Math в LaTeX.

    Неподдерживаемые контейнеры обходятся рекурсивно. Текстовые фрагменты
    помещаются в безопасные \text/\mathrm-команды, чтобы результат собирался XeLaTeX.
    """
    return _render(element).strip()
