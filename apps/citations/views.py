import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from apps.citations.analysis import analyze_claims, document_snapshot, text_snapshot
from apps.citations.forms import CitationSearchForm
from apps.citations.index import ensure_index, get_index_status, search_claim
from apps.citations.rerank import rerank_claims
from apps.citations.workspaces import apply_to_docx, create_workspace


def _read_form_source(form):
    submission = form.cleaned_data.get("submission")
    uploaded = form.cleaned_data.get("file")
    text = (form.cleaned_data.get("text") or "").strip()
    if submission is not None:
        version = submission.current_version
        with version.file.open("rb") as source:
            data = source.read()
        return data, version.file.name.rsplit("/", 1)[-1], submission.title
    if uploaded is not None:
        return uploaded.read(), uploaded.name, uploaded.name
    return None, "pasted-text.txt", "Вставленный текст"


@login_required
def workspace(request):
    result = None
    form = CitationSearchForm(request.POST or None, request.FILES or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        file_bytes, file_name, source_title = _read_form_source(form)
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
                for claim in claims:
                    claim["recommendations"] = search_claim(claim)
                rerank_claims(claims)
                workspace_payload = create_workspace(
                    user_id=request.user.pk,
                    file_bytes=file_bytes,
                    file_name=file_name,
                    snapshot=snapshot,
                    claims=claims,
                    index_status=index_meta,
                )
                result = {
                    "source_title": source_title,
                    "file_name": file_name,
                    "token": workspace_payload["token"],
                    "can_apply_docx": workspace_payload["suffix"] == ".docx",
                    "claims": claims,
                    "analysis": analysis,
                    "index": index_meta,
                    "total_recommendations": sum(
                        len(claim.get("recommendations") or []) for claim in claims
                    ),
                }
                if not claims:
                    messages.warning(
                        request,
                        "Утверждения без ссылок не обнаружены. Попробуйте передать введение или обзор литературы.",
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
        output, file_name = apply_to_docx(
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
    return FileResponse(
        output,
        as_attachment=True,
        filename=file_name,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@login_required
@require_GET
def index_status(request):
    return JsonResponse(get_index_status())
