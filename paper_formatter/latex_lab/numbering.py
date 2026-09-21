"""Visible Word list labels are content even though they are not w:t nodes."""
from lxml import etree
import re
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
        level = int(level.get(qn('w:val'), '0')) if level is not None else 0
        if not 0 <= level <= 8:raise NumberingError('Invalid list level.')
        if self.root is None:
            raise NumberingError('Missing Word numbering part.')
        num_id = num_id.get(qn('w:val'))
        instance = self.root.find(f'w:num[@w:numId="{num_id}"]', NS)
        if instance is None: raise NumberingError('Missing numbering instance.')
        abstract = instance.find('w:abstractNumId', NS)
        if abstract is None: raise NumberingError('Missing abstract numbering link.')
        abstract_id = abstract.get(qn('w:val'))
        definition = self.root.find(f'w:abstractNum[@w:abstractNumId="{abstract_id}"]/w:lvl[@w:ilvl="{level}"]', NS)
        override = instance.find(f'w:lvlOverride[@w:ilvl="{level}"]', NS)
        if override is not None and override.find('w:lvl', NS) is not None:
            definition = override.find('w:lvl', NS)
        if definition is None: raise NumberingError('Missing list level definition.')
        fmt = definition.find('w:numFmt', NS)
        text = definition.find('w:lvlText', NS)
        if fmt is None or text is None:raise NumberingError('Missing list format.')
        fmt = fmt.get(qn('w:val'))
        pattern = text.get(qn('w:val'), '')
        if fmt == 'bullet':
            # Common Word Symbol/Wingdings bullets have private-use glyphs.
            symbols={'\uf0b7':'•','\uf0a7':'▪','\uf0d8':'▸'}
            value=symbols.get(pattern,pattern)
            if value not in {'•','▪','◦','○','●','–','-','▸','o'}:raise NumberingError('Unsupported bullet symbol.')
            return value
        if fmt == 'none':return ''
        if fmt not in {'decimal','decimalZero','lowerLetter','upperLetter','lowerRoman','upperRoman'}:
            raise NumberingError('Unsupported list numbering format.')
        if re.search(r'%(?![1-9])',pattern) or len(pattern)>60:raise NumberingError('Unsupported list label pattern.')
        start = definition.find('w:start', NS)
        start = int(start.get(qn('w:val'), 1)) if start is not None else 1
        if override is not None and override.find('w:startOverride', NS) is not None:
            start = int(override.find('w:startOverride', NS).get(qn('w:val')))
        counters=self.counters.setdefault(num_id,{})
        value=counters.get(level,start-1)+1;counters[level]=value
        for deeper in list(counters):
            if deeper<=level:continue
            lower=self.root.find(f'w:abstractNum[@w:abstractNumId="{abstract_id}"]/w:lvl[@w:ilvl="{deeper}"]',NS)
            restart=lower.find('w:lvlRestart',NS) if lower is not None else None
            trigger=int(restart.get(qn('w:val'))) if restart is not None else deeper
            if trigger and level<trigger:del counters[deeper]
        def replace(match):
            depth=int(match[1])-1
            if depth>level:raise NumberingError('List label references a deeper level.')
            definition2=self.root.find(f'w:abstractNum[@w:abstractNumId="{abstract_id}"]/w:lvl[@w:ilvl="{depth}"]',NS)
            override2=instance.find(f'w:lvlOverride[@w:ilvl="{depth}"]',NS)
            if override2 is not None and override2.find('w:lvl',NS) is not None:
                definition2=override2.find('w:lvl',NS)
            if definition2 is None:raise NumberingError('Missing referenced list level.')
            format2=definition2.find('w:numFmt',NS)
            start2=definition2.find('w:start',NS)
            fallback=int(start2.get(qn('w:val'),1)) if start2 is not None else 1
            if override2 is not None and override2.find('w:startOverride',NS) is not None:
                fallback=int(override2.find('w:startOverride',NS).get(qn('w:val')))
            current=counters.get(depth,fallback)
            return _number(current,format2.get(qn('w:val')) if format2 is not None else 'decimal')
        return re.sub(r'%([1-9])',replace,pattern)


def _number(value, fmt):
    if fmt=='decimal':return str(value)
    if fmt=='decimalZero':return str(value).zfill(2)
    if value<1:raise NumberingError('Alphabetic/Roman list numbers must be positive.')
    if fmt in {'lowerLetter','upperLetter'}:
        # Word repeats the same letter for the second alphabet (aa, bb...).
        result=chr(97+(value-1)%26)*(1+(value-1)//26)
        return result.upper() if fmt=='upperLetter' else result
    if fmt in {'lowerRoman','upperRoman'}:
        if value>3999:raise NumberingError('Roman list number outside supported range.')
        result=''
        for n,s in [(1000,'M'),(900,'CM'),(500,'D'),(400,'CD'),(100,'C'),(90,'XC'),(50,'L'),(40,'XL'),(10,'X'),(9,'IX'),(5,'V'),(4,'IV'),(1,'I')]:
            while value>=n:result+=s;value-=n
        return result.lower() if fmt=='lowerRoman' else result
    raise NumberingError('Unsupported referenced list format.')
