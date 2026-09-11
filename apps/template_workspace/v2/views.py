from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from apps.checks.ai_client import get_api_base_url, get_provider_label, is_ai_configured
from apps.template_workspace.models import TemplateJob
from apps.template_workspace.v2.forms import TemplateV2JobForm
from apps.template_workspace.v2.services import analysis_directory, expire_v2_jobs, launch_v2_job


FILES = {
    "article": ("article_report.json", "application/json", "article_report.json"),
    "template": ("template_report.json", "application/json", "template_report.json"),
    "structure": ("article_structure.json", "application/json", "article_structure.json"),
    "profile": ("template_profile.json", "application/json", "template_profile.json"),
    "mapping": ("mapping_preview.json", "application/json", "mapping_preview.json"),
}


def ai_status():
    return {
        "configured": is_ai_configured(),
        "provider_label": get_provider_label(),
        "endpoint": get_api_base_url(),
    }


@login_required
@require_http_methods(["GET", "POST"])
def workspace(request):
    expire_v2_jobs(request.user)
    form = TemplateV2JobForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            get_user_model().objects.select_for_update().get(pk=request.user.pk)
            if TemplateJob.objects.filter(owner=request.user, kind="v2", status__in=["queued", "running"]).exists():
                form.add_error(None, "Дождитесь завершения текущего V2-анализа.")
            else:
                job = TemplateJob.objects.create(
                    owner=request.user,
                    kind="v2",
                    **form.cleaned_data,
                    article_name=form.cleaned_data["article"].name,
                    template_name=form.cleaned_data["template"].name,
                )
                transaction.on_commit(lambda: launch_v2_job(job))
                return redirect("template_workspace:v2_detail", job_id=job.pk)
    return render(request, "template_workspace/v2/workspace.html", {
        "active_sidebar_section": "template_v2",
        "ai_status": ai_status(),
        "form": form,
        "jobs": TemplateJob.objects.filter(owner=request.user, kind="v2")[:20],
    })


def owned_job(request, job_id):
    return get_object_or_404(TemplateJob, pk=job_id, owner=request.user, kind="v2")


def available_files(job):
    if job.pending or job.status == "failed":
        return []
    return [key for key, (path, _, _) in FILES.items() if (analysis_directory(job) / path).is_file()]


@login_required
@require_GET
def detail(request, job_id):
    expire_v2_jobs(request.user)
    job = owned_job(request, job_id)
    return render(request, "template_workspace/v2/detail.html", {
        "active_sidebar_section": "template_v2",
        "ai_status": ai_status(),
        "job": job,
        "files": available_files(job),
    })


@login_required
@require_GET
def progress(request, job_id):
    expire_v2_jobs(request.user)
    job = owned_job(request, job_id)
    return JsonResponse({"pending": job.pending, "status": job.status})


@login_required
@require_GET
def download(request, job_id, kind):
    job = owned_job(request, job_id)
    if kind not in available_files(job):
        raise Http404
    relative, content_type, name = FILES[kind]
    response = FileResponse((analysis_directory(job) / relative).open("rb"), content_type=content_type, as_attachment=True, filename=name)
    response["Cache-Control"] = "private, no-store"
    return response
