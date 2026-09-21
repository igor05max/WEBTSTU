"""Build the standalone JAMT template from the same contract as native Word.

Only maintained application files supply TeX commands. Article content reaches
the typesetter through the escaping/provenance bridge, never this compiler.
"""
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import shutil
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED

from apps.template_workspace.v2.styles.jamt import load_style


ROOT = Path(__file__).resolve().parent
TEMPLATE_FILES = {'jamt.cls', 'jamt-reference.cls', 'jamt-profile.tex',
                  'jamt-style.json', 'template-contract.json', 'TEMPLATE.md'}


def number(value):
    return format(float(value), '.8g')


def write_template_files(project):
    """Write a complete, versioned class and role catalogue into every export."""
    project = Path(project)
    project.mkdir(parents=True, exist_ok=True)
    style = load_style()
    latex = style['latex']
    source = json.dumps(style, ensure_ascii=False, indent=2) + '\n'
    (project / 'jamt-style.json').write_bytes(source.encode('utf-8'))
    geometry = {**{key: style['margins_twips'][key] / 20 for key in ('left', 'right', 'top', 'bottom')},
                'paperwidth': style['page_twips']['w'] / 20,
                'paperheight': style['page_twips']['h'] / 20}
    profile = ['% Generated from jamt-style.json. Regenerate; do not edit independently.',
               r'\JAMTGeometry{' + ','.join(key + '=' + number(value) + 'bp' for key, value in geometry.items())
               + f',headheight={latex["head_height_pt"]}pt,headsep={latex["head_sep_pt"]}pt,footskip={latex["foot_skip_pt"]}pt' + '}',
               r'\def\JAMTFont{' + style['font'] + '}',
               r'\def\JAMTVersion{' + style['version'] + '}',
               r'\def\JAMTColumns{' + str(style['columns']) + '}',
               r'\def\JAMTColumnGap{' + number(style['column_gap_twips'] / 20) + 'bp}',
               r'\def\JAMTBodySize{' + number(style['roles']['body']['pt']) + '}',
               r'\def\JAMTBodyLeading{' + number(latex['body_leading']) + '}',
               r'\def\JAMTFrontOffset{' + number(latex['front_offset_pt']) + 'pt}',
               r'\def\JAMTBodyIndent{' + number(style['roles']['body'].get('first_indent', 0)) + 'pt}',
               r'\def\JAMTTableRule{' + number(latex['table_rule_pt']) + 'pt}',
               r'\def\JAMTTableSize{' + number(latex['table_size']) + '}']
    alignments = {'center': r'\centering', 'right': r'\raggedleft',
                  'left': r'\RaggedRight', 'both': r'\justifying'}
    body_roles = {'body', 'funding_text', 'acknowledgements_text', 'conflict_text'}
    for name, spec in style['roles'].items():
        leading = latex['body_leading'] if name in body_roles else spec['pt'] * 1.15
        emphasis = (r'\bfseries ' if spec.get('bold') else '') + (r'\itshape ' if spec.get('italic') else '')
        args = [name, number(spec['pt']), number(leading), alignments[spec['align']],
                number(spec.get('first_indent', 0)), number(spec.get('before', 0)),
                number(spec.get('after', 0)), emphasis]
        profile.append(r'\JAMTDeclareRole' + ''.join('{' + value + '}' for value in args))
    (project / 'jamt-profile.tex').write_text('\n'.join(profile) + '\n', encoding='utf-8')
    for name in ('jamt.cls', 'jamt-reference.cls', 'TEMPLATE.md'):
        shutil.copy2(ROOT / name, project / name)
    fonts = project / 'fonts'
    fonts.mkdir(exist_ok=True)
    for name in ('texgyretermes-math.otf', 'GUST-FONT-LICENSE.txt'):
        shutil.copy2(ROOT.parent / 'vendor/fonts' / name, fonts / name)
    contract = {'template': 'JAMT', 'version': style['version'],
                'profile_sha256': sha256(source.encode('utf-8')).hexdigest(),
                'style_authority': 'apps/template_workspace/v2/styles/jamt.json',
                'word_contract': 'native OOXML from the same profile; no reverse conversion',
                'text_policy': 'highlight only; missing mandatory fields remain explicit',
                'files': {name: sha256((project / name).read_bytes()).hexdigest()
                          for name in sorted(TEMPLATE_FILES - {'template-contract.json'})}}
    (project / 'template-contract.json').write_text(json.dumps(contract, ensure_ascii=False, indent=2), encoding='utf-8')
    return contract


def build_master_project(project):
    contract = write_template_files(project)
    shutil.copy2(ROOT / 'master.tex', Path(project) / 'main.tex')
    return contract


def master_archive():
    """Static starter only: never includes a user's manuscript or credentials."""
    buffer = BytesIO()
    with tempfile.TemporaryDirectory(prefix='jamt-master-') as folder:
        root = Path(folder)
        build_master_project(root)
        with ZipFile(buffer, 'w', ZIP_DEFLATED) as archive:
            for path in sorted(root.rglob('*')):
                if path.is_file():
                    archive.write(path, path.relative_to(root).as_posix())
    buffer.seek(0)
    return buffer
