from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from paper_formatter.latex_lab.bridge import Block
from paper_formatter.latex_lab.master_template import build_master_project, load_style, master_archive
from paper_formatter.latex_lab.typesetter import default_plan, render, validate_plan


class MasterTemplateTests(unittest.TestCase):
    def test_starter_is_self_contained_and_has_no_sample_author_metadata(self):
        with ZipFile(master_archive()) as archive:
            names = set(archive.namelist())
            self.assertTrue({'main.tex', 'jamt.cls', 'jamt-profile.tex', 'jamt-style.json',
                             'TEMPLATE.md', 'template-contract.json', 'fonts/texgyretermes-math.otf'} <= names)
            source = archive.read('main.tex').decode('utf-8')
            for field in ('DOI:', 'Received:', 'Accepted:', 'Published:', 'Year, volume, issue'):
                self.assertIn(field, source)
            for foreign in ('Tyutyunnik', 'Balabanov', '10.17277/jamt-'):
                self.assertNotIn(foreign, source)
            contract = json.loads(archive.read('template-contract.json'))
            self.assertEqual(contract['profile_sha256'], sha256(archive.read('jamt-style.json')).hexdigest())
            for name, digest in contract['files'].items():
                self.assertEqual(sha256(archive.read(name)).hexdigest(), digest, name)

    def test_profile_changes_reach_geometry_roles_and_native_word_contract(self):
        from apps.template_workspace.v2.styles.jamt import prepare_jamt_style
        from apps.template_workspace.v2.ooxml.namespaces import qn
        style = load_style()
        style['margins_twips']['left'] = 1200
        style['roles']['title']['pt'] = 15
        style['body_line_multiple'] = 1.05
        with tempfile.TemporaryDirectory() as folder, patch(
                'paper_formatter.latex_lab.master_template.load_style', return_value=style), patch(
                'apps.template_workspace.v2.styles.jamt.load_style', return_value=style):
            root = Path(folder)
            build_master_project(root)
            tex = (root/'jamt-profile.tex').read_text()
            self.assertIn('left=60bp', tex)
            self.assertIn(r'\JAMTDeclareRole{title}{15}', tex)
            carrier, _, profile = prepare_jamt_style(root/'word')
            from docx import Document
            self.assertEqual(Document(carrier).sections[0].left_margin.twips, 1200)
            self.assertEqual(profile.roles['title'].typical_run_formatting['size'], '30')
            spacing = profile.roles['body'].typical_paragraph_formatting['spacing']
            self.assertEqual(spacing[qn('w:line')], '252')

    def test_automated_export_uses_semantic_class_with_exact_escaped_content(self):
        block = Block('title', 'paragraph', 'title', 'front_matter', 'Original title', 'Original title')
        with tempfile.TemporaryDirectory() as folder:
            plan = default_plan([block])
            source = render([block], folder, plan).read_text()
            self.assertIn(r'\documentclass{jamt}', source)
            self.assertIn(']{title}{Original title}', source)
            self.assertNotIn(r'\fontsize{14}', source)
            self.assertEqual(plan['template']['version'], load_style()['version'])
            self.assertEqual(plan['template']['profile_sha256'], sha256((Path(folder)/'jamt-style.json').read_bytes()).hexdigest())

    def test_ai_cannot_shrink_type_or_expand_body_leading_outside_corpus(self):
        plan = default_plan([])
        proposed, accepted, rejected = validate_plan({'table_size': 9.5, 'body_leading': 13.2}, plan, [])
        self.assertEqual(proposed, plan)
        self.assertFalse(accepted)
        self.assertEqual(set(rejected), {'table_size', 'body_leading'})

    def test_qwen_receives_current_template_version_and_ranges(self):
        from paper_formatter.latex_lab.qwen import layout_prompt
        prompt = layout_prompt()
        self.assertIn(load_style()['version'], prompt)
        self.assertIn('"table_size": [10.0, 10.0]', prompt)
