"""Optional grounded local-model refinement for document metadata extraction."""

from __future__ import annotations

from django.conf import settings

from apps.checks.gemini_client import (
    GeminiAPIError,
    extract_response_text,
    generate_content,
    get_configured_model,
    get_provider,
    is_ai_configured,
)
from apps.submissions.article_extraction import (
    article_to_legacy_metadata,
    build_semantic_review_prompt,
    refine_article_with_model,
)
from apps.submissions.document_analysis import analyze_document_bytes


def analyze_document(data, file_name, *, use_ai=None):
    """Run deterministic parsing and use AI only for explicitly ambiguous fields."""

    snapshot = analyze_document_bytes(data, file_name)
    enabled = (
        bool(getattr(settings, "SUBMISSION_DOCUMENT_EXTRACTION_AI_ENABLED", False))
        if use_ai is None
        else bool(use_ai)
    )
    diagnostics = {
        "enabled": enabled,
        "status": "disabled",
        "provider": get_provider(),
        "model": "",
        "error": "",
    }
    snapshot["semantic_refinement"] = diagnostics
    if not enabled:
        return snapshot
    if snapshot.get("parse_error") and not snapshot.get("paragraphs"):
        diagnostics["status"] = "no_text"
        diagnostics["error"] = (
            "Семантическая классификация невозможна: парсер не получил "
            "ни одного текстового блока."
        )
        return snapshot
    if not (snapshot.get("article") or {}).get("needs_review"):
        diagnostics["status"] = "not_needed"
        return snapshot
    if get_provider() != "openai_compatible" or not is_ai_configured():
        diagnostics["status"] = "unavailable"
        diagnostics["error"] = (
            "Локальный OpenAI-совместимый API не настроен или недоступен."
        )
        return snapshot

    model = str(
        getattr(settings, "SUBMISSION_DOCUMENT_EXTRACTION_MODEL", "")
        or get_configured_model()
    ).strip()
    timeout = int(
        getattr(
            settings,
            "SUBMISSION_DOCUMENT_EXTRACTION_TIMEOUT",
            getattr(settings, "AI_REQUEST_TIMEOUT", 120),
        )
    )

    def complete_json(prompt):
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": 4096,
                "responseMimeType": "application/json",
            },
        }
        response, used_model = generate_content(
            payload,
            model=model,
            timeout=timeout,
        )
        diagnostics["model"] = used_model
        return extract_response_text(response)

    try:
        refined = refine_article_with_model(
            snapshot,
            snapshot["article"],
            complete_json=complete_json,
        )
    except (GeminiAPIError, ValueError) as exc:
        diagnostics["status"] = "failed"
        diagnostics["error"] = str(exc)
        return snapshot

    snapshot["article"] = refined
    snapshot["metadata"] = article_to_legacy_metadata(refined)
    diagnostics["status"] = "completed"
    diagnostics["prompt_policy"] = "source_block_ids_only"
    diagnostics["review_items_remaining"] = len(refined.get("needs_review") or [])
    return snapshot
