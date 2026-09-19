"""Native DOCX -> typed, provenance-carrying LaTeX blocks, without LLM prose.

This bridge consumes the native editor's DOCX, not the lossy legacy ArticleIR.
Every visible paragraph (including text boxes and table cells) stays in order.
Unsupported objects fail explicitly. The companion Word remains native/editable.
"""
from dataclasses import asdict, dataclass, field
from collections import Counter
from hashlib import sha256
from pathlib import Path, PurePosixPath
import json
from io import BytesIO
import posixpath
import re
from zipfile import ZipFile

from lxml import etree

from apps.template_workspace.v2.classification.roles import RoleClassifierV2
from apps.template_workspace.v2.inspector.document import DocumentInspector
from apps.template_workspace.v2.ooxml.namespaces import NS, qn
from paper_formatter.parsers.omml_parser import omml_to_latex
from paper_formatter.utils.text import latex_escape


class UnsupportedContent(ValueError):
    pass


@dataclass
class Block:
    id: str
    kind: str
    role: str
    zone: str
    text: str
    tex: str
    width_pt: float = 0
    image_count: int = 0
    math_count: int = 0
    columns: int = 0
    rows: int = 0
    captions: list = field(default_factory=list)


def visible_tree(node):
    """Select one AlternateContent branch; do not duplicate fallback text/images."""
    if etree.QName(node).localname == 'AlternateContent':
        branch = next((x for x in node if etree.QName(x).localname == 'Choice'), None)
        if branch is None:
            branch = next(iter(node), None)
        if branch is not None:
            yield from visible_tree(branch)
        return
    if node.tag in {qn('w:del'), qn('w:moveFrom')}:
        raise UnsupportedContent('Resolve tracked deletions/moves before LaTeX export.')
    yield node
    for child in node:
        yield from visible_tree(child)


def visible_text(node):
    return ''.join((x.text or '') if x.tag == qn('w:t') else ' '
                   for x in visible_tree(node)
                   if x.tag in {qn('w:t'), qn('w:tab'), qn('w:br'), qn('w:p')})


def truth(node, name):
    prop = node.find('w:rPr/' + name, NS)
    return prop is not None and prop.get(qn('w:val'), '1') not in {'0', 'false', 'off'}


def escaped(text):
    # Discretionary hyphens are layout, not scientific content. Preserve actual dashes.
    text = text.replace('\u00ad', '').replace('\u200b', '').replace('\u00a0', ' ')
    return latex_escape(text).replace('\n', ' ')


class NativeBridge:
    def __init__(self, path, project):
        self.path, self.project = Path(path), Path(project)
        self.project.mkdir(parents=True, exist_ok=True)
        (self.project / 'assets').mkdir(exist_ok=True)
        self.report = DocumentInspector(path).inspect()
        self.structure = RoleClassifierV2().article_structure(self.report)
        self.roles = {b['id']: b for b in self.structure.blocks}
        self.tables = {t.id: t for t in self.report.tables}
        self.assets, self.formulas, self.warnings = [], [], []
        self.block_id = ''
        self.zip = None
        self.author_zone = False
        self.emitted_text = []

    def build(self):
        with ZipFile(self.path) as archive:
            self.zip = archive
            root = etree.fromstring(archive.read('word/document.xml'))
            # Footnotes require a dedicated numbered-text contract; never drop them.
            if root.xpath('//w:footnoteReference|//w:endnoteReference', namespaces=NS):
                raise UnsupportedContent('Footnotes/endnotes are not supported in this experimental bridge.')
            relroot = etree.fromstring(archive.read('word/_rels/document.xml.rels'))
            self.rels = {r.get('Id'): r for r in relroot}
            # DOCX packages retain unused footer parts from earlier templates.
            # Only a footer referenced by a live section is journal furniture.
            active_footers = []
            for ref in root.xpath('//w:sectPr/w:footerReference', namespaces=NS):
                rel = self.rels.get(ref.get(qn('r:id')))
                if rel is not None:
                    target = posixpath.normpath(posixpath.join('word',rel.get('Target',''))).lstrip('/')
                    active_footers.extend(f.text for f in self.report.footers if f.part == target and re.search('[A-Za-zА-Яа-я]',f.text))
            blocks = []
            for index, node in enumerate(root.find('w:body', NS), 1):
                self.block_id = f'block_{index:04d}'
                if node.tag == qn('w:sectPr'):
                    continue
                if node.tag not in {qn('w:p'), qn('w:tbl')}:
                    raise UnsupportedContent(f'Unsupported document block: {etree.QName(node).localname}')
                role = self.roles.get(self.block_id, {})
                if role.get('detected_role') == 'author_information':
                    self.author_zone = True
                assets_before, math_before = len(self.assets), len(self.formulas)
                tex = self.table(node) if node.tag == qn('w:tbl') else self.inline(node)
                text = visible_text(node)
                if not tex.strip():
                    continue
                images = self.assets[assets_before:]
                table = self.tables.get(self.block_id)
                kind = 'table' if node.tag == qn('w:tbl') else ('figure' if images and not text.strip() else 'paragraph')
                is_figure_table = table and table.classification == 'FIGURE_CONTAINER'
                if is_figure_table:
                    kind = 'figure'
                if math_before < len(self.formulas) and not text.strip() and not images:
                    kind = 'equation'
                width = sum(a['width_pt'] for a in images)
                if table:
                    width = sum(float(x or 0) for x in table.grid) / 20
                blocks.append(Block(self.block_id, kind,
                                    role.get('detected_role', 'body'), role.get('zone') or '',
                                    text, tex, width, len(images), len(self.formulas)-math_before,
                                    table.logical_column_count if table else 0,
                                    table.row_count if table else 0))
            expected_text = Counter((root.getroottree().getpath(n), n.text or '')
                                    for n in visible_tree(root) if n.tag == qn('w:t'))
            emitted_text = Counter(self.emitted_text)
            if expected_text != emitted_text:
                raise UnsupportedContent('Text transfer mismatch: a native text node was omitted or duplicated.')
        # Only attach adjacent captions; source order and source text remain unchanged.
        grouped, index = [], 0
        while index < len(blocks):
            block = blocks[index]
            if block.role == 'table_caption' and index + 1 < len(blocks) and blocks[index + 1].kind == 'table':
                table = blocks[index + 1]
                table.captions.append(asdict(block)); grouped.append(table); index += 2
            elif block.kind in {'figure', 'table'}:
                index += 1
                expected_caption = 'figure_caption' if block.kind == 'figure' else 'table_caption'
                while index < len(blocks) and blocks[index].role == expected_caption:
                    block.captions.append(asdict(blocks[index])); index += 1
                grouped.append(block)
            else:
                grouped.append(block); index += 1
        manifest = {
            'source_sha256': sha256(self.path.read_bytes()).hexdigest(),
            'blocks': [asdict(b) for b in grouped], 'assets': self.assets,
            'formulas': self.formulas, 'warnings': self.warnings,
            'native_equations': self.report.embedded_objects.omml_formula_count,
            'converted_equations': len(self.formulas),
            'text_transfer': {'expected_nodes': sum(expected_text.values()),
                              'emitted_nodes': sum(emitted_text.values()), 'exact': True},
            'source_text': '\n'.join(b.text for b in blocks),
            'style': 'JAMT 2026.1', 'experimental': True,
            'running_footer': active_footers[-1] if active_footers else '',
        }
        if manifest['native_equations'] != manifest['converted_equations']:
            raise UnsupportedContent('Native equation inventory does not match converted equations.')
        (self.project / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        return grouped, manifest

    def inline(self, node):
        tag = etree.QName(node).localname
        if tag == 'AlternateContent':
            branch = next((c for c in node if etree.QName(c).localname == 'Choice'), None)
            if branch is None:
                branch = next(iter(node), None)
            return self.inline(branch) if branch is not None else ''
        if node.tag in {qn('w:del'), qn('w:moveFrom')}:
            raise UnsupportedContent('Tracked changes must be resolved first.')
        if node.tag == qn('m:oMath'):
            # Only structural constructs supported by the existing tested converter.
            forbidden = {'limLow', 'limUpp', 'sPre', 'groupChr', 'phant', 'borderBox'}
            if any(etree.QName(x).localname in forbidden for x in node.iter()):
                raise UnsupportedContent(f'Unsupported Office Math construct in {self.block_id}')
            # The legacy converter accepts raw delimiter attributes and has
            # fallback accent/bar shapes. Constrain those before conversion.
            for prop in node.xpath('.//m:begChr|.//m:endChr', namespaces=NS):
                if prop.get(qn('m:val'), '') not in {'', '(', ')', '[', ']', '{', '}', '|', '‖', '∥'}:
                    raise UnsupportedContent('Unsupported math delimiter.')
            for token in node.xpath('.//m:t', namespaces=NS):
                if any(c in (token.text or '') for c in ('\\', '$')):
                    raise UnsupportedContent('Literal TeX syntax in Office Math requires a verified adapter.')
            for prop in node.xpath('.//m:accPr/m:chr', namespaces=NS):
                if prop.get(qn('m:val')) not in {'^', '¯', '→', '~'}:
                    raise UnsupportedContent('Unsupported math accent.')
            for prop in node.xpath('.//m:barPr/m:pos', namespaces=NS):
                if prop.get(qn('m:val')) != 'top':
                    raise UnsupportedContent('Lower math bars require a verified adapter.')
            if any(len(d.findall('m:e', NS)) > 1 for d in node.findall('.//m:d', NS)):
                raise UnsupportedContent('Multi-argument math delimiters require a verified adapter.')
            tex = omml_to_latex(node)
            if not tex:
                raise UnsupportedContent('Empty Office Math conversion.')
            self.formulas.append({'block_id': self.block_id, 'latex': tex,
                                  'xml_sha256': sha256(etree.tostring(node)).hexdigest()})
            return r'\(' + tex + r'\)'
        if node.tag == qn('w:hyperlink'):
            text = ''.join(self.inline(child) for child in node)
            rel = self.rels.get(node.get(qn('r:id')))
            target = rel.get('Target', '') if rel is not None else ''
            if re.match(r'^(?:https?://|mailto:)', target, re.I):
                return r'\href{' + escaped(target) + '}{' + text + '}'
            return text
        if node.tag == qn('w:t'):
            self.emitted_text.append((node.getroottree().getpath(node), node.text or ''))
            return escaped(node.text or '')
        if node.tag == qn('w:tab'):
            return r'\quad '
        if node.tag == qn('w:br'):
            return ' '
        if node.tag == qn('w:sym'):
            val = node.get(qn('w:char'), '')
            font = node.get(qn('w:font'), '').casefold()
            if font == 'wingdings' and val in {'F02A', 'F02B'}:
                return r'{\fontspec{DejaVu Sans}✉}'
            if font == 'symbol' and val == 'F0D3':
                return r'\textcopyright{}'
            symbol_chars = {'F0B0': '°', 'F0B4': '×', 'F062': 'β', 'F071': 'θ'}
            if font == 'symbol' and val in symbol_chars:
                return escaped(symbol_chars[val])
            if val == '002A':
                return '*'
            raise UnsupportedContent(f'Unmapped symbolic font character {val}')
        if node.tag == qn('w:r'):
            text = ''.join(self.inline(c) for c in node if c.tag != qn('w:rPr'))
            if not text:
                return ''
            # Inline math has its own typography; never nest it in text superscripts.
            if not node.xpath('.//m:oMath', namespaces=NS):
                va = node.find('w:rPr/w:vertAlign', NS)
                if va is not None and va.get(qn('w:val')) in {'superscript', 'subscript'}:
                    command = 'textsuperscript' if va.get(qn('w:val')) == 'superscript' else 'textsubscript'
                    text = '\\' + command + '{' + text + '}'
            if truth(node, 'w:i'):
                text = r'\textit{' + text + '}'
            if truth(node, 'w:b'):
                text = r'\textbf{' + text + '}'
            return text
        if node.tag in {qn('w:drawing'), qn('w:pict'), qn('w:object')}:
            if node.xpath('.//*[local-name()="OLEObject"]'):
                raise UnsupportedContent('OLE/MathType needs an explicitly verified vector preview; native DOCX is retained.')
            # Visible text boxes are content, not commands, and must survive.
            pictures = node.xpath('.//a:blip|.//v:imagedata', namespaces=NS)
            result = ' '.join(self.picture(p) for p in pictures)
            boxes = node.xpath('.//w:txbxContent[not(ancestor::w:txbxContent)]', namespaces=NS)
            for box in boxes:
                result += ' ' + r' \newline '.join(self.inline(p) for p in box.findall('w:p', NS))
            if not pictures and not boxes:
                raise UnsupportedContent('Unsupported vector shape without an image or text box.')
            return result
        if tag.endswith('Pr') or node.tag in {qn('w:instrText'), qn('w:bookmarkStart'), qn('w:bookmarkEnd')}:
            return ''
        if node.tag == qn('w:tbl'):
            return self.table(node)
        return ''.join(self.inline(child) for child in node)

    def picture(self, node):
        rid = node.get(qn('r:embed')) or node.get(qn('r:id'))
        rel = self.rels.get(rid)
        if rel is None or rel.get('TargetMode') == 'External':
            raise UnsupportedContent('Missing/external image relationship.')
        target = posixpath.normpath(posixpath.join('word', rel.get('Target', '')))
        if not target.startswith('word/'):
            raise UnsupportedContent('Image relationship escapes the Word package.')
        payload = self.zip.read(target)
        ext = PurePosixPath(target).suffix.lower()
        if ext not in {'.png', '.jpg', '.jpeg'}:
            raise UnsupportedContent(f'Image {ext} requires a verified vector/raster conversion.')
        digest = sha256(payload).hexdigest()
        name = 'assets/' + digest[:20] + ext
        (self.project / name).write_bytes(payload)
        container = node
        while container.getparent() is not None and container.tag not in {qn('w:drawing'), qn('w:pict')}:
            container = container.getparent()
        extents = container.xpath('.//wp:extent', namespaces=NS)
        width, height = 160.0, 100.0
        if extents:
            width = float(extents[0].get('cx', 0)) / 12700
            height = float(extents[0].get('cy', 0)) / 12700
        else:
            shape = node.getparent()
            style = shape.get('style', '')
            wm = re.search(r'(?:^|;)width:([\d.]+)(pt)?', style)
            hm = re.search(r'(?:^|;)height:([\d.]+)(pt)?', style)
            scale_x = scale_y = 1.0
            group = shape.getparent()
            if group is not None and etree.QName(group).localname == 'group':
                size = group.get('coordsize', '').split(',')
                gw = re.search(r'(?:^|;)width:([\d.]+)pt', group.get('style', ''))
                gh = re.search(r'(?:^|;)height:([\d.]+)pt', group.get('style', ''))
                if len(size) == 2 and gw and gh:
                    scale_x, scale_y = float(gw[1])/float(size[0]), float(gh[1])/float(size[1])
            if wm:
                width = float(wm[1]) * (1 if wm[2] else scale_x)
            if hm:
                height = float(hm[1]) * (1 if hm[2] else scale_y)
        crop = container.xpath('.//a:srcRect', namespaces=NS)
        crop = {k: float(v)/100000 for k, v in crop[0].attrib.items()} if crop else {}
        transforms = container.xpath('.//a:xfrm', namespaces=NS)
        transform = dict(transforms[0].attrib) if transforms else {}
        rendered_name = name
        if crop or any(k in transform for k in ('rot', 'flipH', 'flipV')):
            from PIL import Image, ImageOps
            with Image.open(BytesIO(payload)) as original:
                im = original.copy()
            left, top, right, bottom = (crop.get(k, 0) for k in ('l', 't', 'r', 'b'))
            if min(left, top, right, bottom) < 0 or left+right >= 1 or top+bottom >= 1:
                raise UnsupportedContent('Invalid or expanded image crop requires manual review.')
            im = im.crop((round(im.width*left), round(im.height*top),
                          round(im.width*(1-right)), round(im.height*(1-bottom))))
            if transform.get('flipH') in {'1', 'true'}:
                im = ImageOps.mirror(im)
            if transform.get('flipV') in {'1', 'true'}:
                im = ImageOps.flip(im)
            angle = float(transform.get('rot', 0))/60000
            if angle:
                im = im.rotate(-angle, expand=True, resample=Image.Resampling.BICUBIC)
            transform_id = sha256(json.dumps([crop, transform], sort_keys=True).encode()).hexdigest()[:8]
            rendered_name = f'assets/{digest[:20]}-{transform_id}.png'
            im.save(self.project / rendered_name)
        self.assets.append({'block_id': self.block_id, 'relationship_id': rid, 'source': target,
                            'path': name, 'rendered_path': rendered_name, 'sha256': digest,
                            'crop': crop, 'transform': transform, 'width_pt': width, 'height_pt': height})
        # adjustbox caps width relative to the current cell/column, preserving aspect.
        return rf'\labimage{{{width:.3f}}}{{{rendered_name}}}'

    def table(self, node):
        grid = [max(1, int(x.get(qn('w:w'), 1))) for x in node.findall('w:tblGrid/w:gridCol', NS)]
        if not grid:
            raise UnsupportedContent('Table has no explicit column grid.')
        if node.xpath('.//w:tbl', namespaces=NS):
            raise UnsupportedContent('Nested tables require a separate layout adapter.')
        # Visual objects in tables are a figure grid, not a ruled data table.
        figure = bool(node.xpath('.//w:drawing|.//w:pict', namespaces=NS))
        ruled = not figure and not self.author_zone
        # An equal-width source grid is particularly poor for mathematical tables:
        # reserve space for displayed fractions, not the short citation column.
        math_columns = set()
        for row in node.findall('w:tr', NS):
            column = 0
            for cell in row.findall('w:tc', NS):
                span_node = cell.find('w:tcPr/w:gridSpan', NS)
                span = int(span_node.get(qn('w:val'), 1)) if span_node is not None else 1
                if cell.xpath('.//m:oMath', namespaces=NS):
                    math_columns.add(column)
                column += span
        if math_columns and len(grid) == 3:
            grid = [200 if i in math_columns else (70 if i == 2 else 140) for i in range(3)]
        total = sum(grid)
        cell_align = r'\RaggedRight' if self.author_zone else r'\centering'
        spec = '@{}' + ''.join('>{' + cell_align + r'\arraybackslash}p{' + f'{n/total:.6f}' + r'\labtablewidth}' for n in grid) + '@{}'
        rows = node.findall('w:tr', NS)
        lines = [r'\begingroup\setlength{\tabcolsep}{3pt}',
                 rf'\setlength{{\labtablewidth}}{{\dimexpr\linewidth-{6*(len(grid)-1)}pt\relax}}',
                 r'\fontsize{\labtablesize}{\labtableleading}\selectfont',
                 r'\renewcommand{\arraystretch}{1.20}\setlength{\extrarowheight}{2pt}', r'\begin{tabular}{' + spec + '}']
        if ruled:
            lines.append(r'\toprule')
        for row_index, row in enumerate(rows):
            first_cell = row.find('w:tc', NS)
            first_merge = first_cell.find('w:tcPr/w:vMerge', NS) if first_cell is not None else None
            if ruled and row_index > 0 and first_merge is not None and first_merge.get(qn('w:val')) == 'restart':
                # Boundary after a multi-row header and between outer data groups.
                lines.append(r'\midrule')
            cells, col = [], 0
            for cell in row.findall('w:tc', NS):
                span_node = cell.find('w:tcPr/w:gridSpan', NS)
                span = int(span_node.get(qn('w:val'), 1)) if span_node is not None else 1
                if span < 1 or col + span > len(grid):
                    raise UnsupportedContent('Invalid table merge grid.')
                content = r'\par '.join(self.inline(p) for p in cell.findall('w:p', NS))
                if cell.xpath('.//m:oMath', namespaces=NS):
                    content = content.replace(r'\(', r'\(\displaystyle ')
                content = content.strip() or r'\strut'
                # Continuation cells contain no copy of the vertically merged text.
                # Preserve native text exactly; top alignment is robust for tall rows.
                if span > 1:
                    fraction = sum(grid[col:col+span]) / total
                    width = rf'\dimexpr {fraction:.6f}\labtablewidth+{6*(span-1)}pt\relax'
                    edge_left = '@{}' if col == 0 else ''
                    edge_right = '@{}' if col + span == len(grid) else ''
                    content = ('\\multicolumn{' + str(span) + '}{' + edge_left
                               + r'>{\centering\arraybackslash}p{' + width + '}'
                               + edge_right + '}{' + content + '}')
                cells.append(content); col += span
            if col != len(grid):
                raise UnsupportedContent('Sparse table rows are not supported.')
            lines.append(' & '.join(cells) + r' \\')
            # Avoid a rule through a vertically merged header. Native header row is
            # kept as text; a single rule after the header group is sufficient.
            if ruled and row_index == 0 and not row.xpath('.//w:vMerge', namespaces=NS):
                lines.append(r'\midrule')
        if ruled:
            lines.append(r'\bottomrule')
        lines += [r'\end{tabular}', r'\endgroup']
        return '\n'.join(lines)
