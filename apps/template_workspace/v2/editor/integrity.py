"""Post-edit preservation gates independent of the formatter's role decisions."""
from collections import Counter
from hashlib import sha256
import re
from lxml import etree
from apps.template_workspace.v2.ooxml.namespaces import NS, qn


def native_integrity(source_zip, result_root, replacements, *, source_author_ids=(), correspondence_symbols=0, excluded_metadata_ids=()):
    source = etree.fromstring(source_zip.read('word/document.xml'))
    def facts(root):
        result = {}
        for label, query in {
            'equations': '//m:oMath', 'drawings': '//w:drawing',
            'legacy_drawings': '//w:pict', 'embedded_objects': '//w:object',
            'hyperlinks': '//w:hyperlink', 'bookmarks': '//w:bookmarkStart',
            'table_cells': '//w:tc',
        }.items():
            result[label] = len(root.xpath(query, namespaces=NS))
        result['math_tokens'] = Counter(root.xpath('//m:t/text()', namespaces=NS))
        result['positioned_text'] = Counter(
            (p.get(qn('w:val')), character)
            for p in root.xpath('//w:rPr/w:vertAlign', namespaces=NS)
            if p.get(qn('w:val')) in {'superscript', 'subscript'}
            for character in ''.join(p.getparent().getparent().xpath('.//w:t/text()', namespaces=NS))
            if not character.isspace())
        return result
    before, after = facts(source), facts(result_root)
    expected_stars = 0
    body = source.find('w:body', NS)
    for index, paragraph in enumerate(body, 1):
        if f'block_{index:04d}' in source_author_ids:
            expected_stars += sum(text.count('*') for text in paragraph.xpath('.//w:r[w:rPr/w:vertAlign[@w:val="superscript"]]/w:t/text()', namespaces=NS))
    expected_stars = min(expected_stars, correspondence_symbols)
    after['positioned_text'][('superscript','*')] += expected_stars
    losses = [key for key, value in before.items()
              if (bool(value - after[key]) if isinstance(value, Counter) else after[key] < value)]
    media_changed = [name for name in source_zip.namelist()
                     if name.startswith(('word/media/', 'word/embeddings/'))
                     and name in replacements and sha256(source_zip.read(name)).digest() != sha256(replacements[name]).digest()]
    if media_changed:
        losses.append('source_binary_parts')
    def tokens(node):
        paragraphs = [node] if node.tag == qn('w:p') else node.xpath('.//w:p', namespaces=NS)
        text = '\n'.join(''.join(
            (e.text or '') if e.tag in {qn('w:t'), qn('m:t')} else
            ' ' if e.tag in {qn('w:br'), qn('w:tab')} else '' for e in p.iter()) for p in paragraphs)
        return Counter(re.findall(r'\w+', text.replace('\u00ad','').casefold()))
    source_tokens = Counter()
    for index, node in enumerate(body, 1):
        if f'block_{index:04d}' not in excluded_metadata_ids:
            source_tokens.update(tokens(node))
    missing_tokens = source_tokens - tokens(result_root.find('w:body', NS))
    for caption_label in ('fig','figure','рис','рисунок'):
        missing_tokens.pop(caption_label, None)
    if missing_tokens:
        losses.append('scientific_text')
    return {'passed': not losses, 'losses': losses,
            'scientific_tokens_missing': dict(missing_tokens),
            'author_markers_replaced_by_template_symbol': expected_stars,
            'positioned_loss_examples': list((before['positioned_text'] - after['positioned_text']).items())[:12],
            'counts_before': {k:v for k,v in before.items() if not isinstance(v, Counter)},
            'counts_after': {k:v for k,v in after.items() if not isinstance(v, Counter)},
            'source_binary_parts_changed': media_changed}
