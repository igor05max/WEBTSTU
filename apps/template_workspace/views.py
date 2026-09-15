from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods
from django.views.decorators.clickjacking import xframe_options_sameorigin

from .models import TemplateJob
from .services import expire_jobs, output_directory

FILES = {"pdf": ("result/result.pdf", "application/pdf", "article.pdf"),
         "docx": ("result/result.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "article.docx"),
         "latex": ("result/latex_project.zip", "application/zip", "latex_project.zip"),
         "tex": ("generated/latex/main.tex", "text/plain; charset=utf-8", "main.tex"),
         "report": ("result/conversion_report.json", "application/json", "report.json")}


@login_required
@require_http_methods(["GET", "POST"])
def workspace(request):
    """Retired entry point: never create or launch another V1 job."""
    if request.method == "POST":
        messages.info(request, 'Старая версия отключена. Выберите файлы заново в «Шаблон V2».')
    return redirect("template_workspace:v2_workspace")


def owned_job(request, job_id):
    return get_object_or_404(TemplateJob, pk=job_id, owner=request.user, kind="v1")


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
