"""Grounded layout actions. No model-provided XML, text, paths or measurements."""
from hashlib import sha256
from pathlib import Path
import re
from zipfile import ZipFile

from lxml import etree
from apps.template_workspace.v2.ooxml.namespaces import NS, qn
from .content_recovery import _facts
from .object_flow import display_width_twips, normalize_display_geometry
from .protected_blocks import code_nodes, visible_code_text, legacy_equation_ids
from .layout_fidelity import prop, pprops


KINDS_FOR_ACTION = {
    'restore_typography': {'text_typography', 'equation_typography'},
    'restore_paragraph': {'front_spacing', 'code_spacing'},
    'center_picture': {'figure_alignment', 'overlap'},
    'fit_picture': {'figure_alignment', 'overlap', 'equation_clipping'},
    'keep_caption': {'caption_detached'},
}


def node_path(root, node):
    path = []
    while node is not root:
        parent = node.getparent()
        if parent is None:
            return None
        path.append(parent.index(node)); node = parent
    return list(reversed(path))


def resolve(root, path):
    node = root
    for index in path:
        node = node[index]
    return node


def inventory(root, metadata, profile, layout, full_width_nodes, equation_ids=(), role_formats=None):
    """Freeze native target identity after all structural edits, before QA."""
    targets = []
    for p, meta in metadata.items():
        path = node_path(root, p)
        if path is None or p.tag != qn('w:p'):
            continue
        role = profile.roles.get(meta.role)
        own_text = ''.join(p.xpath('./w:r/w:t/text()|./w:hyperlink/w:r/w:t/text()', namespaces=NS)).strip()
        actions = []
        # Front shells/rubrics are template layout, not article illustrations.
        is_picture = (meta.zone == 'body' or meta.role in {'figure', 'figure_caption'}) and not own_text and bool(
            p.xpath('.//w:drawing|.//w:pict|.//w:object', namespaces=NS))
        is_equation = bool(p.xpath('.//m:oMath', namespaces=NS)) or any(
            o.get(qn('r:id')) in equation_ids or re.search(r'equation|mathtype', o.get('ProgID', ''), re.I)
            for o in p.xpath('.//o:OLEObject', namespaces=NS))
        if role and meta.role in {'body', 'title', 'author', 'affiliation', 'email', 'abstract', 'keywords', 'citation', 'figure_caption', 'table_caption'}:
            actions += ['restore_typography', 'restore_paragraph']
        if is_picture and display_width_twips(p) >= 1800 and not is_equation:
            actions += ['center_picture', 'fit_picture']
        following = p.getnext()
        fm = metadata.get(following)
        caption = following if is_picture and fm and fm.role == 'figure_caption' else None
        if caption is not None:
            actions.append('keep_caption')
        carrier = caption if caption is not None else p
        # AlternateContent contains equivalent Choice and Fallback captions;
        # PDF displays only one, so duplicate XML text is not a visible anchor.
        text = own_text or ''.join(carrier.xpath('.//w:t[not(ancestor::*[local-name()="Fallback"])]/text()', namespaces=NS)).strip()
        if not actions:
            continue
        pformat, rformat = (role_formats or {}).get(p, (role.typical_paragraph_formatting if role else {},
                                                      role.typical_run_formatting if role else {}))
        targets.append(dict(id=f'layout_{len(targets)+1:04d}', path=path, role=meta.role,
                            native_caption_box=meta.subtype == 'native_caption_box',
                            text=text[:240], group_id=meta.group_id, source_block=meta.block_id,
                            allowed_actions=actions,
                            paragraph_format=pformat, run_format=rformat,
                            width_twips=layout.printable_width_twips if p in full_width_nodes else layout.column_width_twips,
                            left_twips=0 if p in full_width_nodes else layout.body_left_twips,
                            right_twips=0 if p in full_width_nodes else layout.body_right_twips,
                            caption_path=node_path(root, caption) if caption is not None else None))
    return targets


def _normalize(text):
    return ''.join(re.findall(r'\w+', text.replace('\u00ad', '').casefold()))


def ground_targets(pdf, targets):
    """Unique text anchors only; never map by guessed page/block order."""
    texts = [_normalize(p.get_text()) for p in pdf]
    grounded = {}
    for target in targets:
        needle = _normalize(target['text'])[:100]
        if len(needle) < 20:
            continue
        matches = [i + 1 for i, text in enumerate(texts) if needle in text]
        if len(matches) == 1:
            grounded.setdefault(matches[0], []).append({
                'id':target['id'], 'role':target['role'], 'text':target['text'][:160],
                'allowed_actions':target['allowed_actions']})
    return grounded


def rendered_target_ids(pdf, targets):
    """Track text that actually survives PDF export, including page-spanning text.

    Unlike page grounding, duplicates need not resolve to one page. This is a
    conservative regression gate, not a claim that every pixel is visible.
    """
    text = _normalize(''.join(page.get_text() for page in pdf))
    return [t['id'] for t in targets if len(_normalize(t['text'])) >= 20
            and _normalize(t['text']) in text]


def structural_issues(path, targets):
    from .safe_word_editor import _role_run_defaults
    issues = []
    with ZipFile(path) as archive:
        root = etree.fromstring(archive.read('word/document.xml'))
        for target in targets:
            p = resolve(root, target['path'])
            if 'restore_typography' in target['allowed_actions']:
                expected = str(_role_run_defaults(target['role'], target['run_format'])['size'])
                runs = p.xpath('./w:r[w:t and not(w:rPr/w:vertAlign) and not(w:rPr/w:position)]', namespaces=NS)
                if target.get('native_caption_box'):
                    runs += p.xpath('.//w:txbxContent/w:p/w:r[w:t and not(w:rPr/w:vertAlign) and not(w:rPr/w:position)]', namespaces=NS)
                sizes = [r.find('w:rPr/w:sz', NS) for r in runs]
                if any(s is not None and s.get(qn('w:val')) != expected for s in sizes):
                    issues.append(dict(target_id=target['id'], action='restore_typography', kind='text_typography',
                                       severity='medium', source='ooxml', page=0, description='Размер основного текста отличается от профиля роли.'))
            if 'fit_picture' in target['allowed_actions'] and display_width_twips(p) > target['width_twips'] * 1.02:
                issues.append(dict(target_id=target['id'], action='fit_picture', kind='figure_alignment', severity='high',
                                   source='ooxml', page=0, description='Нативный рисунок шире доступной области.'))
    return issues


def select_actions(issues, targets, attempted=()):
    by_id = {t['id']:t for t in targets}
    actions, seen = [], set(attempted)
    for issue in issues:
        ident, operation = issue.get('target_id'), issue.get('action')
        target = by_id.get(ident)
        key = (ident, operation)
        if (not target or operation not in target['allowed_actions'] or key in seen
                or issue.get('kind') not in KINDS_FOR_ACTION.get(operation, set())):
            continue
        seen.add(key)
        actions.append({'target_id':ident, 'action':operation})
    return actions[:12]


def apply_actions(source, destination, targets, actions, expected_hash):
    """Layout-only transaction with exact per-paragraph content/order checks."""
    from .safe_word_editor import (_apply_run_profile, _apply_role_format, _resize_top_level_drawings,
                                   _serialize_xml, _write_package)
    source = Path(source)
    if sha256(source.read_bytes()).hexdigest() != expected_hash:
        raise ValueError('Stale repair candidate')
    by_id = {t['id']:t for t in targets}
    with ZipFile(source) as archive:
        root = etree.fromstring(archive.read('word/document.xml'))
        body = root.find('w:body', NS)
        before = [_facts(p) for p in body.xpath('.//w:p', namespaces=NS)]
        codes = [visible_code_text(p) for p in code_nodes(body)]
        equation_ids = legacy_equation_ids(archive)
        for item in actions:
            target = by_id[item['target_id']]
            action = item['action']
            if action not in target['allowed_actions'] or action not in KINDS_FOR_ACTION:
                raise ValueError('Unsupported repair operation')
            p = resolve(root, target['path'])
            if action == 'restore_typography':
                _apply_run_profile(p, target['role'], target['run_format'])
            elif action == 'restore_paragraph':
                # Typography function does not rewrite text; role helper also
                # decorates label runs, so retain content gates below.
                _apply_role_format(p, target['role'], target['paragraph_format'], target['run_format'])
            elif action == 'fit_picture':
                normalize_display_geometry(p, target['width_twips'], equation_ids)
                _resize_top_level_drawings(p, target['width_twips'])
            elif action == 'center_picture':
                prop(pprops(p), 'jc', val='center')
                ind = prop(pprops(p), 'ind', left=target['left_twips'], right=target['right_twips'], firstLine=0)
                for key in ('hanging', 'firstLineChars', 'leftChars', 'rightChars', 'hangingChars'):
                    ind.attrib.pop(qn('w:'+key), None)
            elif action == 'keep_caption':
                if target['caption_path'] is None:
                    raise ValueError('Missing native caption pairing')
                caption = resolve(root, target['caption_path'])
                if p.getnext() is not caption:
                    raise ValueError('Changed caption pairing')
                prop(pprops(p), 'keepNext', val=1)
                prop(pprops(caption), 'keepLines', val=1)
        after = [_facts(p) for p in body.xpath('.//w:p', namespaces=NS)]
        if before != after or codes != [visible_code_text(p) for p in code_nodes(body)]:
            raise ValueError('Repair changed native content')
        payload = _serialize_xml(root)
        if payload == archive.read('word/document.xml'):
            return False
        # Only this part can change: media/embeddings/rels/headers copied verbatim.
        _write_package(archive, Path(destination), {'word/document.xml':payload})
    return True
