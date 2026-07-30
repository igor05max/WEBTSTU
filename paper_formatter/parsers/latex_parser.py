from __future__ import annotations

import mimetypes
import re
import shutil
from pathlib import Path

from paper_formatter.exceptions import UnsupportedInputError
from paper_formatter.models import (
    ArticleIR,
    Asset,
    Author,
    CitationOccurrence,
    CrossReference,
    EquationBlock,
    FigureBlock,
    ListItemBlock,
    LocalizedText,
    ParagraphBlock,
    RawBlock,
    ReferenceEntry,
    SectionBlock,
    SourceTrace,
    TableBlock,
    TextRun,
)
from paper_formatter.parsers.base import SourceParser
from paper_formatter.parsers.bibliography_parser import BibliographyParser
from paper_formatter.utils.files import sha256_file


_STRUCTURAL_TOKEN = re.compile(
    r"\\(?P<section>section|subsection|subsubsection|paragraph|subparagraph)\*?"
    r"(?:\s*\[[^\]]*\])?\s*\{"
    r"|\\begin\s*\{(?P<environment>"
    r"abstract|equation\*?|align\*?|gather\*?|multline\*?|displaymath|"
    r"figure\*?|table\*?|itemize|enumerate|thebibliography"
    r")\}",
    re.IGNORECASE,
)
_INLINE_TOKEN = re.compile(
    r"(?P<math>\$(?!\$).*?(?<!\\)\$|\\\(.*?\\\))"
    r"|(?P<cite>\\cite[a-zA-Z*]*(?:\[[^\]]*\])*\{[^}]+\})"
    r"|(?P<ref>\\(?:ref|eqref|autoref)\{[^}]+\})"
    r"|(?P<href>\\href\{[^}]+\}\{[^}]*\})",
    re.DOTALL,
)


class LatexParser(SourceParser):
    def __init__(self, source: Path, assets_dir: Path) -> None:
        self.source = Path(source).resolve()
        self.assets_dir = Path(assets_dir)
        self._counter = 0
        self._asset_counter = 0
        self._article = ArticleIR()

    def parse(self) -> ArticleIR:
        if self.source.suffix.lower() != ".tex":
            raise UnsupportedInputError("LaTeXParser принимает только TEX.")
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        text = self._read_with_inputs(self.source, set())
        preamble, body = self._split_document(text)
        self._parse_metadata(preamble + "\n" + body)
        self._article.custom_latex = self._custom_commands(preamble)
        self._parse_body(body)
        self._load_external_bibliography(text)
        self._article.semantic_provider = "latex-structure"
        if not self._article.metadata.titles:
            self._article.warnings.append("В LaTeX не найдено название статьи.")
        return self._article

    def _read_with_inputs(self, path: Path, seen: set[Path]) -> str:
        path = path.resolve()
        if path in seen:
            return f"% Циклический input пропущен: {path.name}\n"
        if not path.exists():
            return f"% Отсутствующий input: {path.name}\n"
        seen.add(path)
        text = path.read_text(encoding="utf-8", errors="replace")
        text = self._strip_comments(text)

        pattern = re.compile(r"\\(?:input|include)\s*\{([^}]+)\}")

        def replace(match: re.Match[str]) -> str:
            name = match.group(1).strip()
            child = path.parent / name
            if not child.suffix:
                child = child.with_suffix(".tex")
            if not child.exists():
                self._article.warnings.append(f"Не найден LaTeX input: {name}")
                return match.group(0)
            return "\n" + self._read_with_inputs(child, seen) + "\n"

        return pattern.sub(replace, text)

    @staticmethod
    def _strip_comments(text: str) -> str:
        result: list[str] = []
        for line in text.splitlines():
            index = 0
            while True:
                index = line.find("%", index)
                if index < 0:
                    break
                slashes = 0
                cursor = index - 1
                while cursor >= 0 and line[cursor] == "\\":
                    slashes += 1
                    cursor -= 1
                if slashes % 2 == 0:
                    line = line[:index]
                    break
                index += 1
            result.append(line)
        return "\n".join(result)

    @staticmethod
    def _split_document(text: str) -> tuple[str, str]:
        begin = re.search(r"\\begin\s*\{document\}", text)
        end = list(re.finditer(r"\\end\s*\{document\}", text))
        if not begin:
            return "", text
        body_end = end[-1].start() if end else len(text)
        return text[: begin.start()], text[begin.end() : body_end]

    def _parse_metadata(self, text: str) -> None:
        title = self._command_argument(text, "title")
        subtitle = self._command_argument(text, "subtitle")
        author = self._command_argument(text, "author")
        abstract = self._environment_content(text, "abstract")
        keywords = (
            self._command_argument(text, "keywords")
            or self._command_argument(text, "keyword")
            or self._labeled_value(text, ("Ключевые слова", "Keywords"))
        )
        doi = self._command_argument(text, "doi")
        udc = self._command_argument(text, "udc") or self._labeled_value(text, ("УДК", "UDC"))
        if title:
            self._article.metadata.titles.append(
                LocalizedText(language=self._guess_language(title), text=self._clean_text(title))
            )
        if subtitle:
            self._article.metadata.subtitles.append(
                LocalizedText(language=self._guess_language(subtitle), text=self._clean_text(subtitle))
            )
        if author:
            for name in re.split(r"\\and|\\\\|;", author):
                cleaned = self._clean_text(name)
                if cleaned:
                    self._article.metadata.authors.append(
                        Author(id=f"author-{len(self._article.metadata.authors) + 1}", name=cleaned)
                    )
        if abstract:
            cleaned = self._clean_text(abstract)
            if cleaned:
                self._article.metadata.abstracts.append(
                    LocalizedText(language=self._guess_language(cleaned), text=cleaned)
                )
        if keywords:
            self._article.metadata.keywords = [
                self._clean_text(value)
                for value in re.split(r"[,;•]", keywords)
                if self._clean_text(value)
            ]
        self._article.metadata.doi = self._clean_text(doi) if doi else None
        self._article.metadata.udc = self._clean_text(udc) if udc else None

    def _parse_body(self, body: str) -> None:
        cursor = 0
        while True:
            match = _STRUCTURAL_TOKEN.search(body, cursor)
            if not match:
                self._append_plain_text(body[cursor:], cursor)
                break
            self._append_plain_text(body[cursor : match.start()], cursor)
            if match.group("section"):
                brace_start = match.end() - 1
                title, end = self._balanced_argument(body, brace_start)
                level = {
                    "section": 1,
                    "subsection": 2,
                    "subsubsection": 3,
                    "paragraph": 4,
                    "subparagraph": 5,
                }[match.group("section").lower()]
                self._article.body.append(
                    SectionBlock(
                        id=self._next_id("section"),
                        title=self._clean_text(title),
                        level=level,
                        source=self._trace(match.start(), end),
                    )
                )
                cursor = end
                continue

            environment = match.group("environment")
            content, end = self._extract_environment(body, match.start(), environment)
            self._append_environment(environment.lower(), content, match.start(), end)
            cursor = end

    def _append_environment(self, environment: str, content: str, start: int, end: int) -> None:
        base = environment.rstrip("*")
        if base == "abstract":
            return
        if base in {"equation", "align", "gather", "multline", "displaymath"}:
            label = self._command_argument(content, "label")
            latex = re.sub(r"\\label\s*\{[^}]+\}", "", content).strip()
            self._article.body.append(
                EquationBlock(
                    id=self._next_id("equation"),
                    latex=latex,
                    label=label,
                    display=True,
                    source=self._trace(start, end),
                )
            )
            return
        if base == "figure":
            self._append_figure(content, start, end)
            return
        if base == "table":
            self._append_table(content, start, end)
            return
        if base in {"itemize", "enumerate"}:
            self._append_list(content, ordered=base == "enumerate", start=start, end=end)
            return
        if base == "thebibliography":
            self._append_thebibliography(content, start)
            return
        self._article.body.append(
            RawBlock(
                id=self._next_id("raw"),
                format="latex",
                content=content,
                warning=f"Окружение {environment} сохранено без разбора.",
                source=self._trace(start, end),
            )
        )

    def _append_plain_text(self, text: str, absolute_start: int) -> None:
        text = re.sub(r"\\(?:maketitle|tableofcontents|newpage|clearpage)\b", "", text)
        text = re.sub(
            r"(?:\\noindent\s*)?\\textbf\s*\{\s*(?:Keywords|Ключевые слова)\s*:?\s*\}"
            r"[^\n]*",
            "",
            text,
            flags=re.IGNORECASE,
        )
        for match in re.finditer(r"\S(?:.*?\S)?(?=\n\s*\n|\s*\Z)", text, re.DOTALL):
            raw = match.group(0).strip()
            if not raw or raw.startswith(r"\end{"):
                continue
            runs = self._runs_from_latex(raw)
            if not any(run.text or run.math_latex for run in runs):
                continue
            block_id = self._next_id("paragraph")
            self._article.body.append(
                ParagraphBlock(
                    id=block_id,
                    runs=runs,
                    source=self._trace(
                        absolute_start + match.start(), absolute_start + match.end()
                    ),
                )
            )
            for run in runs:
                if run.citation_keys:
                    self._article.citations.append(
                        CitationOccurrence(
                            id=f"citation-{len(self._article.citations) + 1}",
                            keys=run.citation_keys,
                            raw_text=run.text,
                            source_block_id=block_id,
                        )
                    )
                if run.reference_target:
                    self._article.cross_references.append(
                        CrossReference(
                            id=f"xref-{len(self._article.cross_references) + 1}",
                            target_id=run.reference_target,
                            raw_text=run.text,
                            source_block_id=block_id,
                        )
                    )

    def _runs_from_latex(self, raw: str) -> list[TextRun]:
        runs: list[TextRun] = []
        cursor = 0
        for match in _INLINE_TOKEN.finditer(raw):
            if match.start() > cursor:
                text = self._clean_inline_text(
                    raw[cursor : match.start()], leading=bool(runs)
                )
                if text:
                    runs.append(TextRun(text=text))
            token = match.group(0)
            if match.group("math"):
                latex = token[2:-2] if token.startswith(r"\(") else token[1:-1]
                runs.append(TextRun(math_latex=latex.strip()))
            elif match.group("cite"):
                keys_match = re.search(r"\{([^}]+)\}\s*$", token)
                keys = [value.strip() for value in keys_match.group(1).split(",")] if keys_match else []
                runs.append(TextRun(text=f"[{', '.join(keys)}]", citation_keys=keys))
            elif match.group("ref"):
                target = re.search(r"\{([^}]+)\}", token)
                runs.append(
                    TextRun(
                        text=token,
                        reference_target=target.group(1).strip() if target else None,
                    )
                )
            else:
                href = re.fullmatch(r"\\href\{([^}]+)\}\{([^}]*)\}", token, re.DOTALL)
                if href:
                    runs.append(TextRun(text=self._clean_text(href.group(2)), hyperlink=href.group(1)))
            cursor = match.end()
        if cursor < len(raw):
            text = self._clean_inline_text(raw[cursor:], leading=bool(runs))
            if text:
                runs.append(TextRun(text=text))
        return runs

    def _clean_inline_text(self, value: str, *, leading: bool) -> str:
        starts_with_space = bool(re.match(r"[\s~]", value))
        ends_with_space = bool(re.search(r"[\s~]$", value))
        cleaned = self._clean_text(value)
        if not cleaned:
            return " " if leading and (starts_with_space or ends_with_space) else ""
        if leading and starts_with_space:
            cleaned = " " + cleaned
        if ends_with_space:
            cleaned += " "
        return cleaned

    def _append_figure(self, content: str, start: int, end: int) -> None:
        graphic = re.search(
            r"\\includegraphics(?:\[[^\]]*\])?\s*\{([^}]+)\}", content
        )
        if not graphic:
            self._article.body.append(
                RawBlock(
                    id=self._next_id("raw"),
                    format="latex",
                    content=content,
                    warning="Figure без includegraphics сохранена как raw.",
                    source=self._trace(start, end),
                )
            )
            return
        source_asset = self._resolve_graphic(graphic.group(1))
        if source_asset is None:
            self._article.warnings.append(f"Не найден рисунок LaTeX: {graphic.group(1)}")
            return
        asset = self._copy_asset(source_asset)
        self._article.body.append(
            FigureBlock(
                id=self._next_id("figure"),
                asset_id=asset.id,
                caption=self._clean_text(self._command_argument(content, "caption") or "") or None,
                label=self._command_argument(content, "label"),
                source=self._trace(start, end),
            )
        )

    def _append_table(self, content: str, start: int, end: int) -> None:
        tabular = re.search(
            r"\\begin\s*\{tabular\*?\}(?:\{[^}]*\})?(.*?)\\end\s*\{tabular\*?\}",
            content,
            re.DOTALL,
        )
        rows: list[list[str]] = []
        if tabular:
            table_text = re.sub(
                r"\\(?:hline|toprule|midrule|bottomrule|cline\{[^}]+\})", "", tabular.group(1)
            )
            for raw_row in re.split(r"(?<!\\)\\\\", table_text):
                values = [self._clean_text(value) for value in re.split(r"(?<!\\)&", raw_row)]
                if any(values):
                    rows.append(values)
        self._article.body.append(
            TableBlock(
                id=self._next_id("table"),
                rows=rows,
                caption=self._clean_text(self._command_argument(content, "caption") or "") or None,
                label=self._command_argument(content, "label"),
                header_rows=1 if rows else 0,
                source=self._trace(start, end),
            )
        )
        if not rows:
            self._article.warnings.append("Таблица LaTeX сохранена без распознанных ячеек.")

    def _append_list(self, content: str, *, ordered: bool, start: int, end: int) -> None:
        items = re.split(r"\\item(?:\[[^\]]*\])?", content)[1:]
        for index, item in enumerate(items):
            runs = self._runs_from_latex(item.strip())
            self._article.body.append(
                ListItemBlock(
                    id=self._next_id("list"),
                    runs=runs,
                    ordered=ordered,
                    level=0,
                    source=self._trace(start + index, end),
                )
            )

    def _append_thebibliography(self, content: str, start: int) -> None:
        matches = list(re.finditer(r"\\bibitem(?:\[[^\]]*\])?\{([^}]+)\}", content))
        for index, match in enumerate(matches):
            item_end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
            text = self._clean_text(content[match.end() : item_end])
            self._article.references.append(
                ReferenceEntry(
                    id=f"ref-{len(self._article.references) + 1}",
                    citation_key=match.group(1).strip(),
                    text=text,
                )
            )

    def _load_external_bibliography(self, text: str) -> None:
        if self._article.references:
            return
        names: list[str] = []
        for pattern in (
            r"\\bibliography\s*\{([^}]+)\}",
            r"\\addbibresource(?:\[[^\]]*\])?\s*\{([^}]+)\}",
        ):
            for match in re.findall(pattern, text):
                names.extend(value.strip() for value in match.split(",") if value.strip())
        parser = BibliographyParser()
        for name in names:
            path = self.source.parent / name
            if not path.suffix:
                path = path.with_suffix(".bib")
            if path.exists():
                self._article.references.extend(parser.parse(path))
            else:
                self._article.warnings.append(f"Не найден файл библиографии: {name}")

    def _copy_asset(self, source: Path) -> Asset:
        self._asset_counter += 1
        target_name = f"asset-{self._asset_counter}{source.suffix.lower()}"
        target = self.assets_dir / target_name
        shutil.copy2(source, target)
        asset = Asset(
            id=f"asset-{self._asset_counter}",
            path=f"assets/{target_name}",
            media_type=mimetypes.guess_type(source.name)[0],
            original_name=source.name,
            sha256=sha256_file(target),
        )
        self._article.assets.append(asset)
        return asset

    def _resolve_graphic(self, name: str) -> Path | None:
        raw = Path(name.strip())
        candidate = self.source.parent / raw
        if candidate.exists():
            return candidate
        if not raw.suffix:
            for extension in (".pdf", ".png", ".jpg", ".jpeg", ".eps", ".svg"):
                candidate = self.source.parent / raw.with_suffix(extension)
                if candidate.exists():
                    return candidate
        return None

    def _extract_environment(self, text: str, start: int, environment: str) -> tuple[str, int]:
        begin_pattern = re.compile(rf"\\begin\s*\{{{re.escape(environment)}\}}", re.IGNORECASE)
        end_pattern = re.compile(rf"\\end\s*\{{{re.escape(environment)}\}}", re.IGNORECASE)
        begin = begin_pattern.search(text, start)
        if not begin:
            return "", start + 1
        cursor = begin.end()
        depth = 1
        while depth:
            next_begin = begin_pattern.search(text, cursor)
            next_end = end_pattern.search(text, cursor)
            if not next_end:
                self._article.warnings.append(f"Не закрыто окружение {environment}.")
                return text[begin.end() :], len(text)
            if next_begin and next_begin.start() < next_end.start():
                depth += 1
                cursor = next_begin.end()
            else:
                depth -= 1
                if depth == 0:
                    return text[begin.end() : next_end.start()], next_end.end()
                cursor = next_end.end()
        return "", cursor

    @staticmethod
    def _balanced_argument(text: str, brace_start: int) -> tuple[str, int]:
        depth = 0
        escaped = False
        for index in range(brace_start, len(text)):
            char = text[index]
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[brace_start + 1 : index], index + 1
        return text[brace_start + 1 :], len(text)

    def _command_argument(self, text: str, command: str) -> str | None:
        match = re.search(
            rf"\\{re.escape(command)}\*?(?:\s*\[[^\]]*\])?\s*\{{", text
        )
        if not match:
            return None
        value, _ = self._balanced_argument(text, match.end() - 1)
        return value.strip()

    def _environment_content(self, text: str, environment: str) -> str | None:
        match = re.search(rf"\\begin\s*\{{{re.escape(environment)}\}}", text)
        if not match:
            return None
        content, _ = self._extract_environment(text, match.start(), environment)
        return content.strip()

    @staticmethod
    def _labeled_value(text: str, labels: tuple[str, ...]) -> str | None:
        for label in labels:
            match = re.search(
                rf"{re.escape(label)}\s*[:—-]\s*([^\n]+)", text, re.IGNORECASE
            )
            if match:
                return match.group(1).strip()
        return None

    @staticmethod
    def _clean_text(value: str) -> str:
        if not value:
            return ""
        replacements = {
            r"\%": "%",
            r"\_": "_",
            r"\&": "&",
            r"\#": "#",
            r"\$": "$",
            r"\{": "{",
            r"\}": "}",
            "~": " ",
            r"\\": " ",
        }
        for old, new in replacements.items():
            value = value.replace(old, new)
        for _ in range(4):
            updated = re.sub(
                r"\\(?:textbf|textit|emph|underline|textrm|textsf|texttt|mbox|mathrm)"
                r"(?:\[[^\]]*\])?\s*\{([^{}]*)\}",
                r"\1",
                value,
            )
            if updated == value:
                break
            value = updated
        value = re.sub(r"\\(?:label|index)\s*\{[^}]*\}", "", value)
        value = re.sub(r"\\[A-Za-z@]+\*?(?:\[[^\]]*\])?", "", value)
        value = value.replace("{", "").replace("}", "")
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _custom_commands(preamble: str) -> list[str]:
        return [
            match.group(0).strip()
            for match in re.finditer(
                r"\\(?:newcommand|renewcommand|providecommand|DeclareMathOperator)"
                r"\*?\s*\{?\\[A-Za-z@]+\}?(?:\[[^\]]*\])?\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}",
                preamble,
                re.DOTALL,
            )
        ]

    @staticmethod
    def _guess_language(text: str) -> str | None:
        cyrillic = len(re.findall(r"[А-Яа-яЁё]", text))
        latin = len(re.findall(r"[A-Za-z]", text))
        if cyrillic > latin:
            return "ru"
        if latin > cyrillic:
            return "en"
        return None

    def _trace(self, start: int, end: int) -> SourceTrace:
        return SourceTrace(
            format="latex",
            location=f"{self.source.name}:chars:{start}-{end}",
            part=self.source.name,
            confidence=0.95,
        )

    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}-{self._counter}"
