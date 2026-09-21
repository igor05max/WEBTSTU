import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pymupdf
from django.test import SimpleTestCase, TestCase, override_settings

from apps.template_workspace.v2.readability import review_pdf, run_readability_review
from apps.template_workspace.v2.timeouts import job_timeout_seconds, stale_job_seconds


class VisualTimeoutTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pdf = Path(self.tmp.name) / 'page.pdf'
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((40, 40), 'A test page')
            pdf.save(self.pdf)

    def test_queue_wait_longer_than_old_limit_is_allowed(self):
        now = [0]

        def review(images, page, timeout):
            self.assertEqual(timeout, 300)
            now[0] += 68
            return []

        with patch('apps.template_workspace.v2.readability.time.monotonic', side_effect=lambda: now[0]):
            report = review_pdf(self.pdf, provider=SimpleNamespace(review=review))
        self.assertEqual(report['status'], 'reviewed')
        self.assertEqual(report['elapsed_seconds'], 68)
        self.assertEqual(report['budget_seconds'], 900)
        self.assertTrue(report['queue_wait_included_in_limits'])

    def test_page_timeout_cannot_exceed_remaining_total_budget(self):
        review = Mock(return_value=[])
        with patch('apps.template_workspace.v2.readability.time.monotonic', return_value=0):
            review_pdf(self.pdf, provider=SimpleNamespace(review=review), budget_seconds=75)
        self.assertEqual(review.call_args.kwargs['timeout'], 75)

    def test_timeout_does_not_claim_vpn_is_down_or_page_checked(self):
        provider = SimpleNamespace(review=Mock(side_effect=TimeoutError))
        report = review_pdf(self.pdf, provider=provider)
        self.assertEqual(report['pages_checked'], [])
        self.assertEqual(report['pages_not_checked'], [1])
        self.assertIn('возможна очередь модели', report['warnings'][0])

    @override_settings(TEMPLATE_V2_VISUAL_REVIEW_ENABLED=True,
                       TEMPLATE_V2_VISUAL_REVIEW_PAGE_TIMEOUT=420,
                       TEMPLATE_V2_VISUAL_REVIEW_BUDGET=1500)
    def test_configured_limits_reach_actual_visual_review(self):
        with patch('apps.submissions.document_preview.convert_word_path_to_pdf'), patch(
                'apps.template_workspace.v2.readability.review_pdf',
                return_value={'pages_checked': [], 'warnings': []}) as review:
            run_readability_review('unused.docx', self.tmp.name)
        self.assertEqual(review.call_args.kwargs['request_timeout'], 420)
        self.assertEqual(review.call_args.kwargs['budget_seconds'], 1500)

    @override_settings(TEMPLATE_V2_EDIT_CYCLE_ENABLED=False, TEMPLATE_V2_QWEN_ENABLED=True, TEMPLATE_V2_QWEN_TIMEOUT=300,
                       TEMPLATE_V2_VISUAL_REVIEW_ENABLED=True, TEMPLATE_V2_VISUAL_REVIEW_BUDGET=900)
    def test_outer_worker_deadlines_include_both_ai_phases_and_render_margin(self):
        self.assertEqual(job_timeout_seconds(), 1920)
        self.assertEqual(stale_job_seconds(), 2040)

    @override_settings(TEMPLATE_V2_QWEN_ENABLED=False, TEMPLATE_V2_VISUAL_REVIEW_ENABLED=False)
    def test_offline_worker_keeps_original_deadlines(self):
        self.assertEqual(job_timeout_seconds(), 18 * 60)
        self.assertEqual(stale_job_seconds(), 20 * 60)


@override_settings(TEMPLATE_V2_EDIT_CYCLE_ENABLED=False, TEMPLATE_V2_QWEN_ENABLED=True, TEMPLATE_V2_QWEN_TIMEOUT=300,
                   TEMPLATE_V2_VISUAL_REVIEW_ENABLED=True, TEMPLATE_V2_VISUAL_REVIEW_BUDGET=900)
class StaleJobTimeoutTests(TestCase):
    def test_legacy_expirer_does_not_abort_v2_waiting_in_queue(self):
        from datetime import timedelta
        from django.contrib.auth import get_user_model
        from django.utils import timezone
        from apps.template_workspace.models import TemplateJob
        from apps.template_workspace.services import expire_jobs
        from apps.template_workspace.v2.services import expire_v2_jobs
        owner = get_user_model().objects.create_user(username='timeout-test')
        legacy = TemplateJob.objects.create(owner=owner, kind='v1', status='running')
        current = TemplateJob.objects.create(owner=owner, kind='v2', status='running')
        jamt = TemplateJob.objects.create(owner=owner, kind='jamt', status='running')
        TemplateJob.objects.filter(owner=owner).update(updated_at=timezone.now()-timedelta(minutes=21))
        expire_jobs(owner)
        expire_v2_jobs(owner)
        legacy.refresh_from_db(); current.refresh_from_db(); jamt.refresh_from_db()
        self.assertEqual(legacy.status, 'failed')
        self.assertEqual(current.status, 'running')
        self.assertEqual(jamt.status, 'running')
        TemplateJob.objects.filter(pk=current.pk).update(updated_at=timezone.now()-timedelta(seconds=stale_job_seconds()+1))
        expire_v2_jobs(owner)
        current.refresh_from_db()
        self.assertEqual(current.status, 'failed')
        TemplateJob.objects.filter(pk=jamt.pk).update(updated_at=timezone.now()-timedelta(seconds=stale_job_seconds()+1))
        expire_v2_jobs(owner)
        jamt.refresh_from_db()
        self.assertEqual(jamt.status, 'failed')
