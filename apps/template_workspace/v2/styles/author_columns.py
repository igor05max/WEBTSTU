"""Place short, explicitly bilingual biography groups alongside one another."""
from lxml import etree
from ..ooxml.namespaces import NS, qn
from .jamt import load_style


def arrange_author_columns(body, metadata):
    headings = [p for p in body if metadata.get(p) and metadata[p].role == 'author_information']
    if len(headings) != 2 or {metadata[p].language for p in headings} != {'en','ru'}:
        return 0
    groups = {}
    for heading in headings:
        paragraphs = []
        node = heading.getnext()
        while node is not None and metadata.get(node) and metadata[node].role == 'author_bio':
            paragraphs.append(node); node = node.getnext()
        groups[metadata[heading].language] = paragraphs
    if not all(groups.values()): return 0
    nodes = headings + groups['en'] + groups['ru']
    if any(p.tag != qn('w:p') or p.xpath('.//w:drawing|.//w:pict|.//w:object|./w:pPr/w:sectPr', namespaces=NS) for p in nodes):
        return 0
    # Retain the flow for long/ambiguous sections instead of introducing a tall
    # unbreakable PDF box or guessing correspondence between individual authors.
    if sum(len(''.join(p.xpath('.//w:t/text()', namespaces=NS))) for p in nodes) > 1800:
        return 0
    between = list(body)[min(body.index(p) for p in nodes):max(body.index(p) for p in nodes)+1]
    if set(between) != set(nodes): return 0
    english = next(p for p in headings if metadata[p].language == 'en')
    russian = next(p for p in headings if metadata[p].language == 'ru')
    first = min(nodes, key=body.index)
    if english is not first: body.remove(english); first.addprevious(english)
    # A layout tab separates the original labels; punctuation/text is untouched.
    etree.SubElement(etree.SubElement(english, qn('w:r')), qn('w:tab'))
    for child in list(russian):
        if child.tag != qn('w:pPr'): english.append(child)
    body.remove(russian); metadata.pop(russian, None)
    style = load_style(); width = style['roles']['editorial_metadata']['right_tab']; gap = style['column_gap_twips']
    widths = [(width-gap)//2, gap, width-gap-(width-gap)//2]
    table = etree.Element(qn('w:tbl'))
    def prop(parent,name,**attrs):
        item = etree.SubElement(parent,qn('w:'+name))
        for k,v in attrs.items(): item.set(qn('w:'+k),str(v))
        return item
    pr=prop(table,'tblPr');prop(pr,'tblW',w=width,type='dxa');prop(pr,'jc',val='center');prop(pr,'tblLayout',type='fixed')
    borders=prop(pr,'tblBorders')
    for side in ('top','left','bottom','right','insideH','insideV'): prop(borders,side,val='nil')
    grid=prop(table,'tblGrid')
    for w in widths: prop(grid,'gridCol',w=w)
    row=prop(table,'tr')
    for col,w in enumerate(widths):
        cell=prop(row,'tc'); cp=prop(cell,'tcPr');prop(cp,'tcW',w=w,type='dxa');prop(cp,'vAlign',val='top')
        margins=prop(cp,'tcMar')
        for side in ('top','left','bottom','right'):prop(margins,side,w=0,type='dxa')
        if col==1:prop(cell,'p')
        else:
            for paragraph in groups['en' if col==0 else 'ru']:cell.append(paragraph)
    english.addnext(table)
    return 1
