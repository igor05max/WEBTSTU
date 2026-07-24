from pathlib import Path

from django.contrib.auth.decorators import login_required
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from apps.directory.formatting_templates import get_latest_formatting_template
from apps.directory.journal_search import search_journals
from apps.directory.models import ArticleType, FormattingTemplate
from apps.directory.publication_topics import search_publication_topics
from document_template_engine import build_latex_template


def _get_article_type(request):
    value = str(request.GET.get("article_type") or "").strip()
    if not value.isdigit():
        return None
    return ArticleType.objects.filter(pk=int(value), is_active=True).first()


def _template_payload(template):
    if template is None:
        return None
    return {
        "id": template.id,
        "version": template.version_number,
        "file_name": Path(template.file.name).name,
        "status": template.analysis_status,
        "status_label": template.get_analysis_status_display(),
        "message": template.analysis_message,
        "rules": template.extracted_rules or {},
        "created_at": template.created_at.isoformat(),
        "uploaded_by": str(template.uploaded_by),
        "download_url": reverse("directory:formatting_template_download", args=[template.id]),
        "latex_download_url": reverse(
            "directory:formatting_template_latex_download",
            args=[template.id],
        ),
        "latex_preview_url": reverse(
            "directory:formatting_template_latex_preview",
            args=[template.id],
        ),
    }


@login_required
def journal_search(request):
    query = request.GET.get("q", "")
    journals = search_journals(query, limit=20)
    article_type = _get_article_type(request)
    results = [
        {
            "id": journal.id,
            "name": journal.name,
            "issn": journal.issn,
            "level": journal.white_list_level,
            "label": f"{journal.name} ({journal.issn})" if journal.issn else journal.name,
            "template": _template_payload(
                get_latest_formatting_template(article_type=article_type, journal=journal)
            )
            if article_type is not None
            else None,
        }
        for journal in journals
    ]
    return JsonResponse({"results": results})


@login_required
def publication_topic_search(request):
    query = request.GET.get("q", "")
    topics = search_publication_topics(query, limit=20)
    article_type = _get_article_type(request)
    results = []
    for topic in topics:
        template = (
            get_latest_formatting_template(article_type=article_type, publication_topic=topic)
            if article_type is not None
            else None
        )
        results.append(
            {
                "id": topic.id,
                "name": topic.name,
                "label": topic.name,
                "last_used_at": topic.last_used_at.isoformat() if topic.last_used_at else None,
                "template": _template_payload(template),
            }
        )
    return JsonResponse({"results": results})


@login_required
def formatting_template_detail(request, pk):
    template = get_object_or_404(
        FormattingTemplate.objects.select_related(
            "uploaded_by",
            "article_type",
            "journal",
            "publication_topic",
        ),
        pk=pk,
    )
    return JsonResponse({"template": _template_payload(template)})


@login_required
def formatting_template_download(request, pk):
    template = get_object_or_404(FormattingTemplate, pk=pk)
    return FileResponse(
        template.file.open("rb"),
        as_attachment=True,
        filename=Path(template.file.name).name,
    )


@login_required
def formatting_template_latex_download(request, pk):
    template = get_object_or_404(FormattingTemplate, pk=pk)
    source = build_latex_template(template.extracted_rules or {})
    response = HttpResponse(source, content_type="application/x-tex; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="formatting-template-{template.pk}-v{template.version_number}.tex"'
    )
    return response


@login_required
def formatting_template_latex_preview(request, pk):
    template = get_object_or_404(FormattingTemplate, pk=pk)
    latex_source = build_latex_template(template.extracted_rules or {})
    if isinstance(latex_source, bytes):
        latex_source = latex_source.decode("utf-8", errors="replace")
    return render(
        request,
        "directory/formatting_template_latex_preview.html",
        {
            "formatting_template": template,
            "latex_source": latex_source,
        },
    )
