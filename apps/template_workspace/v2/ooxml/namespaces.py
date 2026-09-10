from __future__ import annotations

from lxml import etree

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "o": "urn:schemas-microsoft-com:office:office",
    "pkgrel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "v": "urn:schemas-microsoft-com:vml",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
}


def qn(prefix_name: str) -> str:
    prefix, name = prefix_name.split(":", 1)
    return f"{{{NS[prefix]}}}{name}"


def local_name(element: etree._Element) -> str:
    return etree.QName(element).localname


def xml_string(element: etree._Element) -> str:
    return etree.tostring(element, encoding="unicode", with_tail=False)
