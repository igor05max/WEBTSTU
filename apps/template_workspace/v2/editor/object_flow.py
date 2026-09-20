"""Reflow large legacy display objects without recreating their native content.

Only direct paragraph/run carriers are moved. Fields, inline equations, icons,
hyperlinks and objects inside tables/text boxes remain owned by their container.
"""
from copy import deepcopy
from dataclasses import replace
import re

from lxml import etree
from apps.template_workspace.v2.ooxml.namespaces import NS, qn, local_name
from .layout_fidelity import pprops, prop


def vml_dimensions(shape):
    style = dict(pair.strip().split(':', 1) for pair in shape.get('style', '').split(';') if ':' in pair)
    def points(key):
        match = re.fullmatch(r'\s*([\d.]+)pt\s*', style.get(key, ''))
        return float(match[1]) if match else 0
    return style, points('width'), points('height')


def normalize_vml_canvases(body):
    """Complete a legacy canvas's missing Y scale from its explicit frame.

    Word tolerates a scalar coordsize; LibreOffice can expand the canvas onto
    empty pages and lose its text. Infer nothing unless the frame at coordorigin
    matches the stated width and the physical aspect ratio. All native shapes,
    text, images and relationships stay in place.
    """
    def number(value):
        return float(value) if re.fullmatch(r'-?\d+(?:\.\d+)?', value or '') else None

    changed = 0
    for group in body.xpath('.//v:group[@editas="canvas"]', namespaces=NS):
        width = number(group.get('coordsize'))
        origin = [number(part) for part in re.split(r'[,\s]+', group.get('coordorigin', '').strip())]
        _, physical_width, physical_height = vml_dimensions(group)
        if not width or width <= 0 or len(origin) != 2 or None in origin or min(physical_width, physical_height) <= 0:
            continue
        heights = set()
        for shape in group.findall('v:shape', NS):
            style, _, _ = vml_dimensions(shape)
            x, y, w, h = [number(style.get(key, '').strip()) for key in ('left', 'top', 'width', 'height')]
            if (x, y, w) == (*origin, width) and h and h > 0:
                heights.add(h)
        if len(heights) != 1:
            continue
        height = heights.pop()
        if abs(height / width - physical_height / physical_width) > 0.001:
            continue
        group.set('coordsize', f'{width:g},{height:g}')
        # State Word's wrapping default explicitly for LibreOffice's VML importer.
        # Explicit no-wrap/auto-fit settings remain authoritative.
        for shape in group.findall('v:shape', NS):
            box = shape.find('v:textbox', NS)
            style, _, _ = vml_dimensions(shape)
            if (box is not None and not {'mso-wrap-style', 'mso-wrap-mode'} & style.keys()
                    and not re.search(r'mso-fit-shape-to-text\s*:\s*(?:t|true|1)\b', box.get('style', ''), re.I)):
                shape.set('style', shape.get('style', '').rstrip(';') + ';mso-wrap-style:square')
        changed += 1
    return changed


def display_width_twips(node):
    widths = [int(e.get('cx', '0')) / 635 for e in node.xpath('.//wp:extent', namespaces=NS)]
    widths += [vml_dimensions(s)[1] * 20 for s in node.xpath('.//v:shape', namespaces=NS)]
    return max(widths, default=0)


def _eligible(carrier, equation_ids):
    if carrier.xpath('.//m:oMath|.//w:fldChar|.//w:instrText', namespaces=NS):
        return False
    if any(o.get(qn('r:id')) in equation_ids or re.search(r'equation|mathtype', o.get('ProgID', ''), re.I)
           for o in carrier.xpath('.//o:OLEObject', namespaces=NS)):
        return False
    # Caption text boxes travel intact, including both AlternateContent branches.
    captions = carrier.xpath('.//w:txbxContent//w:t/text()', namespaces=NS)
    if captions:
        return bool(re.match(r'^\s*(?:Fig(?:ure)?\.?|Рис(?:унок)?\.?)\s*\d', ''.join(captions), re.I))
    if display_width_twips(carrier) < 1800:  # 90pt: inline symbols are not figures
        return False
    return bool(carrier.xpath('.//wp:anchor|.//wp:inline[.//pic:pic]|.//v:imagedata', namespaces=NS))


def detach_display_objects(body, metadata, equation_ids):
    count = 0
    for paragraph in list(body):
        meta = metadata.get(paragraph)
        if (paragraph.tag == qn('w:p') and meta and not ''.join(paragraph.xpath('./w:r/w:t/text()', namespaces=NS)).strip()
                and _eligible(paragraph, equation_ids) and not paragraph.xpath('.//w:txbxContent', namespaces=NS)):
            metadata[paragraph] = meta = replace(meta, role='figure', group_id='native_figure_'+str(meta.block_id))
            following = paragraph.getnext()
            fm = metadata.get(following)
            if fm and re.match(r'^\s*(?:Fig(?:ure)?\.?|Рис(?:унок)?\.?)\s*\d', ''.join(following.xpath('.//w:t/text()', namespaces=NS)), re.I):
                # Keep a translated/multi-paragraph caption's existing group.
                # Replacing only its first member's id strands the translation
                # outside a full-width figure span.
                group = fm.group_id or meta.group_id
                metadata[paragraph] = meta = replace(meta, group_id=group)
                metadata[following] = replace(fm, role='figure_caption', group_id=group)
                if fm.zone == 'body':
                    # A textless leading illustration belongs with its body
                    # caption, not with the bilingual front preceding it.
                    metadata[paragraph] = meta = replace(meta, zone='body')
        if paragraph.tag != qn('w:p') or not meta or meta.role not in {'body', 'figure_caption', 'heading_1', 'heading_2'}:
            continue
        if paragraph.xpath('./w:r/w:fldChar|./w:r/w:instrText|./w:hyperlink', namespaces=NS):
            continue
        # Already separate image/caption paragraphs need geometry normalization,
        # not another paragraph on every invocation.
        if not ''.join(paragraph.xpath('./w:r/w:t/text()', namespaces=NS)).strip():
            continue
        carriers = [(run, child) for run in paragraph.findall('w:r', NS) for child in list(run)
                    if local_name(child) in {'drawing', 'pict', 'object', 'AlternateContent'} and _eligible(child, equation_ids)]
        cursor = paragraph
        for run, carrier in carriers:
            new = etree.Element(qn('w:p'))
            rr = etree.SubElement(new, qn('w:r'))
            if run.find('w:rPr', NS) is not None:
                rr.append(deepcopy(run.find('w:rPr', NS)))
            rr.append(carrier)
            cursor.addnext(new); cursor = new
            caption = bool(carrier.xpath('.//w:txbxContent', namespaces=NS))
            metadata[new] = replace(meta, role='figure_caption' if caption else 'figure',
                                    zone='body', group_id='native_figure_' + str(meta.block_id),
                                    subtype='native_caption_box' if caption else 'detached_display')
            count += 1
    return count


def normalize_display_geometry(paragraph, target_twips, equation_ids=()):
    """Reset stale anchor coordinates and fit native VML previews proportionally."""
    direct_text = ''.join(paragraph.xpath('./w:r/w:t/text()', namespaces=NS)).strip()
    if direct_text or paragraph.xpath('.//m:oMath', namespaces=NS):
        return 0
    if not _eligible(paragraph, equation_ids):
        return 0
    changed = 0
    # Native caption boxes are manuscript geometry, not scientific graphics.
    # Preserve both representations and text while removing their obsolete frame.
    if paragraph.xpath('.//w:txbxContent', namespaces=NS):
        for line in paragraph.xpath('.//a:ln', namespaces=NS):
            for child in list(line):
                if local_name(child) in {'solidFill', 'gradFill', 'pattFill', 'noFill'}:
                    line.remove(child)
            line.insert(0, etree.Element(qn('a:noFill')))
        for shape in paragraph.xpath('.//v:shape', namespaces=NS):
            shape.set('stroked', 'f')
        # Word centres an inline text frame; LibreOffice can pin the same narrow
        # frame to the column's left edge. Use the available column width for
        # text-only captions in BOTH compatibility branches. Never stretch an
        # image, OLE preview or mixed drawing group along with the text frame.
        if not paragraph.xpath('.//a:blip|.//v:imagedata|.//o:OLEObject', namespaces=NS):
            width = str(round(target_twips * 635))
            for extent in paragraph.xpath('.//wp:extent|.//a:xfrm/a:ext', namespaces=NS):
                if extent.get('cx') != width:
                    extent.set('cx', width); changed += 1
            for shape in paragraph.xpath('.//v:shape[v:textbox]', namespaces=NS):
                style, _, _ = vml_dimensions(shape)
                style['width'] = f'{target_twips / 20:.3f}pt'
                shape.set('style', ';'.join(f'{k}:{v}' for k, v in style.items()))
                changed += 1
    for anchor in paragraph.xpath('.//wp:anchor[not(ancestor::w:txbxContent)]', namespaces=NS):
        inline = etree.Element(qn('wp:inline'), distT='0', distB='0', distL='0', distR='0')
        for tag in ('wp:extent', 'wp:effectExtent', 'wp:docPr', 'wp:cNvGraphicFramePr', 'a:graphic'):
            child = anchor.find(tag, NS)
            if child is not None:
                inline.append(child)
        anchor.getparent().replace(anchor, inline)
        changed += 1
    for shape in paragraph.xpath('.//v:shape[not(ancestor::w:txbxContent)]', namespaces=NS):
        style, width, height = vml_dimensions(shape)
        if width <= 0 or height <= 0:
            continue
        # Text frames already fit exactly; the safety inset applies to pictures.
        factor = min(1, target_twips / 20 * (1 if shape.find('v:textbox', NS) is not None else .98) / width)
        clean = {k: v for k, v in style.items() if k not in {'position','left','top','margin-left','margin-top','z-index'}
                 and not k.startswith(('mso-position-', 'mso-wrap-'))}
        clean.update(width=f'{width * factor:.3f}pt', height=f'{height * factor:.3f}pt')
        value = ';'.join(f'{k}:{v}' for k, v in clean.items())
        if value != shape.get('style'):
            shape.set('style', value); changed += 1
    prop(pprops(paragraph), 'jc', val='center')
    prop(pprops(paragraph), 'ind', left=0, right=0, firstLine=0)
    return changed
