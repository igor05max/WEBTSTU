"""JAMT's first-page identifier row, without rewriting identifier text.

Twenty independently measured articles put UDC at the left margin and DOI at
the right margin on one baseline. A bibliography DOI is not this field.
"""
from copy import deepcopy
import re

from lxml import etree

from ..ooxml.namespaces import NS, qn


DOI = r'(?:DOI\b\s*:?|https?://(?:dx\.)?doi\.org/)'


def identifier_kind(text):
    text = text.strip()
    if re.match(r'^(?:УДК|UDC)\b', text, re.I):
        return 'pair' if re.search(DOI, text, re.I) else 'udc'
    if re.match('^' + DOI, text, re.I):
        return 'doi'
    return ''


def _text(node):
    return ''.join(node.xpath('.//w:t/text()', namespaces=NS))


def _plain_paragraph(node):
    return node.tag == qn('w:p') and not node.xpath(
        './/w:drawing|.//w:pict|.//w:object|.//m:oMath|./w:pPr/w:sectPr', namespaces=NS)


def _separator(node):
    """Add a layout tab before DOI, retaining every source text character/run."""
    text = _text(node)
    match = re.search(DOI, text, re.I)
    if not match:
        return False
    position, seen, tabs = match.start(), 0, False
    for item in list(node.iter()):
        if item.tag == qn('w:tab') and item.getparent().tag == qn('w:r') and seen <= position:
            tabs = True
        if item.tag != qn('w:t'):
            continue
        value = item.text or ''
        if seen <= position < seen + len(value):
            if tabs:
                return False
            offset = position - seen
            run = item.getparent()
            if run.tag != qn('w:r'):
                return False
            tab = etree.Element(qn('w:tab'))
            if offset:
                following = deepcopy(item)
                item.text, following.text = value[:offset], value[offset:]
                item.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
                item.addnext(tab); tab.addnext(following)
            else:
                item.addprevious(tab)
            return True
        seen += len(value)
    return False


def arrange_identifiers(body, meta_by_node):
    """Join an unambiguous pair of front fields; never merge prose or duplicates."""
    fields = [p for p in body if _plain_paragraph(p)
              and meta_by_node.get(p) and meta_by_node[p].zone == 'front_matter'
              and meta_by_node[p].role == 'editorial_metadata' and identifier_kind(_text(p))]
    kinds = {p: identifier_kind(_text(p)) for p in fields}
    changed = 0
    if list(kinds.values()).count('udc') == 1 and list(kinds.values()).count('doi') == 1 and 'pair' not in kinds.values():
        udc = next(p for p in fields if kinds[p] == 'udc')
        doi = next(p for p in fields if kinds[p] == 'doi')
        # Keep the pair at the earliest identifier location, below rubric/issue.
        anchor = min((udc, doi), key=body.index)
        if anchor is doi:
            body.remove(udc); doi.addprevious(udc)
        run = etree.SubElement(udc, qn('w:r'))
        etree.SubElement(run, qn('w:tab'))
        for child in list(doi):
            if child.tag != qn('w:pPr'):
                udc.append(child)
        body.remove(doi); meta_by_node.pop(doi, None)
        meta_by_node[udc].subtype = 'bibliographic_id'
        changed += 1
        fields.remove(doi)
    for p in fields:
        if identifier_kind(_text(p)) == 'pair':
            changed += int(_separator(p))
    return changed


def repair_existing_identifiers(root, report, structure, style):
    """Repair alignment in the conservative preservation path, idempotently.

    Already correctly typeset references remain byte-identical. Existing
    paragraph/section/object boundaries are retained on this path.
    """
    decisions = {b['id']: b for b in structure.blocks}
    children = list(root.find('w:body', NS))
    changes = []
    width = style['roles']['editorial_metadata']['right_tab']
    for p in report.paragraphs:
        decision = decisions.get(p.id, {})
        if decision.get('zone') != 'front_matter' or decision.get('detected_role') != 'editorial_metadata':
            continue
        kind = identifier_kind(p.text)
        if kind not in {'doi', 'pair'}:
            continue
        node = children[int(p.id.split('_')[-1]) - 1]
        if not _plain_paragraph(node):
            continue
        changed = _separator(node) if kind == 'pair' else False
        properties = node.find('w:pPr', NS)
        if properties is None:
            properties = etree.Element(qn('w:pPr')); node.insert(0, properties)
        alignment = p.effective_formatting.get('paragraph', {}).get('alignment') or 'left'
        wanted = 'right' if kind == 'doi' else 'left'
        if alignment != wanted and not (kind == 'pair' and alignment == 'both'):
            jc = properties.find('w:jc', NS)
            if jc is None: jc = etree.SubElement(properties, qn('w:jc'))
            jc.set(qn('w:val'), wanted); changed = True
        if kind == 'pair':
            tabs = properties.find('w:tabs', NS)
            correct = tabs is not None and any(t.get(qn('w:val')) == 'right'
                and abs(int(t.get(qn('w:pos'), '0')) - width) <= 20 for t in tabs)
            if not correct:
                if tabs is None: tabs = etree.SubElement(properties, qn('w:tabs'))
                for t in list(tabs): tabs.remove(t)
                tab = etree.SubElement(tabs, qn('w:tab'))
                tab.set(qn('w:val'), 'right'); tab.set(qn('w:pos'), str(width))
                changed = True
        if changed: changes.append(p.id)
    return changes
