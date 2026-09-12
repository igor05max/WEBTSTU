"""Layout-only handling for code and display equations; never regenerate content."""
import json
import posixpath
import re
from lxml import etree

from apps.template_workspace.v2.ooxml.namespaces import NS, qn
from apps.template_workspace.v2.editor.template_evidence import NativeTemplateFormatting


def visible_code_text(p):
    return ''.join((n.text or '') if n.tag == qn('w:t') else
                   '\n' if n.tag in {qn('w:br'), qn('w:cr')} else
                   '\t' if n.tag == qn('w:tab') else '' for n in p.iter())


def is_code(text, style=''):
    lines = text.strip().splitlines()
    if len(lines) < 2:
        return False
    if re.search(r'code|listing|source.?code|исходный.?код', style, re.I):
        return True
    try:
        return isinstance(json.loads(text), (dict, list))
    except (ValueError, RecursionError):
        pass
    # Incomplete snippets are common in papers. Require several independent
    # syntax clues, not a colon or a model/file name in an ordinary paragraph.
    keys = len(re.findall(r'^\s*["\'][^"\'\n]+["\']\s*:', text, re.M))
    statements = len(re.findall(r'^\s*(?:def |class |import |from \w+ import |return |if .+:|for .+:|const |let |function )', text, re.M))
    return (keys >= 3 and any(c in text for c in '{}[]')) or statements >= 2 or (
        text.strip().startswith('```') and text.strip().endswith('```'))


def code_nodes(body):
    """Recognize both one-paragraph/soft-break and multi-paragraph listings."""
    found = set()
    children = list(body)
    for i, p in enumerate(children):
        if p.tag != qn('w:p') or p in found:
            continue
        text = visible_code_text(p)
        style = p.find('w:pPr/w:pStyle', NS)
        if is_code(text, style.get(qn('w:val'), '') if style is not None else ''):
            found.add(p)
        elif text.strip() in {'{', '['}:
            group = []
            for candidate in children[i:i+120]:
                if candidate.tag != qn('w:p'):
                    break
                group.append(candidate)
                combined = '\n'.join(visible_code_text(n) for n in group)
                try:
                    parsed = json.loads(combined)
                except (ValueError, RecursionError):
                    continue
                if isinstance(parsed, (dict, list)) and len(group) > 1:
                    found.update(group)
                    break
    return found


def legacy_equation_ids(package):
    """Recover equation identity lost by DOC converters, without running OLE."""
    relpart = 'word/_rels/document.xml.rels'
    if relpart not in package.namelist():
        return set()
    found = set()
    for rel in etree.fromstring(package.read(relpart)):
        if not rel.get('Type', '').endswith('/oleObject') or rel.get('TargetMode') == 'External':
            continue
        target = posixpath.normpath(posixpath.join('word', rel.get('Target', '')))
        if not target.startswith('word/embeddings/') or target not in package.namelist():
            continue
        if package.getinfo(target).file_size > 4 * 1024 * 1024:
            continue
        data = package.read(target)
        # Compound-file stream name + producer marker: don't classify arbitrary
        # spreadsheet/chart objects merely from dimensions or an empty ProgID.
        if 'Equation Native'.encode('utf-16le') in data and any(
                tag in data for tag in (b'Equation.3', b'Equation.DSMT4', b'MathType')):
            found.add(rel.get('Id'))
    return found


def is_display_equation(p, equation_ids=None):
    if p.tag != qn('w:p'):
        return False
    math = p.xpath('.//m:oMath | .//o:OLEObject[contains(@ProgID,"Equation")]', namespaces=NS)
    known_legacy = any(n.get(qn('r:id')) in (equation_ids or set())
                       for n in p.xpath('.//o:OLEObject', namespaces=NS))
    if not math and not known_legacy:
        return False
    text = ''.join(p.xpath('.//w:t[not(ancestor::m:oMath)]/text()', namespaces=NS)).strip()
    return not text or bool(re.fullmatch(r'[\[(]?\s*\d+(?:[.\-]\d+)*\s*[\])]?', text))


def prop(parent, name, **attrs):
    node = parent.find('w:'+name, NS)
    if node is None:
        node = etree.SubElement(parent, qn('w:'+name))
    for k, v in attrs.items():
        node.set(qn('w:'+k), str(v))
    return node


def format_code(p, body_profile, apply_format):
    """Use template typography, with semantic whitespace and non-justified lines."""
    if body_profile:
        apply_format(p, 'body', body_profile.typical_paragraph_formatting,
                     body_profile.typical_run_formatting)
    pp = p.find('w:pPr', NS)
    if pp is None:
        pp = etree.Element(qn('w:pPr')); p.insert(0, pp)
    prop(pp, 'jc', val='left')
    ind = prop(pp, 'ind', firstLine=0)
    ind.attrib.pop(qn('w:hanging'), None)
    prop(pp, 'wordWrap', val=1)
    prop(pp, 'suppressAutoHyphens', val=1)
    # Do not chain an arbitrarily long listing to the next page. Small blocks
    # remain intact, larger ones can break between their existing lines.
    prop(pp, 'keepLines', val=int(len(visible_code_text(p).splitlines()) <= 12))
    prop(pp, 'keepNext', val=0)
    for t in p.xpath('.//w:t', namespaces=NS):
        t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
    for r in p.xpath('.//w:r', namespaces=NS):
        rp = r.find('w:rPr', NS)
        if rp is None:
            rp = etree.Element(qn('w:rPr')); r.insert(0, rp)
        prop(rp, 'noProof', val=1)
        # Source syntax colours are not scientific meaning; template text is
        # authoritative. Never remove hyperlink relationships or change tokens.
        color = prop(rp, 'color', val='000000')
        for attr in list(color.attrib):
            if attr != qn('w:val'):
                del color.attrib[attr]


def equation_spacing(template_zip):
    """Read real equation paragraphs/styles, including equation table cells."""
    native = NativeTemplateFormatting(template_zip)
    root = etree.fromstring(template_zip.read('word/document.xml'))
    equation_ids = legacy_equation_ids(template_zip)
    for p in root.xpath('//w:p', namespaces=NS):
        sid = p.find('w:pPr/w:pStyle', NS)
        sid = sid.get(qn('w:val'), '') if sid is not None else ''
        if is_display_equation(p, equation_ids) or (re.search(r'equation|formula|формул', sid, re.I)
                                     and not re.search(r'number|номер', sid, re.I)):
            spacing = native.properties(p).find('w:spacing', NS)
            if spacing is not None:
                return {etree.QName(k).localname:v for k,v in spacing.attrib.items()}
    return {}


def hide_equation_control_fields(body):
    """Keep an already-hidden MathType section field hidden in LibreOffice too.

    Word honours the hidden instruction run; LibreOffice may render the whole
    MACROBUTTON label and evaluate nested SEQ counters using the begin-run font.
    Propagate the existing hidden intent to the field boundary, not normal text.
    No field codes, counters or cached scientific values are deleted or locked.
    """
    count = 0
    for p in body.xpath('.//w:p', namespaces=NS):
        depth, group = 0, []
        for r in p.findall('w:r', NS):
            markers = r.findall('w:fldChar', NS)
            if any(n.get(qn('w:fldCharType')) == 'begin' for n in markers):
                depth += 1
            if depth:
                group.append(r)
            if any(n.get(qn('w:fldCharType')) == 'end' for n in markers) and depth:
                depth -= 1
                if depth:
                    continue
                instruction = ''.join(''.join(n.xpath('./w:instrText/text()', namespaces=NS)) for n in group)
                hidden = any(n.xpath('./w:rPr/w:vanish[not(@w:val="0" or @w:val="false" or @w:val="off")]', namespaces=NS) for n in group)
                visible_result = any(n.findall('w:t', NS) and not n.xpath('./w:rPr/w:vanish[not(@w:val="0" or @w:val="false" or @w:val="off")]', namespaces=NS) for n in group)
                if hidden and not visible_result and re.match(r'\s*MACROBUTTON\s+MTEditEquationSection\d*\b', instruction, re.I):
                    for n in group:
                        rp = n.find('w:rPr', NS)
                        if rp is None:
                            rp = etree.Element(qn('w:rPr')); n.insert(0, rp)
                        prop(rp, 'vanish', val=1)
                    count += 1
                group = []
    return count


def format_display_equation(p, layout, template_spacing=None):
    pp = p.find('w:pPr', NS)
    if pp is None:
        pp = etree.Element(qn('w:pPr')); p.insert(0, pp)
    ind = prop(pp, 'ind', left=layout.body_left_twips,
               right=layout.body_right_twips, firstLine=0)
    ind.attrib.pop(qn('w:hanging'), None)
    prop(pp, 'jc', val='center')
    prop(pp, 'keepLines', val=1)
    prop(pp, 'keepNext', val=0)
    # Auto height is essential for tall fractions / legacy Equation objects.
    spacing = dict(template_spacing or {'before':120,'after':120,'line':240,'lineRule':'auto'})
    if spacing.get('lineRule') == 'exact':
        spacing['lineRule'] = 'atLeast'
    prop(pp, 'spacing', **spacing)
    # m:jc is a layout property; native math structures and tokens stay intact.
    for mp in p.findall('m:oMathPara', NS):
        mpp = mp.find('m:oMathParaPr', NS)
        if mpp is None:
            mpp = etree.Element(qn('m:oMathParaPr')); mp.insert(0, mpp)
        jc = mpp.find('m:jc', NS)
        if jc is None:
            jc = etree.SubElement(mpp, qn('m:jc'))
        jc.set(qn('m:val'), 'center')
    # Authors often align an equation number with six manual tabs. Reusing them
    # with new margins pushes the number outside the page. Tabs are layout, not
    # equation content: replace with exactly centre + right stops, keeping all
    # native equation nodes and number/field text in place.
    number_texts = p.xpath('.//w:t[not(ancestor::m:oMath)]', namespaces=NS)
    number_texts = [t for t in number_texts if (t.text or '').strip()]
    for tab in p.xpath('./w:r/w:tab', namespaces=NS):
        tab.getparent().remove(tab)
    if number_texts:
        prop(pp, 'jc', val='left')
        run = etree.Element(qn('w:r'))
        etree.SubElement(run, qn('w:tab'))
        p.insert(1,run)
        number_texts[0].addprevious(etree.Element(qn('w:tab')))
        tabs = pp.find('w:tabs', NS)
        if tabs is not None:
            pp.remove(tabs)
        tabs = etree.SubElement(pp, qn('w:tabs'))
        stops = [('center',layout.column_width_twips//2), ('right',layout.column_width_twips)]
        for align, pos in stops:
            prop_tab = etree.SubElement(tabs, qn('w:tab'))
            prop_tab.set(qn('w:val'), align)
            prop_tab.set(qn('w:pos'), str(layout.body_left_twips+pos))
