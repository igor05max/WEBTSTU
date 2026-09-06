from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods
from django.views.decorators.clickjacking import xframe_options_sameorigin

from .forms import TemplateJobForm
from .models import TemplateJob
from .services import expire_jobs, launch_job, output_directory

FILES = {"pdf": ("result/result.pdf", "application/pdf", "article.pdf"),
         "latex": ("result/latex_project.zip", "application/zip", "latex_project.zip"),
         "tex": ("generated/latex/main.tex", "text/plain; charset=utf-8", "main.tex"),
         "report": ("result/conversion_report.json", "application/json", "report.json")}


@login_required
@require_http_methods(["GET", "POST"])
def workspace(request):
    expire_jobs(request.user)
    form = TemplateJobForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            # Serialize submissions from the same account, including concurrent tabs.
            get_user_model().objects.select_for_update().get(pk=request.user.pk)
            if TemplateJob.objects.filter(owner=request.user, status__in=["queued", "running"]).exists():
                form.add_error(None, "Дождитесь завершения текущей сборки.")
            else:
                job = TemplateJob.objects.create(owner=request.user, **form.cleaned_data,
                    article_name=form.cleaned_data["article"].name,
                    template_name=form.cleaned_data["template"].name)
                transaction.on_commit(lambda: launch_job(job))
                return redirect("template_workspace:detail", job_id=job.pk)
    return render(request, "template_workspace/workspace.html", {
        "form": form, "jobs": TemplateJob.objects.filter(owner=request.user)[:20]})


def owned_job(request, job_id):
    return get_object_or_404(TemplateJob, pk=job_id, owner=request.user)


def available_files(job):
    if job.pending or job.status == "failed":
        return []
    return [key for key, (path, _, _) in FILES.items() if (output_directory(job) / path).is_file()]


@login_required
@require_GET
def detail(request, job_id):
    expire_jobs(request.user)
    job = owned_job(request, job_id)
    return render(request, "template_workspace/detail.html", {"job": job, "files": available_files(job)})


@login_required
@require_GET
def progress(request, job_id):
    expire_jobs(request.user)
    job = owned_job(request, job_id)
    return JsonResponse({"pending": job.pending, "status": job.status})


@login_required
@require_GET
@xframe_options_sameorigin
def download(request, job_id, kind):
    job = owned_job(request, job_id)
    if kind not in available_files(job):
        raise Http404
    relative, content_type, name = FILES[kind]
    response = FileResponse((output_directory(job) / relative).open("rb"),
        content_type=content_type, as_attachment=not (kind == "pdf" and request.GET.get("preview") == "1"), filename=name)
    response["Cache-Control"] = "private, no-store"
    return response
