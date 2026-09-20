"""Visible Word list labels are content even though they are not w:t nodes."""
from lxml import etree
from apps.template_workspace.v2.ooxml.namespaces import NS, qn
from apps.template_workspace.v2.editor.template_evidence import NativeTemplateFormatting


class NumberingError(ValueError):
    pass


class Numbering:
    def __init__(self, archive):
        self.styles = NativeTemplateFormatting(archive)
        self.root = etree.fromstring(archive.read('word/numbering.xml')) if 'word/numbering.xml' in archive.namelist() else None
        self.counters = {}

    def label(self, paragraph):
        direct = paragraph.find('w:pPr/w:numPr', NS)
        if direct is None:
            sid = paragraph.find('w:pPr/w:pStyle', NS)
            for style in reversed(self.styles.chain(sid.get(qn('w:val')) if sid is not None else self.styles.default)):
                direct = style.find('w:pPr/w:numPr', NS)
                if direct is not None: break
        if direct is None:
            return ''
        num_id = direct.find('w:numId', NS)
        if num_id is None or num_id.get(qn('w:val')) == '0': return ''
        level = direct.find('w:ilvl', NS)
        if level is not None and level.get(qn('w:val'), '0') != '0':
            raise NumberingError('Multilevel numbering needs a verified LaTeX adapter.')
        if self.root is None:
            raise NumberingError('Missing Word numbering part.')
        num_id = num_id.get(qn('w:val'))
        instance = self.root.find(f'w:num[@w:numId="{num_id}"]', NS)
        if instance is None: raise NumberingError('Missing numbering instance.')
        abstract = instance.find('w:abstractNumId', NS)
        if abstract is None: raise NumberingError('Missing abstract numbering link.')
        definition = self.root.find(f'w:abstractNum[@w:abstractNumId="{abstract.get(qn("w:val"))}"]/w:lvl[@w:ilvl="0"]', NS)
        override = instance.find('w:lvlOverride[@w:ilvl="0"]', NS)
        if override is not None and override.find('w:lvl', NS) is not None:
            definition = override.find('w:lvl', NS)
        if definition is None: raise NumberingError('Missing list level definition.')
        fmt = definition.find('w:numFmt', NS)
        text = definition.find('w:lvlText', NS)
        if fmt is None or fmt.get(qn('w:val')) != 'decimal' or text is None:
            raise NumberingError('Only verified decimal list numbering is supported.')
        pattern = text.get(qn('w:val'), '')
        if pattern not in {'%1.', '%1)', '(%1)', '[%1]', '%1'}:
            raise NumberingError('Unsupported list label pattern.')
        start = definition.find('w:start', NS)
        start = int(start.get(qn('w:val'), 1)) if start is not None else 1
        if override is not None and override.find('w:startOverride', NS) is not None:
            start = int(override.find('w:startOverride', NS).get(qn('w:val')))
        value = self.counters.get(num_id, start-1) + 1
        self.counters[num_id] = value
        return pattern.replace('%1', str(value))
