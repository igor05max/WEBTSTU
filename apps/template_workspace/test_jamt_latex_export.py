import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from zipfile import ZipFile

import pymupdf
from django.test import SimpleTestCase, override_settings
from docx import Document

from apps.template_workspace.v2.latex_export import export_jamt_latex
from apps.template_workspace.v2.exports import select_jamt_pdf
from hashlib import sha256
from apps.template_workspace.v2.timeouts import job_timeout_seconds


@override_settings(TEMPLATE_V2_VISUAL_REVIEW_ENABLED=False)
class LatexExportTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.text = ' '.join(['Original scientific measurements remain unchanged.'] * 12)
        self.source = self.root/'source.docx'
        doc = Document(); doc.add_paragraph(self.text); doc.save(self.source)
        self.baseline = self.root/'result.pdf'; self.make_pdf(self.baseline, self.text)
        (self.root/'result.docx').write_bytes(b'native companion')

    def make_pdf(self, path, text):
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_textbox(pymupdf.Rect(60, 100, 520, 700), text)
            pdf.save(path)

    def compile(self, project):
        pdf = project/'main.pdf'; self.make_pdf(pdf, self.text)
        return pdf, {'missing_glyphs': [], 'overfull_hbox_pt': [], 'overfull_vbox_pt': []}

    def test_compiler_failure_removes_stale_latex_but_keeps_native_results(self):
        native = self.baseline.read_bytes()
        for name in ('result-latex.pdf', 'result-latex.zip'):
            (self.root/name).write_bytes(b'stale')
        with patch('paper_formatter.latex_lab.typesetter.compile_pdf', side_effect=TimeoutError):
            report = export_jamt_latex(self.source, self.root, self.baseline)
        self.assertEqual(report['status'], 'unavailable')
        self.assertEqual(self.baseline.read_bytes(), native)
        self.assertEqual((self.root/'result.docx').read_bytes(), b'native companion')
        self.assertFalse((self.root/'result-latex.pdf').exists())
        self.assertFalse((self.root/'result-latex.zip').exists())

    def test_dropped_content_fails_gate_and_cannot_be_downloaded(self):
        self.text = 'Incomplete content'
        with patch('paper_formatter.latex_lab.typesetter.compile_pdf', side_effect=self.compile):
            report = export_jamt_latex(self.source, self.root, self.baseline)
        self.assertEqual(report['status'], 'unavailable')
        self.assertIn('text_coverage_regression', report['gate']['errors'])
        self.assertFalse((self.root/'result-latex.pdf').exists())

    @override_settings(TEMPLATE_V2_VISUAL_REVIEW_ENABLED=True)
    def test_success_includes_class_and_original_source_despite_ai_timeout(self):
        with patch('paper_formatter.latex_lab.typesetter.compile_pdf', side_effect=self.compile), patch(
                'paper_formatter.latex_lab.reference_review.compare_all_pages', side_effect=TimeoutError):
            report = export_jamt_latex(self.source, self.root, self.baseline)
        self.assertEqual(report['status'], 'completed')
        self.assertEqual(report['visual_review']['status'], 'unavailable')
        with ZipFile(self.root/'result-latex.zip') as archive:
            self.assertIn('jamt-reference.cls', archive.namelist())
            manifest = json.loads(archive.read('manifest.json'))
            self.assertEqual(manifest['source_text'].strip(), self.text)
        self.assertTrue(report['gate']['passed'])

    def test_worker_deadline_includes_optional_export_and_comparison_budget(self):
        with override_settings(JAMT_LATEX_EXPORT_ENABLED=False):
            before = job_timeout_seconds()
        with override_settings(JAMT_LATEX_EXPORT_ENABLED=True):
            self.assertGreaterEqual(job_timeout_seconds(), before + 16 * 60)

    def test_validated_latex_is_primary_and_word_pdf_is_preserved(self):
        original=self.baseline.read_bytes();candidate=self.root/'result-latex.pdf'
        self.make_pdf(candidate,'A different verified layout.');payload=candidate.read_bytes()
        result=select_jamt_pdf(self.root,{'status':'completed','pages':1,'pdf_sha256':sha256(original).hexdigest()},
            {'status':'completed','pages':1,'gate':{'passed':True},'pdf_sha256':sha256(payload).hexdigest()})
        self.assertEqual(result['engine'],'xelatex');self.assertEqual(self.baseline.read_bytes(),payload)
        self.assertEqual((self.root/'result-word.pdf').read_bytes(),original)

    def test_hash_mismatch_or_serious_visual_issue_keeps_word_as_primary(self):
        original=self.baseline.read_bytes();candidate=self.root/'result-latex.pdf';self.make_pdf(candidate,'Candidate')
        report={'status':'completed','pages':1,'gate':{'passed':True},'pdf_sha256':'wrong'}
        self.assertEqual(select_jamt_pdf(self.root,{'status':'completed'},report)['engine'],'word')
        report['pdf_sha256']=sha256(candidate.read_bytes()).hexdigest()
        report['visual_review']={'events':[{'mapping':{'A':'candidate'},'response':{'issues_A':[{'category':'overlap','severity':'high','description':'Overlap'}]}}]}
        self.assertEqual(select_jamt_pdf(self.root,{'status':'completed'},report)['engine'],'word')
        self.assertEqual(self.baseline.read_bytes(),original)
