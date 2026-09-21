from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from apps.checks.ai_client import get_api_base_url
from apps.template_workspace.models import TemplateJob
from apps.template_workspace.v2.forms import TemplateV2JobForm, JamtStyleJobForm
from apps.template_workspace.v2.services import analysis_directory, expire_v2_jobs, launch_v2_job, NATIVE_JOB_KINDS


FILES = {
    "docx": ("result.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "result.docx"),
    "pdf": ("result.pdf", "application/pdf", "result.pdf"),
    "word_pdf": ("result-word.pdf", "application/pdf", "result-word.pdf"),
    "latex_pdf": ("result-latex.pdf", "application/pdf", "result-latex.pdf"),
    "latex_source": ("result-latex.zip", "application/zip", "result-latex.zip"),
    "latex_report": ("latex-export-report.json", "application/json", "latex-export-report.json"),
    "export": ("export_report.json", "application/json", "export_report.json"),
    "editorial": ("editorial-review.json", "application/json", "editorial-review.json"),
    "style": ("style_source.json", "application/json", "jamt-style.json"),
    "article": ("article_report.json", "application/json", "article_report.json"),
    "template": ("template_report.json", "application/json", "template_report.json"),
    "structure": ("article_structure.json", "application/json", "article_structure.json"),
    "profile": ("template_profile.json", "application/json", "template_profile.json"),
    "mapping": ("mapping_preview.json", "application/json", "mapping_preview.json"),
    "editor": ("editor_report.json", "application/json", "editor_report.json"),
    "readability": ("readability_report.json", "application/json", "readability_report.json"),
    "quality": ("quality_cycle_report.json", "application/json", "quality_cycle_report.json"),
    "preview": ("readability-preview.pdf", "application/pdf", "result-preview.pdf"),
}


def mode_context(job_kind):
    if job_kind not in NATIVE_JOB_KINDS:
        raise Http404
    return {
        "is_style": job_kind == "jamt",
        "active_sidebar_section": "jamt_style" if job_kind == "jamt" else "template_v2",
        **{name + "_route": f"template_workspace:{job_kind}_{name}" for name in ("workspace", "detail", "progress", "download")},
    }


def ai_status():
    endpoint = get_api_base_url(settings.TEMPLATE_V2_QWEN_BASE_URL or None)
    return {
        "configured": bool(settings.TEMPLATE_V2_QWEN_ENABLED and endpoint),
        "visual_configured": bool(getattr(settings, 'TEMPLATE_V2_VISUAL_REVIEW_ENABLED', False)
                                  and endpoint and settings.TEMPLATE_V2_QWEN_MODEL),
        "provider_label": settings.TEMPLATE_V2_QWEN_MODEL,
        "endpoint": endpoint,
    }


@login_required
@require_http_methods(["GET", "POST"])
def workspace(request, job_kind="v2"):
    context = mode_context(job_kind)
    expire_v2_jobs(request.user)
    form_class = JamtStyleJobForm if job_kind == "jamt" else TemplateV2JobForm
    form = form_class(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            get_user_model().objects.select_for_update().get(pk=request.user.pk)
            if TemplateJob.objects.filter(owner=request.user, kind__in=NATIVE_JOB_KINDS, status__in=["queued", "running"]).exists():
                form.add_error(None, "Дождитесь завершения текущего оформления статьи.")
            else:
                job = TemplateJob.objects.create(
                    owner=request.user,
                    kind=job_kind,
                    **form.cleaned_data,
                    article_name=form.cleaned_data["article"].name,
                    template_name=form.cleaned_data["template"].name if job_kind == "v2" else "",
                )
                transaction.on_commit(lambda: launch_v2_job(job))
                return redirect(context["detail_route"], job_id=job.pk)
    return render(request, "template_workspace/v2/workspace.html", {
        **context,
        "ai_status": ai_status(),
        "form": form,
        "jobs": TemplateJob.objects.filter(owner=request.user, kind=job_kind)[:20],
    })


def owned_job(request, job_id, job_kind="v2"):
    return get_object_or_404(TemplateJob, pk=job_id, owner=request.user, kind=job_kind)


def available_files(job):
    if job.pending or job.status == "failed":
        return []
    return [key for key, (path, _, _) in FILES.items() if (analysis_directory(job) / path).is_file()]


def visual_review_context(job):
    if job.pending or job.status == 'failed':
        return None
    try:
        report = json.loads((analysis_directory(job) / 'readability_report.json').read_text(encoding='utf-8'))
        if not isinstance(report, dict):
            return None
    except (OSError, ValueError):
        return None
    labels = {'reviewed': 'Проверка завершена', 'partial': 'Проверена часть страниц',
              'unavailable': 'Проверка недоступна', 'disabled': 'AI-проверка выключена'}
    checked = report.get('pages_checked') or []
    issues = [item for item in report.get('issues', []) if item.get('source') == 'qwen-vision']
    return {
        'label': labels.get(report.get('status'), 'Проверка не завершена'),
        'checked_count': len(checked), 'total': report.get('pages_total'),
        'unchecked': report.get('pages_not_checked', []), 'issues': issues,
        'has_findings': bool(checked),
    }


@login_required
@require_GET
def detail(request, job_id, job_kind="v2"):
    expire_v2_jobs(request.user)
    job = owned_job(request, job_id, job_kind)
    return render(request, "template_workspace/v2/detail.html", {
        **mode_context(job_kind),
        "ai_status": ai_status(),
        "job": job,
        "files": available_files(job),
        "ai_review": visual_review_context(job),
        "latex_review": latex_review_context(job),
        "pdf_engine": pdf_engine(job),
        "editorial_review": editorial_review_context(job),
    })


def editorial_review_context(job):
    if job.kind != 'jamt' or job.pending or job.status == 'failed':
        return None
    try:
        report = json.loads((analysis_directory(job) / 'editorial-review.json').read_text(encoding='utf-8'))
        if not isinstance(report, dict):return None
    except (OSError, ValueError):
        return None
    ai=report.get('ai', {})
    return {'missing':report.get('missing_fields', []), 'issues':report.get('issues', []),
            'count':report.get('missing_count',0)+report.get('highlighted_findings',0),
            'checked':len(ai.get('checked',[])), 'unchecked':len(ai.get('unchecked',[])),
            'ai_status':{'disabled':'AI-проверка текста выключена','unavailable':'AI-проверка текста недоступна',
                         'partial':'AI проверил часть текста','reviewed':'AI-проверка текста завершена'}.get(ai.get('status'),'AI-проверка не завершена')}


def pdf_engine(job):
    try:
        report=json.loads((analysis_directory(job)/'export_report.json').read_text(encoding='utf-8'))
        return 'LaTeX' if report.get('engine')=='xelatex' else 'Word'
    except (OSError,ValueError,AttributeError):
        return 'Word'


def latex_review_context(job):
    if job.kind != 'jamt' or job.pending or job.status == 'failed':
        return None
    try:
        report = json.loads((analysis_directory(job) / 'latex-export-report.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(report, dict):
        return None
    visual = report.get('visual_review') or {}
    issues = []
    for event in visual.get('events', []):
        letter = next((key for key, value in event.get('mapping', {}).items() if value == 'candidate'), None)
        for issue in event.get('response', {}).get('issues_'+str(letter), []):
            issues.append({'page': event['candidate_page'], 'description': issue['description']})
    return {'message': report.get('message'), 'has_review': bool(visual),
            'checked': len(visual.get('candidate_pages_checked', [])),
            'total': visual.get('candidate_pages', report.get('pages', 0)), 'issues': issues,
            'reference_checked': len(visual.get('reference_pages_checked', [])),
            'reference_total': visual.get('reference_pages', 0),
            'complete': visual.get('status') == 'reviewed'}


@login_required
@require_GET
def progress(request, job_id, job_kind="v2"):
    expire_v2_jobs(request.user)
    job = owned_job(request, job_id, job_kind)
    return JsonResponse({"pending": job.pending, "status": job.status, "message": job.message})


@login_required
@require_GET
def download(request, job_id, kind, job_kind="v2"):
    job = owned_job(request, job_id, job_kind)
    if kind not in available_files(job):
        raise Http404
    relative, content_type, name = FILES[kind]
    response = FileResponse((analysis_directory(job) / relative).open("rb"), content_type=content_type, as_attachment=True, filename=name)
    response["Cache-Control"] = "private, no-store"
    return response
