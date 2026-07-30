from __future__ import annotations

import mimetypes
import re
from collections import Counter
from pathlib import Path

from paper_formatter.exceptions import UnsupportedInputError
from paper_formatter.models import (
    ArticleIR,
    Asset,
    Author,
    FigureBlock,
    LocalizedText,
    ParagraphBlock,
    SectionBlock,
    SourceTrace,
    TableBlock,
    TextRun,
)
from paper_formatter.parsers.base import SourceParser
from paper_formatter.utils.files import sha256_file


class PdfParser(SourceParser):
    """Ограниченный PDF → ArticleIR с обязательными предупреждениями об уверенности."""

    def __init__(self, source: Path, assets_dir: Path) -> None:
        self.source = Path(source).resolve()
        self.assets_dir = Path(assets_dir)
        self._counter = 0

    def parse(self) -> ArticleIR:
        if self.source.suffix.lower() != ".pdf":
            raise UnsupportedInputError("PdfParser принимает только PDF.")
        try:
            import pymupdf
        except ImportError as exc:
            raise UnsupportedInputError("Для PDF установите PyMuPDF.") from exc

        document = pymupdf.open(self.source)
        article = ArticleIR(semantic_provider="pymupdf-layout")
        metadata = document.metadata or {}
        if metadata.get("title"):
            article.metadata.titles.append(
                LocalizedText(text=metadata["title"].strip(), language=None)
            )
        if metadata.get("author"):
            for name in re.split(r";|\band\b", metadata["author"]):
                if name.strip():
                    article.metadata.authors.append(
                        Author(
                            id=f"author-{len(article.metadata.authors) + 1}",
                            name=name.strip(),
                        )
                    )

        font_sizes: Counter[float] = Counter()
        page_data: list[dict] = []
        for page in document:
            data = page.get_text("dict")
            page_data.append(data)
            for block in data.get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        font_sizes[round(float(span.get("size", 12.0)), 1)] += max(
                            1, len(span.get("text", ""))
                        )
        main_size = font_sizes.most_common(1)[0][0] if font_sizes else 12.0
        title_candidate: tuple[float, str] | None = None
        seen_images: set[int] = set()

        self.assets_dir.mkdir(parents=True, exist_ok=True)
        for page_index, data in enumerate(page_data, start=1):
            for block_index, block in enumerate(data.get("blocks", [])):
                if "lines" not in block:
                    continue
                spans = [
                    span
                    for line in block.get("lines", [])
                    for span in line.get("spans", [])
                    if span.get("text", "").strip()
                ]
                if not spans:
                    continue
                text = " ".join(span["text"].strip() for span in spans)
                text = re.sub(r"\s+", " ", text).strip()
                max_size = max(float(span.get("size", main_size)) for span in spans)
                trace = SourceTrace(
                    format="pdf",
                    location=f"page:{page_index}:block:{block_index}",
                    page=page_index,
                    block_index=block_index,
                    confidence=0.62,
                )
                if (
                    page_index == 1
                    and len(text) < 300
                    and (title_candidate is None or max_size > title_candidate[0])
                ):
                    title_candidate = (max_size, text)
                if max_size >= main_size * 1.18 and len(text) <= 220:
                    level = 1 if max_size >= main_size * 1.45 else 2
                    article.body.append(
                        SectionBlock(
                            id=self._next_id("section"),
                            title=text,
                            level=level,
                            source=trace,
                        )
                    )
                else:
                    article.body.append(
                        ParagraphBlock(
                            id=self._next_id("paragraph"),
                            runs=[TextRun(text=text)],
                            source=trace,
                        )
                    )

            page = document[page_index - 1]
            for image_info in page.get_images(full=True):
                xref = int(image_info[0])
                if xref in seen_images:
                    continue
                seen_images.add(xref)
                try:
                    extracted = document.extract_image(xref)
                except Exception:
                    continue
                extension = "." + extracted.get("ext", "png")
                target = self.assets_dir / f"pdf-image-{len(seen_images)}{extension}"
                target.write_bytes(extracted["image"])
                asset = Asset(
                    id=f"asset-{len(article.assets) + 1}",
                    path=f"assets/{target.name}",
                    media_type=mimetypes.guess_type(target.name)[0],
                    original_name=target.name,
                    sha256=sha256_file(target),
                )
                article.assets.append(asset)
                article.body.append(
                    FigureBlock(
                        id=self._next_id("figure"),
                        asset_id=asset.id,
                        source=SourceTrace(
                            format="pdf",
                            location=f"page:{page_index}:image-xref:{xref}",
                            page=page_index,
                            confidence=0.55,
                        ),
                    )
                )

            try:
                finder = page.find_tables()
                for table_index, table in enumerate(finder.tables):
                    rows = [
                        [str(cell or "") for cell in row]
                        for row in table.extract()
                        if row
                    ]
                    if rows:
                        article.body.append(
                            TableBlock(
                                id=self._next_id("table"),
                                rows=rows,
                                header_rows=1,
                                source=SourceTrace(
                                    format="pdf",
                                    location=f"page:{page_index}:table:{table_index}",
                                    page=page_index,
                                    confidence=0.58,
                                ),
                            )
                        )
            except Exception:
                pass

        if not article.metadata.titles and title_candidate:
            article.metadata.titles.append(
                LocalizedText(text=title_candidate[1], language=None)
            )
            article.body = [
                block
                for block in article.body
                if not (
                    isinstance(block, SectionBlock)
                    and block.source
                    and block.source.page == 1
                    and block.title == title_candidate[1]
                )
            ]
        article.warnings.extend(
            [
                "PDF разобран по визуальному слою; порядок чтения, формулы и таблицы требуют проверки.",
                "Результат PDF-входа нельзя считать эквивалентным исходнику без ручного аудита.",
            ]
        )
        document.close()
        return article

    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}-{self._counter}"
