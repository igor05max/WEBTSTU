import json
from io import BytesIO
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.cache import patch_cache_control
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.clickjacking import xframe_options_sameorigin

from apps.citations.analysis import analyze_claims, document_snapshot, text_snapshot
from apps.citations.forms import CitationSearchForm
from apps.citations.index import ensure_index, get_index_status, search_claim
from apps.citations.matching import (
    build_source_identity,
    claims_with_recommendations,
    remove_source_article,
    submission_authors_display,
)
from apps.citations.rerank import rerank_claims
from apps.citations.workspaces import (
    apply_to_source,
    create_workspace,
    load_workspace,
    prepare_result,
    read_prepared_result,
)
from apps.submissions.models import Submission, SubmissionStatus
from apps.submissions.document_preview import (
    DocumentPreviewError,
    build_docx_bytes_pdf,
)
from apps.submissions.services import add_submission_version
from apps.submissions.latex_projects import (
    LatexProjectError,
    replace_project_main_source,
)
from apps.submissions.template_processing import prepare_submission_template_by_id


def _read_form_source(form):
    submission = form.cleaned_data.get("submission")
    uploaded = form.cleaned_data.get("file")
    text = (form.cleaned_data.get("text") or "").strip()
    if submission is not None:
        version = submission.current_version
        with version.file.open("rb") as source:
            data = source.read()
        return data, version.file.name.rsplit("/", 1)[-1], submission.title, submission
    if uploaded is not None:
        return uploaded.read(), uploaded.name, uploaded.name, None
    return None, "pasted-text.txt", "Вставленный текст", None


def _submission_for_user(request, submission_id):
    submission = get_object_or_404(
        Submission.objects.select_related("author", "current_version").prefetch_related(
            "authors"
        ),
        pk=submission_id,
    )
    if submission.author_id != request.user.pk and not request.user.is_superuser:
        raise PermissionError("Этот материал недоступен.")
    return submission


def _submission_author_data(submission):
    if submission is None:
        return {"display": "", "is_selected": False}
    selected_authors = str(submission.get_authors_display() or "").strip()
    return {
        "display": selected_authors or submission_authors_display(submission),
        "is_selected": bool(selected_authors),
    }


def _workspace_submission(request, payload):
    submission_id = payload.get("submission_id")
    if not submission_id:
        raise ValueError("Рабочий набор не связан с материалом.")
    submission = _submission_for_user(request, submission_id)
    if submission.current_version_id != payload.get("source_version_id"):
        raise ValueError(
            "Версия материала уже изменилась. Запустите подбор источников заново."
        )
    return submission


def _workspace_result(payload, *, source_title):
    claims = payload.get("claims") or []
    return {
        "source_title": source_title,
        "file_name": payload.get("file_name") or "",
        "token": payload["token"],
        "can_apply_docx": payload.get("suffix") == ".docx",
        "can_apply_latex": payload.get("suffix") == ".tex",
        "claims": claims,
        "analyzed_claim_count": payload.get("analyzed_claim_count", len(claims)),
        "analysis": payload.get("analysis") or {},
        "index": payload.get("index_status") or {},
        "submission_id": payload.get("submission_id"),
        "selections": payload.get("selections") or [],
        "total_recommendations": sum(
            len(claim.get("recommendations") or []) for claim in claims
        ),
        "best_available": any(
            item.get("best_available")
            for claim in claims
            for item in (claim.get("recommendations") or [])
        ),
    }


@login_required
def workspace(request):
    result = None
    initial = {}
    selected_submission = None
    if request.method == "GET" and request.GET.get("submission"):
        initial["submission"] = request.GET.get("submission")
    form = CitationSearchForm(
        request.POST or None,
        request.FILES or None,
        user=request.user,
        initial=initial,
    )
    requested_submission_id = (
        request.POST.get("submission")
        if request.method == "POST"
        else request.GET.get("submission")
    )
    if str(requested_submission_id or "").isdigit():
        try:
            selected_submission = _submission_for_user(
                request,
                int(requested_submission_id),
            )
        except (PermissionError, Submission.DoesNotExist):
            selected_submission = None
    requested_workspace_token = (
        str(request.GET.get("workspace") or "")
        if request.method == "GET"
        else ""
    )
    if requested_workspace_token:
        try:
            workspace_payload = load_workspace(
                user_id=request.user.pk,
                token=requested_workspace_token,
            )
            if workspace_payload.get("submission_id"):
                workspace_submission = _workspace_submission(
                    request,
                    workspace_payload,
                )
                if (
                    selected_submission is not None
                    and selected_submission.pk != workspace_submission.pk
                ):
                    raise ValueError("Рабочий набор относится к другому материалу.")
                selected_submission = workspace_submission
            result = _workspace_result(
                workspace_payload,
                source_title=(
                    selected_submission.title
                    if selected_submission is not None
                    else workspace_payload.get("file_name") or "Документ"
                ),
            )
        except (ValueError, PermissionError, FileNotFoundError):
            messages.warning(
                request,
                "Сохранённый подбор источников больше недоступен. Запустите поиск заново.",
            )
    if request.method == "POST" and form.is_valid():
        file_bytes, file_name, source_title, selected_submission = _read_form_source(form)
        if file_bytes is None:
            snapshot = text_snapshot(form.cleaned_data["text"])
        else:
            snapshot = document_snapshot(file_bytes, file_name)
        if not (snapshot.get("text") or "").strip():
            form.add_error(
                None,
                snapshot.get("parse_error")
                or "Из документа не удалось извлечь текст для анализа.",
            )
        else:
            try:
                index_meta = ensure_index()
                analysis = analyze_claims(
                    snapshot,
                    max_claims=form.cleaned_data["max_claims"],
                )
                claims = analysis["claims"]
                analyzed_claim_count = len(claims)
                for claim in claims:
                    claim["recommendations"] = search_claim(claim)
                # Keep several nearest topical results available for manual
                # selection even when the local model finds no direct support.
                # Such candidates are explicitly marked in the interface.
                rerank_claims(claims, best_available_limit=8)
                remove_source_article(
                    claims,
                    build_source_identity(
                        snapshot,
                        source_title=(
                            selected_submission.title
                            if selected_submission is not None
                            else ""
                        ),
                        source_authors=submission_authors_display(selected_submission),
                    ),
                )
                claims = claims_with_recommendations(claims)
                analysis["claims"] = claims
                workspace_payload = create_workspace(
                    user_id=request.user.pk,
                    file_bytes=file_bytes,
                    file_name=file_name,
                    snapshot=snapshot,
                    claims=claims,
                    index_status=index_meta,
                    submission_id=(
                        selected_submission.pk if selected_submission is not None else None
                    ),
                    source_version_id=(
                        selected_submission.current_version_id
                        if selected_submission is not None
                        else None
                    ),
                )
                workspace_payload["analyzed_claim_count"] = analyzed_claim_count
                result = _workspace_result(
                    workspace_payload,
                    source_title=source_title,
                )
                if not claims:
                    messages.warning(
                        request,
                        "Подходящие новые источники не найдены.",
                    )
                if selected_submission is not None:
                    query = urlencode(
                        {
                            "submission": selected_submission.pk,
                            "workspace": workspace_payload["token"],
                        }
                    )
                    return redirect(
                        f"{reverse('citations:workspace')}?{query}"
                    )
            except Exception as exc:
                form.add_error(None, f"Поиск источников не завершён: {exc}")

    return render(
        request,
        "citations/workspace.html",
        {
            "form": form,
            "result": result,
            "index_status": get_index_status(),
            "selected_submission": selected_submission,
            "selected_submission_author_data": _submission_author_data(
                selected_submission
            ),
            "auto_analyze": bool(
                request.method == "GET"
                and selected_submission is not None
                and result is None
                and not requested_workspace_token
            ),
        },
    )


@login_required
@require_POST
def apply_citations(request):
    token = str(request.POST.get("workspace_token") or "")
    try:
        selections = json.loads(request.POST.get("selections") or "[]")
        if not isinstance(selections, list):
            raise ValueError("Некорректный список выбранных источников.")
        output, file_name = apply_to_source(
            user_id=request.user.pk,
            token=token,
            selections=selections,
        )
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        messages.error(request, str(exc))
        return render(
            request,
            "citations/workspace.html",
            {
                "form": CitationSearchForm(user=request.user),
                "result": None,
                "index_status": get_index_status(),
            },
            status=400,
        )
    is_latex = file_name.casefold().endswith(".tex")
    return FileResponse(
        output,
        as_attachment=True,
        filename=file_name,
        content_type=(
            "application/x-tex; charset=utf-8"
            if is_latex
            else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
    )


@login_required
@require_POST
def prepare_submission_result(request):
    token = str(request.POST.get("workspace_token") or "")
    try:
        selections = json.loads(request.POST.get("selections") or "[]")
        if not isinstance(selections, list):
            raise ValueError("Некорректный список выбранных источников.")
        payload = load_workspace(user_id=request.user.pk, token=token)
        _workspace_submission(request, payload)
        prepare_result(
            user_id=request.user.pk,
            token=token,
            selections=selections,
        )
    except (ValueError, PermissionError, FileNotFoundError, json.JSONDecodeError) as exc:
        messages.error(request, str(exc))
        return redirect("citations:workspace")
    return redirect("citations:submission_result_preview", token=token)


@login_required
@require_GET
def submission_result_preview(request, token):
    try:
        payload, result_bytes = read_prepared_result(
            user_id=request.user.pk,
            token=token,
        )
        submission = _workspace_submission(request, payload)
    except (ValueError, PermissionError, FileNotFoundError) as exc:
        messages.error(request, str(exc))
        return redirect("citations:workspace")
    return render(
        request,
        "citations/submission_result_preview.html",
        {
            "submission": submission,
            "workspace_token": token,
            "display_filename": payload.get("result_file_name") or "material-with-sources.docx",
            "is_latex": payload.get("result_suffix") == ".tex",
            "latex_source": (
                result_bytes.decode("utf-8", errors="replace")
                if payload.get("result_suffix") == ".tex"
                else ""
            ),
        },
    )


@login_required
@require_GET
@xframe_options_sameorigin
def submission_result_content(request, token):
    try:
        payload, result_bytes = read_prepared_result(
            user_id=request.user.pk,
            token=token,
        )
        _workspace_submission(request, payload)
        if payload.get("result_suffix") == ".tex":
            return HttpResponse(
                result_bytes,
                content_type="application/x-tex; charset=utf-8",
            )
        preview_bytes = build_docx_bytes_pdf(result_bytes)
    except (ValueError, PermissionError, FileNotFoundError) as exc:
        return HttpResponse(str(exc), status=404)
    except (DocumentPreviewError, OSError):
        return HttpResponse(
            "Не удалось показать DOCX. Скачайте подготовленный файл для просмотра.",
            status=422,
        )
    response = HttpResponse(
        preview_bytes,
        content_type="application/pdf",
    )
    response["Content-Disposition"] = (
        f'inline; filename="submission-{payload.get("submission_id")}-with-sources.pdf"'
    )
    patch_cache_control(response, private=True, no_store=True)
    return response


@login_required
@require_GET
def submission_result_download(request, token):
    try:
        payload, result_bytes = read_prepared_result(
            user_id=request.user.pk,
            token=token,
        )
        _workspace_submission(request, payload)
    except (ValueError, PermissionError, FileNotFoundError) as exc:
        messages.error(request, str(exc))
        return redirect("citations:workspace")
    is_latex = payload.get("result_suffix") == ".tex"
    return FileResponse(
        BytesIO(result_bytes),
        as_attachment=True,
        filename=payload.get("result_file_name") or "material-with-sources.docx",
        content_type=(
            "application/x-tex; charset=utf-8"
            if is_latex
            else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
    )


@login_required
@require_POST
def use_submission_result(request, token):
    try:
        payload, result_bytes = read_prepared_result(
            user_id=request.user.pk,
            token=token,
        )
        submission = _workspace_submission(request, payload)
        if submission.status not in {
            SubmissionStatus.DRAFT,
            SubmissionStatus.AUTO_CHECKING,
            SubmissionStatus.SUBMITTED,
        }:
            raise ValueError(
                "Источники можно добавить только до запуска маршрута согласования."
            )
        result_is_latex = payload.get("result_suffix") == ".tex"
        if submission.formatting_template_id and not result_is_latex:
            prepare_submission_template_by_id(
                submission.pk,
                template_id=submission.formatting_template_id,
                expected_version_id=submission.current_version_id,
                start_checks=False,
            )
            submission.refresh_from_db()
        if result_is_latex:
            prepared = replace_project_main_source(
                submission.current_version,
                result_bytes.decode("utf-8", errors="replace"),
            )
            material_file = prepared.main_file
        else:
            prepared = None
            material_file = ContentFile(
                result_bytes,
                name=payload.get("result_file_name") or "material-with-sources.docx",
            )
        add_submission_version(
            submission,
            request.user,
            material_file,
            comment="Добавлены выбранные источники из локальной RAG-системы.",
            expected_current_version_id=payload.get("source_version_id"),
            latex_project=prepared,
        )
    except (LatexProjectError, ValueError, PermissionError, FileNotFoundError) as exc:
        messages.error(request, str(exc))
        return redirect("citations:workspace")
    messages.success(
        request,
        "Создана новая версия с выбранными источниками. Автоматические проверки запущены.",
    )
    return redirect("submissions:detail", pk=submission.pk)


@login_required
@require_GET
def index_status(request):
    return JsonResponse(get_index_status())
