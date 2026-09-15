"""Evidence-based layout corrections. Never regenerate scientific content."""
from collections import Counter, defaultdict
import re
from lxml import etree
from apps.template_workspace.v2.ooxml.namespaces import NS, qn
from .template_evidence import NativeTemplateFormatting
from .protected_blocks import prop


FRONT = {'editorial_metadata', 'title', 'author', 'affiliation', 'email', 'abstract', 'keywords', 'citation'}


def text(p):
    return ''.join(p.xpath('.//w:t/text()', namespaces=NS)).strip()


def pprops(p):
    pp = p.find('w:pPr', NS)
    if pp is None:
        pp = etree.Element(qn('w:pPr')); p.insert(0, pp)
    return pp


def blank(p):
    return p.tag == qn('w:p') and not text(p) and not p.xpath(
        './/w:drawing|.//w:pict|.//w:object|.//m:oMath|.//w:fldChar|.//w:instrText|.//w:sectPr|.//w:br|.//w:bookmarkStart|.//w:bookmarkEnd', namespaces=NS)


def front_gap_evidence(package, structure):
    """Measure blank-line geometry by adjacent semantic roles, not journal name."""
    root = etree.fromstring(package.read('word/document.xml'))
    children = list(root.find('w:body', NS))
    roles = {b['id']: b.get('detected_role') for b in structure.blocks}
    native = NativeTemplateFormatting(package)
    gaps = defaultdict(list)
    for i, p in enumerate(children):
        role = roles.get(f'block_{i+1:04d}')
        if role not in FRONT:
            continue
        j, height = i+1, 0
        while j < len(children) and blank(children[j]):
            bp = children[j]
            spacing = native.properties(bp).find('w:spacing', NS)
            rp = native.properties(bp, 'rPr')
            # Empty paragraph marks often carry their own font size.
            sz = bp.find('w:pPr/w:rPr/w:sz', NS)
            if sz is None:
                sz = rp.find('w:sz', NS)
            size = int(sz.get(qn('w:val'), '22')) if sz is not None else 22
            line = int(spacing.get(qn('w:line'), '240'))
            line_height = round(size*11.5*line/240) if spacing.get(qn('w:lineRule'), 'auto') == 'auto' else line
            height += line_height + max(int(spacing.get(qn('w:before'), '0')), int(spacing.get(qn('w:after'), '0')))
            j += 1
        following = roles.get(f'block_{j+1:04d}')
        if following in FRONT and role != following and height:
            gaps[role+'>'+following].append(min(height, 720))
    return {key: sorted(values)[len(values)//2] for key, values in gaps.items()}


def apply_front_gaps(body, metadata, gaps):
    changed = 0
    children = list(body)
    for i, p in enumerate(children):
        meta = metadata.get(p)
        if not meta or meta.role not in FRONT:
            continue
        j = i+1
        while j < len(children) and blank(children[j]):
            j += 1
        if j >= len(children):
            continue
        following = metadata.get(children[j])
        if not following or following.role not in FRONT:
            continue
        gap = gaps.get(meta.role+'>'+following.role)
        if not gap:
            continue
        # Replace retained source blank lines with an explicit, measured gap.
        # Do not remove bookmarks/fields/section breaks (blank() excludes them).
        for unused in children[i+1:j]:
            if unused.getparent() is body:
                body.remove(unused)
        spacing = prop(pprops(p), 'spacing')
        before = children[j].find('w:pPr/w:spacing', NS)
        already = max(int(spacing.get(qn('w:after'), '0')),
                      int(before.get(qn('w:before'), '0')) if before is not None else 0)
        prop(pprops(p), 'spacing', after=max(gap, already), afterAutospacing=0)
        prop(pprops(p), 'contextualSpacing', val=0)
        if meta.role in {'title', 'author', 'affiliation'}:
            prop(pprops(p), 'keepNext', val=1)
        changed += 1
    return changed


def heading_gap_evidence(package, structure):
    """Blank paragraphs around headings are layout evidence too.

    A short reference article may have no numbered sections. Its References
    heading is the fallback evidence for synthetic numbered-heading profiles.
    Never impose the same centimeter/point value on every journal.
    """
    root=etree.fromstring(package.read('word/document.xml'))
    children=list(root.find('w:body',NS))
    native=NativeTemplateFormatting(package)
    roles={b['id']:b.get('detected_role') for b in structure.blocks}
    observed=defaultdict(lambda: defaultdict(list))
    for i,p in enumerate(children):
        role=roles.get(f'block_{i+1:04d}')
        if role not in {'heading_1','heading_2','heading_3','references_heading'}:
            continue
        for direction,offset in [('before',-1),('after',1)]:
            if 0 <= i+offset < len(children) and blank(children[i+offset]):
                bp=children[i+offset]
                size=bp.find('w:pPr/w:rPr/w:sz',NS)
                if size is None:
                    size=native.properties(bp,'rPr').find('w:sz',NS)
                spacing=native.properties(bp).find('w:spacing',NS)
                halfpt=int(size.get(qn('w:val'),'22')) if size is not None else 22
                line=int(spacing.get(qn('w:line'),'240'))
                height=round(halfpt*11.5*line/240) if spacing.get(qn('w:lineRule'),'auto')=='auto' else line
                observed[role][direction].append(min(height,480))
    return {role:{direction:sorted(values)[len(values)//2] for direction,values in evidence.items()} for role,evidence in observed.items()}


def apply_heading_gaps(body, metadata, evidence):
    changed=0
    for p in body.findall('w:p',NS):
        meta=metadata.get(p)
        if not meta or meta.role not in {'heading_1','heading_2','heading_3','references_heading'}:
            continue
        gaps=evidence.get(meta.role, evidence.get('references_heading',{}))
        for direction,gap in gaps.items():
            spacing=prop(pprops(p),'spacing')
            if int(spacing.get(qn('w:'+direction),'0')) < gap:
                prop(pprops(p),'spacing',**{direction:gap,direction+'Autospacing':0})
                changed+=1
        if gaps:
            prop(pprops(p),'contextualSpacing',val=0)
    return changed


def align_standalone_picture(p, layout, *, full_width=False):
    """Clear paragraph/anchor offsets only on real stand-alone images."""
    if text(p) or not p.xpath('.//w:drawing|.//w:pict', namespaces=NS) or p.xpath('.//w:object|.//m:oMath', namespaces=NS):
        return 0
    pp = pprops(p)
    ind = prop(pp, 'ind', left=0 if full_width else layout.body_left_twips,
               right=0 if full_width else layout.body_right_twips, firstLine=0)
    for name in ('hanging', 'firstLineChars', 'leftChars', 'rightChars', 'hangingChars'):
        ind.attrib.pop(qn('w:'+name), None)
    prop(pp, 'jc', val='center')
    for tab in p.xpath('./w:r/w:tab', namespaces=NS):
        tab.getparent().remove(tab)
    # Turn top-level floating pictures into in-flow objects. This guarantees
    # the picture cannot float before its anchor paragraph or beyond its column.
    for anchor in p.xpath('.//wp:anchor[not(ancestor::w:txbxContent)]', namespaces=NS):
        inline = etree.Element(qn('wp:inline'))
        for name in ('distT', 'distB', 'distL', 'distR'):
            inline.set(name, '0')
        for name in ('extent', 'effectExtent', 'docPr', 'cNvGraphicFramePr'):
            el = anchor.find('wp:'+name, NS)
            if el is not None:
                inline.append(el)
        graphic = anchor.find('a:graphic', NS)
        if graphic is not None:
            inline.append(graphic)
        anchor.getparent().replace(anchor, inline)
    return 1


def scientific_table_rules(table, info):
    """User policy: horizontal rules, centered values, readable text columns."""
    if info is None or info.classification == 'FIGURE_CONTAINER':
        return 0
    rows = table.findall('w:tr', NS)
    if len(rows) < 2 or (len(text(table)) < 240 and re.search(r'\b(?:DOI|УДК|UDC)\b', text(table), re.I)):
        return 0
    pr = table.find('w:tblPr', NS)
    if pr is None:
        pr = etree.Element(qn('w:tblPr')); table.insert(0, pr)
    for border_parent, tag in [(pr, 'tblBorders')] + [(c, 'tcBorders') for c in table.xpath('./w:tr/w:tc/w:tcPr', namespaces=NS)]:
        borders = prop(border_parent, tag)
        for side in ('left', 'right', 'start', 'end', 'insideV'):
            if side in {'start','end'} and borders.find('w:'+side, NS) is None:
                continue  # do not introduce newer-schema logical edges
            side_node = prop(borders, side, val='nil')
            for attr in list(side_node.attrib):
                if attr != qn('w:val'):
                    del side_node.attrib[attr]
    borders = prop(pr, 'tblBorders')
    for side in ('top', 'bottom'):
        el = prop(borders, side)
        if el.get(qn('w:val')) in {None, 'nil', 'none'}:
            prop(borders, side, val='single', sz=4, color='000000')
    # Choose by the whole body column; don't alternate alignment on each row.
    columns = defaultdict(list)
    for row in rows[1:]:
        col = 0
        for cell in row.findall('w:tc', NS):
            columns[col].append(text(cell))
            span = cell.find('w:tcPr/w:gridSpan', NS)
            col += int(span.get(qn('w:val'), '1')) if span is not None else 1
    left_columns = {col for col, values in columns.items() if any(len(v)>32 for v in values)
                    or sum(len(v)>18 and bool(re.search(r'[A-Za-zА-Яа-я]{3}', v)) for v in values) > len(values)/2}
    for ri, row in enumerate(rows):
        col = 0
        for cell in row.findall('w:tc', NS):
            cp = cell.find('w:tcPr', NS)
            if cp is None:
                cp = etree.Element(qn('w:tcPr')); cell.insert(0, cp)
            prop(cp, 'vAlign', val='center')
            cb = prop(cp, 'tcBorders')
            for side in ('left', 'right', 'start', 'end', 'insideV'):
                if side in {'start','end'} and cb.find('w:'+side, NS) is None:
                    continue
                prop(cb, side, val='nil')
            if ri == 0:
                prop(cb, 'bottom', val='single', sz=4, color='000000')
            for p in cell.findall('w:p', NS):
                prop(pprops(p), 'jc', val='left' if ri and col in left_columns else 'center')
                ind = prop(pprops(p), 'ind', left=0, right=0, firstLine=0)
                ind.attrib.pop(qn('w:hanging'), None)
            span = cp.find('w:gridSpan', NS)
            col += int(span.get(qn('w:val'), '1')) if span is not None else 1
    return 1


def math_typography(body, package, body_profile):
    """Copy native-math typography; fall back to body size, never edit OLE bytes."""
    reference = etree.fromstring(package.read('word/document.xml'))
    sizes = reference.xpath('.//m:oMath//w:sz/@w:val', namespaces=NS)
    fonts = reference.xpath('.//m:oMath//w:rFonts/@w:ascii', namespaces=NS)
    profile = body_profile.typical_run_formatting if body_profile else {}
    size = Counter(sizes).most_common(1)[0][0] if sizes else profile.get('size')
    font = Counter(fonts).most_common(1)[0][0] if fonts else (profile.get('fonts') or {}).get(qn('w:ascii'))
    if not size:
        return 0
    changed = 0
    for math in body.xpath('.//m:oMath', namespaces=NS):
        for run in math.xpath('.//m:r', namespaces=NS):
            rp = run.find('w:rPr', NS)
            if rp is None:
                rp = etree.SubElement(run, qn('w:rPr'))
                # CT_R: m:rPr, w:rPr, m:t.
                run.remove(rp); run.insert(1 if run.find('m:rPr', NS) is not None else 0, rp)
            prop(rp, 'sz', val=size); prop(rp, 'szCs', val=size)
            if font:
                rf = prop(rp, 'rFonts', ascii=font, hAnsi=font, cs=font, eastAsia=font)
                for attr in list(rf.attrib):
                    if 'Theme' in attr:
                        del rf.attrib[attr]
        for rp in math.xpath('.//m:ctrlPr/w:rPr', namespaces=NS):
            prop(rp, 'sz', val=size); prop(rp, 'szCs', val=size)
        changed += 1
    return changed


def descriptions_before_figures(body, metadata):
    """Move an intact figure+caption AFTER a nearby first mention, never prose."""
    moved = 0
    for figure in list(body):
        if not figure.xpath('.//w:drawing|.//w:pict', namespaces=NS) or figure.xpath('.//w:object', namespaces=NS):
            continue
        if figure.tag == qn('w:p') and text(figure):
            continue
        if figure.tag == qn('w:tbl') and len(text(figure)) > 80:
            continue
        children = list(body); i = children.index(figure)
        j = i+1
        while j < len(children) and blank(children[j]): j += 1
        if j >= len(children): continue
        cap = metadata.get(children[j])
        if not cap or cap.role != 'figure_caption': continue
        match = re.match(r'^(?:Fig(?:ure)?\.?|Рис(?:унок)?\.?)\s*(\d+)', text(children[j]), re.I)
        if not match: continue
        mention = re.compile(r'(?:\bFig(?:ure)?\.?|\bРис(?:унок)?\.?)\s*'+match[1]+r'(?!\d)', re.I)
        if any(metadata.get(p) and metadata[p].role == 'body' and mention.search(text(p)) for p in children[:i]):
            continue
        end = j+1
        while end < len(children) and metadata.get(children[end]) and metadata[children[end]].role == 'figure_caption': end += 1
        for candidate in children[end:end+8]:
            meta = metadata.get(candidate)
            if blank(candidate): continue
            if not meta or meta.role != 'body': break
            if mention.search(text(candidate)):
                group = children[i:end]
                anchor = candidate
                for node in group:
                    anchor.addnext(node); anchor = node
                moved += 1
                break
    return moved


def wrap_picture_captions(body, metadata, layout, full_width_nodes):
    """Atomic, borderless native row: Word column balancing ignores keepNext.

    Only modest stand-alone figures with an immediately following caption are
    wrapped. Large figures keep their splittable flow. No image/caption is copied
    or rasterized, and all relationships/bookmarks stay in the same story.
    """
    count = 0
    for p in list(body):
        if p.tag != qn('w:p') or text(p) or p.xpath('.//w:object|.//m:oMath', namespaces=NS):
            continue
        holders = p.xpath('.//wp:inline', namespaces=NS)
        if len(holders) != 1:
            continue
        extent = holders[0].find('wp:extent', NS)
        if extent is None or int(extent.get('cy','0')) > 480*12700:
            continue
        children = list(body); i = children.index(p); j = i+1
        while j < len(children) and blank(children[j]): j += 1
        if j >= len(children): continue
        cm = metadata.get(children[j])
        if not cm or cm.role != 'figure_caption': continue
        end = j+1
        while end < len(children) and metadata.get(children[end]) and metadata[children[end]].role == 'figure_caption' and metadata[children[end]].group_id == cm.group_id:
            end += 1
        if sum(len(text(n)) for n in children[j:end]) > 650:
            continue
        width = layout.printable_width_twips if p in full_width_nodes else layout.column_width_twips
        inset = 0 if p in full_width_nodes else layout.body_left_twips
        table = etree.Element(qn('w:tbl'))
        pr = etree.SubElement(table,qn('w:tblPr'))
        prop(pr,'tblW',w=width,type='dxa');prop(pr,'tblLayout',type='fixed')
        prop(pr,'jc',val='left' if inset else 'center')
        prop(pr,'tblInd',w=inset,type='dxa')
        borders=prop(pr,'tblBorders')
        for side in ('top','bottom','left','right','insideH','insideV'):
            prop(borders,side,val='nil')
        margins=prop(pr,'tblCellMar')
        for side in ('top','bottom','left','right'):
            prop(margins,side,w=0,type='dxa')
        grid=etree.SubElement(table,qn('w:tblGrid'));prop(grid,'gridCol',w=width)
        row=etree.SubElement(table,qn('w:tr'));rp=etree.SubElement(row,qn('w:trPr'))
        prop(rp,'cantSplit')
        cell=etree.SubElement(row,qn('w:tc'));cp=etree.SubElement(cell,qn('w:tcPr'))
        prop(cp,'tcW',w=width,type='dxa')
        p.addprevious(table)
        for node in children[i:end]:
            cell.append(node)
            prop(pprops(node),'ind',left=0,right=0,firstLine=0)
        prop(pprops(children[end-1]),'keepNext',val=0)
        count += 1
    return count
