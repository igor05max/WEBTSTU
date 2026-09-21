"""Conservative typography repairs for an existing, evidenced JAMT layout.

This is a structural gate, not a filename/reference-hash allowlist. It does not
claim visual correctness; the normal rendered review still runs afterwards.
"""
from collections import Counter
from pathlib import Path
import posixpath
import shutil
from zipfile import ZipFile

from lxml import etree

from ..ooxml.namespaces import NS, qn
from .jamt import load_style


def assess_existing_layout(report, structure):
    style = load_style()
    decisions = {b['id']: b for b in structure.blocks}
    counts = Counter(b['detected_role'] for b in structure.blocks)
    reasons = []
    from .identifiers import identifier_kind
    identifiers = [identifier_kind(p.text) for p in report.paragraphs
                   if decisions.get(p.id, {}).get('zone') == 'front_matter'
                   and decisions.get(p.id, {}).get('detected_role') == 'editorial_metadata']
    if identifiers.count('udc') == identifiers.count('doi') == 1 and 'pair' not in identifiers:
        # A former formatter could already match the fonts but put these fields
        # on separate lines. It needs the normal, content-preserving row repair.
        reasons.append('identifier_fields_separate')
    if counts['title'] < 2 or counts['abstract'] < 2 or counts['body'] < 12:
        reasons.append('insufficient_bilingual_journal_structure')
    if not counts['reference_item'] or not counts['author_information']:
        reasons.append('missing_reference_or_author_sections')
    column_counts = set()
    for section in report.sections:
        for key, value in style['page_twips'].items():
            if abs(int(section.page_size.get(qn('w:' + key), 0)) - value) > 10:
                reasons.append('page_geometry')
        for key in ('top', 'bottom', 'left', 'right'):
            if abs(int(section.margins.get(qn('w:' + key), 0)) - style['margins_twips'][key]) > 20:
                reasons.append('page_margins')
        cols = int(section.columns.get(qn('w:num'), 1))
        column_counts.add(cols)
        if cols == 2 and abs(int(section.columns.get(qn('w:space'), 0)) - style['column_gap_twips']) > 20:
            reasons.append('column_gap')
    if not {1, 2}.issubset(column_counts):
        reasons.append('missing_full_width_and_column_sections')
    # An unused header part alone is not evidence of live journal furniture.
    live_headers = {r['relationship_id'] for s in report.sections for r in s.header_refs}
    with ZipFile(report.source_path) as archive:
        relationships = etree.fromstring(archive.read('word/_rels/document.xml.rels'))
    live_parts = {posixpath.normpath(posixpath.join('word', r.get('Target', ''))) for r in relationships
                  if r.get('Id') in live_headers and r.get('TargetMode') != 'External'}
    if not any(h.part in live_parts and 'Journal of Advanced Materials and Technologies' in h.text for h in report.headers):
        reasons.append('journal_furniture_missing')
    expected, matching, candidates, font_repairs = 0, 0, [], []
    for paragraph in report.paragraphs:
        role = decisions.get(paragraph.id, {}).get('detected_role')
        rule = style['roles'].get(role)
        if not rule or role in {'rubric', 'editorial_metadata', 'copyright_metadata', 'author_bio'}:
            continue
        if paragraph.drawings or paragraph.formulas or not paragraph.text.strip():
            continue
        weights = Counter()
        fonts = Counter()
        for run in paragraph.runs:
            fmt = run.effective_formatting
            # Superscripts, footnote markers and symbols are deliberately smaller.
            if fmt.get('vertical_align') or fmt.get('vertical_alignment') or fmt.get('vertAlign'):
                continue
            size = fmt.get('size')
            if size is not None and run.text.strip():
                weights[str(size)] += len(run.text.strip())
                face = fmt.get('fonts', {}).get(qn('w:ascii')) or fmt.get('fonts', {}).get(qn('w:hAnsi'))
                if face: fonts[face] += len(run.text.strip())
        if not weights:
            continue
        dominant, weight = weights.most_common(1)[0]
        expected += 1
        wanted = str(round(rule['pt'] * 2))
        dominant_font = fonts.most_common(1)[0][0] if fonts else None
        if dominant == wanted and dominant_font == style['font']:
            matching += 1
        if dominant != wanted and weight / sum(weights.values()) >= .85:
            candidates.append({'id': paragraph.id, 'role': role, 'old_size': dominant, 'new_size': wanted})
        if dominant_font and dominant_font != style['font'] and fonts[dominant_font]/sum(fonts.values()) >= .85:
            font_repairs.append({'id': paragraph.id, 'old_font': dominant_font, 'new_font': style['font']})
    ratio = matching / expected if expected else 0
    if expected < 20 or ratio < .85:
        reasons.append('typography_not_close_to_reference')
    return {'eligible': not reasons, 'reasons': sorted(set(reasons)),
            'matching_paragraph_fraction': round(ratio, 4),
            'checked_paragraphs': expected, 'size_repairs': candidates,
            'font_repairs': font_repairs,
            'style_version': style['version'], 'visual_quality_confirmed': False}


def preserve_existing_layout(source, output, report, structure):
    """Return editor result if eligible, otherwise let the full formatter run.

    Keep the complete original package when no repair is needed. For outliers,
    repair dominant fonts, sizes and identifier alignment on ordinary paragraphs;
    keep section geometry, paragraph boundaries, fields, formulas and media intact.
    """
    assessment = assess_existing_layout(report, structure)
    if not assessment['eligible']:
        return None
    from ..editor.safe_word_editor import SafeWordEditorResult, _serialize_xml, _write_package
    from ..editor.integrity import native_integrity
    from ..editor.content_recovery import ContentPreservationError
    source, output = Path(source), Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    applied = []
    with ZipFile(source) as archive:
        root = etree.fromstring(archive.read('word/document.xml'))
        children = list(root.find('w:body', NS))
        for change in assessment['size_repairs']:
            paragraph = children[int(change['id'].split('_')[-1]) - 1]
            if paragraph.tag != qn('w:p') or paragraph.xpath('.//w:object|.//w:drawing|.//w:pict|.//m:oMath', namespaces=NS):
                continue
            for run in paragraph.xpath('./w:r|./w:hyperlink/w:r', namespaces=NS):
                if not run.findall('w:t', NS) or run.find('w:rPr/w:vertAlign', NS) is not None:
                    continue
                properties = run.find('w:rPr', NS)
                if properties is None:
                    properties = etree.Element(qn('w:rPr')); run.insert(0, properties)
                size = properties.find('w:sz', NS)
                if size is not None and size.get(qn('w:val')) != change['old_size']:
                    continue
                if size is None:
                    size = etree.SubElement(properties, qn('w:sz'))
                size.set(qn('w:val'), change['new_size'])
                complex_size = properties.find('w:szCs', NS)
                if complex_size is not None and complex_size.get(qn('w:val')) == change['old_size']:
                    complex_size.set(qn('w:val'), change['new_size'])
                applied.append(change['id'])
        for change in assessment['font_repairs']:
            paragraph = children[int(change['id'].split('_')[-1]) - 1]
            for run in paragraph.xpath('./w:r|./w:hyperlink/w:r', namespaces=NS):
                if not run.findall('w:t', NS) or run.xpath('./w:object|./w:drawing|./w:pict|./w:sym', namespaces=NS):
                    continue
                properties = run.find('w:rPr', NS)
                if properties is None:
                    properties = etree.Element(qn('w:rPr')); run.insert(0, properties)
                fonts = properties.find('w:rFonts', NS)
                if fonts is not None and fonts.get(qn('w:ascii')) not in {None, change['old_font']}:
                    continue
                if fonts is None:
                    fonts = etree.SubElement(properties, qn('w:rFonts'))
                for name in list(fonts.attrib):
                    if name.endswith('Theme'): del fonts.attrib[name]
                for script in ('ascii', 'hAnsi', 'cs'):
                    fonts.set(qn('w:'+script), change['new_font'])
                applied.append(change['id'])
        from .identifiers import repair_existing_identifiers
        identifier_repairs = repair_existing_identifiers(root, report, structure, load_style())
        applied.extend(identifier_repairs)
        assessment['identifier_repairs'] = identifier_repairs
        replacements = {'word/document.xml': _serialize_xml(root)} if applied else {}
        integrity = native_integrity(archive, root, replacements)
        if not integrity['passed']:
            raise ContentPreservationError(integrity)
        if applied:
            _write_package(archive, output, replacements)
    if not applied and source.resolve() != output.resolve():
        shutil.copy2(source, output)
    return SafeWordEditorResult(str(output), changes=[
        'Сохранена исходная журнальная вёрстка: секции, объекты, таблицы, формулы и колонтитулы.',
        f'Исправлена типографика или положение реквизитов в абзацах: {len(set(applied))}.' if applied else
        'Отклонений гарнитуры и кегля не обнаружено; DOCX сохранён без изменений. Внешний вид проверяется отдельно.'
    ], warnings=[], metrics={
        'reference_fidelity': assessment, 'formatted_paragraphs': len(set(applied)),
        'native_integrity': integrity, 'layout_targets': [],
        'planning_provider': 'reference_fidelity', 'headers_footers_copied': False,
        'layout_topology_preserved': True, 'byte_identical': not applied,
        'local_content_recovery_count': 0,
    })
