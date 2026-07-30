from __future__ import annotations

import re


_LATEX_REPLACEMENTS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
    "⌀": r"\diameter{}",
}
_LONG_BREAKABLE_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:=+\-]{19,}")
_ESCAPED_BREAKABLE_TOKEN = re.compile(
    r"(?:[A-Za-z0-9]|\\_|[./:=+\-]){12,}"
)


def latex_break_long_tokens(value: str) -> str:
    def add_breaks(match: re.Match[str]) -> str:
        token = match.group(0)
        token = token.replace(r"\_", r"\_\allowbreak{}")
        token = token.replace("/", r"/\allowbreak{}")
        token = token.replace(".", r".\allowbreak{}")
        return token.replace("=", r"=\allowbreak{}")

    return _ESCAPED_BREAKABLE_TOKEN.sub(add_breaks, value)


def latex_escape(text: str) -> str:
    parts = re.split(rf"({_LONG_BREAKABLE_TOKEN.pattern})", text)
    escaped: list[str] = []
    for part in parts:
        value = "".join(_LATEX_REPLACEMENTS.get(char, char) for char in part)
        if _LONG_BREAKABLE_TOKEN.fullmatch(part):
            value = value.replace(r"\_", r"\_\allowbreak{}")
            value = value.replace("/", r"/\allowbreak{}")
            value = value.replace(".", r".\allowbreak{}")
            value = value.replace("=", r"=\allowbreak{}")
        escaped.append(value)
    return latex_break_long_tokens("".join(escaped))


def clean_text(text: str) -> str:
    return re.sub(r"[ \t\u00a0]+", " ", text.replace("\r", "")).strip()


def safe_filename(name: str, fallback: str = "asset") -> str:
    value = re.sub(r"[^\w.\-]+", "_", name, flags=re.UNICODE).strip("._")
    return value or fallback
