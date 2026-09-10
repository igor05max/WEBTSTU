from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from lxml import etree

from .relationships import Relationship, parse_relationships


IMPORTANT_EXACT_PARTS = {
    "[Content_Types].xml",
    "_rels/.rels",
    "word/document.xml",
    "word/styles.xml",
    "word/numbering.xml",
    "word/settings.xml",
}

IMPORTANT_PREFIXES = (
    "word/_rels/",
    "word/header",
    "word/footer",
    "word/media/",
    "word/charts/",
    "word/embeddings/",
    "word/theme/",
)


@dataclass(frozen=True)
class PackagePart:
    name: str
    size: int
    compressed_size: int
    sha256: str
    important: bool


class WordPackage:
    """Read-only access to a DOCX as an OOXML package."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        try:
            with ZipFile(self.path) as archive:
                self._parts = {info.filename: info for info in archive.infolist()}
                self._names = tuple(self._parts)
        except (OSError, BadZipFile) as exc:
            raise ValueError(f"Not a readable DOCX package: {self.path}") from exc
        if "word/document.xml" not in self._parts:
            raise ValueError(f"DOCX package has no word/document.xml: {self.path}")

    @property
    def names(self) -> tuple[str, ...]:
        return self._names

    @property
    def package_parts(self) -> list[PackagePart]:
        result: list[PackagePart] = []
        with ZipFile(self.path) as archive:
            for name in self._names:
                info = self._parts[name]
                result.append(
                    PackagePart(
                        name=name,
                        size=info.file_size,
                        compressed_size=info.compress_size,
                        sha256=sha256(archive.read(name)).hexdigest(),
                        important=self.is_important_part(name),
                    )
                )
        return result

    @staticmethod
    def is_important_part(name: str) -> bool:
        return name in IMPORTANT_EXACT_PARTS or name.startswith(IMPORTANT_PREFIXES)

    def exists(self, name: str) -> bool:
        return name in self._parts

    def read(self, name: str) -> bytes:
        with ZipFile(self.path) as archive:
            return archive.read(name)

    def xml(self, name: str) -> etree._Element | None:
        if name not in self._parts:
            return None
        return etree.fromstring(self.read(name))

    @property
    def main_document(self) -> etree._Element:
        root = self.xml("word/document.xml")
        if root is None:
            raise ValueError("DOCX package has no main document")
        return root

    @property
    def styles(self) -> etree._Element | None:
        return self.xml("word/styles.xml")

    @property
    def numbering(self) -> etree._Element | None:
        return self.xml("word/numbering.xml")

    @property
    def settings(self) -> etree._Element | None:
        return self.xml("word/settings.xml")

    @property
    def content_types(self) -> etree._Element | None:
        return self.xml("[Content_Types].xml")

    @property
    def header_parts(self) -> list[str]:
        return sorted(name for name in self._names if name.startswith("word/header") and name.endswith(".xml"))

    @property
    def footer_parts(self) -> list[str]:
        return sorted(name for name in self._names if name.startswith("word/footer") and name.endswith(".xml"))

    @property
    def media_parts(self) -> list[str]:
        return sorted(name for name in self._names if name.startswith("word/media/"))

    @property
    def relationships(self) -> dict[str, list[Relationship]]:
        result: dict[str, list[Relationship]] = {}
        for name in self._names:
            if name == "_rels/.rels" or "/_rels/" in name and name.endswith(".rels"):
                result[name] = parse_relationships(self.read(name), name)
        return result

    def relationships_for(self, source_part: str) -> dict[str, Relationship]:
        if source_part:
            source_path = Path(source_part)
            rels_name = str(source_path.parent / "_rels" / f"{source_path.name}.rels").replace("\\", "/")
        else:
            rels_name = "_rels/.rels"
        return {rel.id: rel for rel in self.relationships.get(rels_name, [])}

    @property
    def document_relationships(self) -> dict[str, Relationship]:
        return self.relationships_for("word/document.xml")

    @property
    def media_hashes(self) -> dict[str, str]:
        with ZipFile(self.path) as archive:
            return {name: sha256(archive.read(name)).hexdigest() for name in self.media_parts}
