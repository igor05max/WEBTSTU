"""Fail locally for unsafe inline edits, but keep the package-level safety gate.

Checkpoints use native element identity, not text matching: repeated values in
different table cells must never cause a repair in the wrong place.
"""
from collections import Counter
from copy import deepcopy
from contextlib import contextmanager
import logging
import re

from lxml import etree

from apps.template_workspace.v2.ooxml.namespaces import NS, qn
from .integrity import math_structure
from .protected_blocks import visible_code_text

logger = logging.getLogger(__name__)


class ContentPreservationError(ValueError):
    def __init__(self, report):
        self.report = report
        # Do not put manuscript text or formula values in application logs.
        super().__init__('Content preservation failed: ' + ', '.join(report.get('losses', [])))


def _facts(paragraph):
    text = ''.join((n.text or '') if n.tag in {qn('w:t'), qn('m:t')} else
                   ' ' if n.tag in {qn('w:br'), qn('w:tab')} else '' for n in paragraph.iter())
    text = text.replace('\u00ad', '').casefold()
    words = tuple(re.findall(r'\w+', text))
    # Keep signs/decimal punctuation too: "a-b" is not "a+b" and 1.5 is
    # not 15. Spaces/line breaks are layout, not scientific content here.
    scientific_text = re.sub(r'\s+', '', text)
    positioned = Counter(
        (p.get(qn('w:val')), c)
        for p in paragraph.xpath('.//w:rPr/w:vertAlign', namespaces=NS)
        if p.get(qn('w:val')) in {'superscript', 'subscript'}
        for c in ''.join(p.getparent().getparent().xpath('.//w:t/text()', namespaces=NS))
        if not c.isspace())
    # IDs/targets are semantic; drawing sizes, wrapping and run fonts are not.
    references = Counter((n.tag, key, value) for n in paragraph.iter()
                         if not any(parent.tag == qn('w:pPr') for parent in n.iterancestors())
                         for key, value in n.attrib.items() if key.startswith('{' + NS['r'] + '}'))
    fields = tuple((n.tag, tuple(sorted(n.attrib.items())), n.text or '')
                   for n in paragraph.iter() if n.tag in {
                       qn('w:fldChar'), qn('w:instrText'), qn('w:fldSimple'),
                       qn('w:bookmarkStart'), qn('w:bookmarkEnd'),
                       qn('w:footnoteReference'), qn('w:endnoteReference')})
    return dict(words=words, scientific_text=scientific_text, positioned_text=positioned, references=references, fields=fields,
                math_structures=tuple(math_structure(n) for n in paragraph.xpath('.//m:oMath', namespaces=NS)),
                objects=Counter(n.tag for n in paragraph.iter() if n.tag in {
                    qn('w:drawing'), qn('w:pict'), qn('w:object'), qn('w:hyperlink')}))


def _losses(before, after, role, *, correspondence_symbol=False):
    before, after = dict(before), dict(after)
    if role not in {None, 'body', 'code', 'abstract', 'affiliation',
                     'funding_text', 'acknowledgements_text', 'conflict_text'}:
        # Labels, journal glyphs and materialised numbering have explicit
        # punctuation conventions; their word/structure checks still apply.
        before.pop('scientific_text'); after.pop('scientific_text')
    if role in {'editorial_metadata', 'article_type', 'rubric'}:
        # These deliberately use the template's editorial shell/terminology.
        before.pop('words'); after.pop('words')
    elif role == 'figure_caption':
        labels = {'figure', 'fig', 'рис', 'рисунок'}
        for facts in (before, after):
            words = facts['words']
            facts['words'] = ('figure',) + words[1:] if words and words[0] in labels else words
    elif role in {'heading_1', 'heading_2', 'heading_3', 'reference_item'}:
        # Materialising existing Word numbering may add a numeric prefix only.
        words = after['words']
        while len(words) > len(before['words']) and words[0].isdigit():
            words = words[1:]
        after['words'] = words
    if role == 'author' and correspondence_symbol:
        after['positioned_text'] = after['positioned_text'].copy()
        key = ('superscript', '*')
        # Only the one corresponding-author marker is replaced by the envelope.
        if before['positioned_text'][key] - after['positioned_text'][key] == 1:
            after['positioned_text'][key] += 1
    return [key for key in before if before[key] != after[key]]


class LocalContentGuard:
    def __init__(self, body, metadata, protected_code=()):
        self.body = body
        self.checkpoints = []
        self.operation_recoveries = []
        for index, paragraph in enumerate(body.xpath('.//w:p', namespaces=NS), 1):
            ancestor = paragraph
            while ancestor.getparent() is not None and ancestor.getparent() is not body:
                ancestor = ancestor.getparent()
            meta = metadata.get(ancestor)
            self.checkpoints.append((paragraph, deepcopy(paragraph), _facts(paragraph),
                                     getattr(meta, 'role', None), getattr(meta, 'block_id', None),
                                     index, visible_code_text(paragraph) if paragraph in protected_code else None))

    @contextmanager
    def formatting(self, paragraph):
        """A malformed local formatting value must not abort unrelated prose.

        I/O, memory and unexpected runtime failures remain fatal. This boundary
        is only for operations that edit one attached paragraph in place.
        """
        backup = deepcopy(paragraph)
        try:
            yield
        except (ValueError, TypeError, KeyError, IndexError, ArithmeticError) as exc:
            if isinstance(exc, ContentPreservationError):
                raise
            if not any(parent is self.body for parent in paragraph.iterancestors()):
                raise ContentPreservationError({'losses': ['detached_paragraph_during_formatting']}) from exc
            for child in list(paragraph):
                paragraph.remove(child)
            for child in backup:
                paragraph.append(deepcopy(child))
            checkpoint = next(item for item in self.checkpoints if item[0] is paragraph)
            event = {'paragraph': checkpoint[5], 'block_id': checkpoint[4], 'role': checkpoint[3],
                     'losses': ['formatting_exception:' + type(exc).__name__],
                     'action': 'restored_paragraph_before_failed_operation'}
            self.operation_recoveries.append(event)
            logger.warning('Template V2 skipped formatting paragraph %s (%s): %s',
                           checkpoint[5], checkpoint[4], type(exc).__name__)

    def recover(self):
        events, unresolved = [], []
        # Inner text boxes first; a containing paragraph must not undo their repair.
        for paragraph, backup, before, role, block_id, index, code in reversed(self.checkpoints):
            attached = any(parent is self.body for parent in paragraph.iterancestors())
            if not attached:
                # Removing an empty spacer is a permitted layout edit.
                if any(before.values()):
                    unresolved.append({'paragraph': index, 'block_id': block_id, 'losses': ['detached_paragraph']})
                continue
            after = _facts(paragraph)
            replaced_star = (len(paragraph.xpath('.//w:sym', namespaces=NS)) >
                             len(backup.xpath('.//w:sym', namespaces=NS)))
            losses = _losses(before, after, role, correspondence_symbol=replaced_star)
            if code is not None and visible_code_text(paragraph) != code:
                losses.append('code_whitespace')
            if not losses:
                continue
            # Retain final paragraph geometry, including live section boundaries.
            # Restore original inline XML, never rebuild text/formulas as strings.
            for child in list(paragraph):
                if child.tag != qn('w:pPr'):
                    paragraph.remove(child)
            for child in backup:
                if child.tag != qn('w:pPr'):
                    paragraph.append(deepcopy(child))
            if _facts(paragraph) != before or (code is not None and visible_code_text(paragraph) != code):
                unresolved.append({'paragraph': index, 'block_id': block_id, 'losses': losses})
                continue
            event = {'paragraph': index, 'block_id': block_id, 'role': role,
                     'losses': losses, 'action': 'restored_original_inline_content'}
            events.append(event)
            logger.warning('Template V2 restored paragraph %s (%s): %s', index, block_id, ', '.join(losses))
        if unresolved:
            raise ContentPreservationError({'losses': ['unrecoverable_local_content'],
                                            'unresolved': unresolved, 'local_recoveries': events})
        return self.operation_recoveries + list(reversed(events))


def insert_rich_line_breaks(paragraph, lines):
    """Replace word-boundary whitespace with breaks without flattening rich runs."""
    if paragraph.xpath('.//w:br|.//w:tab|.//w:fldChar|.//w:instrText|.//w:fldSimple|'
                       './/m:oMath|.//w:drawing|.//w:pict|.//w:object|.//w:ins|.//w:del', namespaces=NS):
        return False
    nodes = paragraph.xpath('.//w:t', namespaces=NS)
    if any(n.getparent().tag != qn('w:r') for n in nodes):
        return False
    raw = ''.join(n.text or '' for n in nodes)
    words = list(re.finditer(r'\S+', raw))
    if [w.group() for w in words] != [w for line in lines for w in line.split()]:
        return False
    boundaries, end = [], 0
    for line in lines[:-1]:
        end += len(line.split())
        if not 0 < end < len(words):
            return False
        boundaries.append((words[end - 1].end(), words[end].start()))
    offset = 0
    for node in nodes:
        value = node.text or ''
        pieces, part = [], []
        for i, char in enumerate(value, offset):
            if any(i == start for start, stop in boundaries):
                pieces.extend([''.join(part), None]); part = []
            if not any(start <= i < stop for start, stop in boundaries):
                part.append(char)
        pieces.append(''.join(part))
        offset += len(value)
        if pieces == [value]:
            continue
        parent, position = node.getparent(), node.getparent().index(node)
        parent.remove(node)
        for piece in pieces:
            child = etree.Element(qn('w:br')) if piece is None else deepcopy(node)
            if piece is not None:
                child.text = piece
                child.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
            parent.insert(position, child); position += 1
    return bool(boundaries)
