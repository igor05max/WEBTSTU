"""Native template formatting and collision-safe package dependency imports.

Only properties and explicitly selected reusable stories are copied. The source
article remains the package base; its styles, media and relationship IDs survive.
"""
from copy import deepcopy
import posixpath
from lxml import etree
from apps.template_workspace.v2.ooxml.namespaces import NS, qn, local_name

REL_NS = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT_NS = 'http://schemas.openxmlformats.org/package/2006/content-types'


class NativeTemplateFormatting:
    def __init__(self, package):
        self.root = etree.fromstring(package.read('word/styles.xml'))
        self.styles = {s.get(qn('w:styleId')): s for s in self.root.findall('w:style', NS)}
        self.default = next((s.get(qn('w:styleId')) for s in self.styles.values()
                             if s.get(qn('w:type')) == 'paragraph' and s.get(qn('w:default')) == '1'), None)

    def chain(self, sid):
        result, seen = [], set()
        while sid in self.styles and sid not in seen:
            seen.add(sid)
            style = self.styles[sid]
            result.insert(0, style)
            base = style.find('w:basedOn', NS)
            sid = base.get(qn('w:val')) if base is not None else None
        return result

    def properties(self, paragraph, kind='pPr', run=None):
        direct = paragraph.find('w:pPr', NS)
        sid = direct.find('w:pStyle', NS) if direct is not None else None
        sid = sid.get(qn('w:val')) if sid is not None else self.default
        layers = [self.root.find(f'w:docDefaults/w:{kind}Default/w:{kind}', NS)]
        layers.extend(s.find(f'w:{kind}', NS) for s in self.chain(sid))
        if kind == 'pPr':
            layers.append(direct)
        elif run is not None:
            rpr = run.find('w:rPr', NS)
            rsid = rpr.find('w:rStyle', NS) if rpr is not None else None
            if rsid is not None:
                layers.extend(s.find('w:rPr', NS) for s in self.chain(rsid.get(qn('w:val'))))
            layers.append(rpr)
        result = etree.Element(qn(f'w:{kind}'))
        for layer in layers:
            if layer is None:
                continue
            for prop in layer:
                if local_name(prop) in {'pStyle', 'rStyle', 'sectPr', 'numPr', 'pPrChange', 'rPrChange'}:
                    continue
                old = result.find(prop.tag)
                if old is not None and local_name(prop) in {'spacing', 'ind', 'rFonts', 'lang'}:
                    old.attrib.update(prop.attrib)
                else:
                    if old is not None:
                        result.remove(old)
                    result.append(deepcopy(prop))
        if kind == 'rPr' and result.find('w:sz', NS) is None:
            etree.SubElement(result, qn('w:sz')).set(qn('w:val'), '20')
        if kind == 'pPr' and result.find('w:jc', NS) is None:
            etree.SubElement(result, qn('w:jc')).set(qn('w:val'), 'left')
        if kind == 'pPr':
            ind = result.find('w:ind', NS)
            if ind is None:
                ind = etree.SubElement(result, qn('w:ind'))
            for attr in ('left', 'right'):
                if qn('w:'+attr) not in ind.attrib:
                    ind.set(qn('w:'+attr), '0')
            if not any(qn('w:'+a) in ind.attrib for a in ('firstLine','hanging')):
                ind.set(qn('w:firstLine'), '0')
            for flag in ('snapToGrid','contextualSpacing','keepNext','keepLines'):
                if result.find('w:'+flag, NS) is None:
                    etree.SubElement(result, qn('w:'+flag)).set(qn('w:val'), '0')
        return result

    def materialise(self, root):
        for p in root.xpath('.//w:p', namespaces=NS):
            # Resolve runs before removing paragraph style information.
            runs = [(r, self.properties(p, 'rPr', r)) for r in p.xpath('.//w:r[not(ancestor::m:oMath)]', namespaces=NS)]
            ppr = self.properties(p)
            old = p.find('w:pPr', NS)
            if old is not None:
                p.remove(old)
            p.insert(0, ppr)
            for run, rpr in runs:
                old = run.find('w:rPr', NS)
                if old is not None:
                    run.remove(old)
                run.insert(0, rpr)


class StoryImporter:
    def __init__(self, source, target):
        self.source, self.target = source, target
        self.parts = {}
        self.names = {}
        self.used = set(target.namelist())
        self.content_types = etree.fromstring(target.read('[Content_Types].xml'))
        self.source_types = etree.fromstring(source.read('[Content_Types].xml'))

    def copy(self, part, transform=None):
        if part in self.names:
            return self.names[part]
        if part not in self.source.namelist():
            raise ValueError(f'Missing template story dependency: {part}')
        folder, filename = posixpath.split(part)
        count = 1
        stem, ext = posixpath.splitext(filename)
        dest = posixpath.join(folder, stem + '_v2' + ext)
        while dest in self.used:
            count += 1
            dest = posixpath.join(folder, stem + f'_v2_{count}' + ext)
        self.used.add(dest)
        self.names[part] = dest
        payload = self.source.read(part)
        self.parts[dest] = transform(payload) if transform else payload
        self._content_type(part, dest)
        relpart = posixpath.join(folder, '_rels', filename + '.rels')
        if relpart in self.source.namelist():
            rels = etree.fromstring(self.source.read(relpart))
            for rel in rels:
                if rel.get('TargetMode') == 'External':
                    continue
                dependency = posixpath.normpath(posixpath.join(folder, rel.get('Target', ''))).lstrip('/')
                # No executable/template attachment is needed for page furniture.
                if rel.get('Type', '').endswith(('/attachedTemplate', '/oleObject', '/package', '/control')):
                    raise ValueError('Active template story dependency is not supported')
                copied = self.copy(dependency)
                rel.set('Target', posixpath.relpath(copied, posixpath.dirname(dest)))
            self.parts[posixpath.join(posixpath.dirname(dest), '_rels', posixpath.basename(dest)+'.rels')] = etree.tostring(rels)
        return dest

    def _content_type(self, source, dest):
        for item in self.source_types:
            name = item.get('PartName')
            if name == '/' + source:
                clone = deepcopy(item)
                clone.set('PartName', '/' + dest)
                self.content_types.append(clone)
            elif local_name(item) == 'Default' and dest.endswith('.' + item.get('Extension', '')):
                if not any(x.get('Extension') == item.get('Extension') for x in self.content_types):
                    self.content_types.append(deepcopy(item))
