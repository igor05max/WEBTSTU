from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import PurePosixPath

from lxml import etree

from .namespaces import NS


@dataclass(frozen=True)
class Relationship:
    id: str
    type: str
    target: str
    target_mode: str | None
    source_part: str
    resolved_target: str | None

    @property
    def is_external(self) -> bool:
        return (self.target_mode or "").lower() == "external"

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


def relationship_source_part(rels_part: str) -> str:
    if rels_part == "_rels/.rels":
        return ""
    prefix, name = rels_part.rsplit("/_rels/", 1)
    return str(PurePosixPath(prefix) / name.removesuffix(".rels"))


def resolve_relationship_target(source_part: str, target: str, target_mode: str | None) -> str | None:
    if (target_mode or "").lower() == "external":
        return target
    if target.startswith("/"):
        return target.lstrip("/")
    if not source_part:
        return target
    base = PurePosixPath(source_part).parent
    parts: list[str] = []
    for part in str(base / target).split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def parse_relationships(xml: bytes, rels_part: str) -> list[Relationship]:
    root = etree.fromstring(xml)
    source_part = relationship_source_part(rels_part)
    relationships: list[Relationship] = []
    for element in root.findall("pkgrel:Relationship", namespaces=NS):
        target = element.get("Target", "")
        target_mode = element.get("TargetMode")
        relationships.append(
            Relationship(
                id=element.get("Id", ""),
                type=element.get("Type", ""),
                target=target,
                target_mode=target_mode,
                source_part=source_part,
                resolved_target=resolve_relationship_target(source_part, target, target_mode),
            )
        )
    return relationships
