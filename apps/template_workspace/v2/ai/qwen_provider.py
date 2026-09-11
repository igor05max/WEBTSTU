from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings

from apps.checks.ai_client import (
    AIProviderError,
    extract_response_text,
    generate_content,
    get_api_base_url,
    get_configured_model,
    is_ai_configured,
)
from apps.template_workspace.v2.models.document_info import DocumentReport, ParagraphInfo


@dataclass
class QwenProviderDiagnostics:
    rules_processed: int = 0
    qwen_candidates: int = 0
    qwen_sent: int = 0
    qwen_changed: int = 0
    qwen_accepted: int = 0
    qwen_errors: int = 0
    qwen_timeouts: int = 0
    qwen_bad_json: int = 0
    model: str = ""
    endpoint: str = ""
    errors: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rules_processed": self.rules_processed,
            "qwen_candidates": self.qwen_candidates,
            "qwen_sent": self.qwen_sent,
            "qwen_changed": self.qwen_changed,
            "qwen_accepted": self.qwen_accepted,
            "qwen_errors": self.qwen_errors,
            "qwen_timeouts": self.qwen_timeouts,
            "qwen_bad_json": self.qwen_bad_json,
            "model": self.model,
            "endpoint": self.endpoint,
            "errors": self.errors,
            "decisions": self.decisions,
        }


class QwenProvider:
    """V2-only role classifier provider for ambiguous Word blocks.

    Qwen is a remote/local OpenAI-compatible service reached by the Django server
    through VPN.  This provider never edits text or DOCX structure; it only returns
    a semantic role proposal for a block that deterministic rules already marked as
    low confidence or review-worthy.
    """

    def __init__(
        self,
        *,
        allowed_roles: tuple[str, ...],
        model: str | None = None,
        timeout: int | None = None,
        max_blocks: int | None = None,
        context_radius: int | None = None,
        min_confidence: float | None = None,
    ):
        self.allowed_roles = tuple(allowed_roles)
        self.model = model if model is not None else _setting_or_env("TEMPLATE_V2_QWEN_MODEL", getattr(settings, "AI_MODEL", ""))
        self.timeout = timeout if timeout is not None else _env_int("TEMPLATE_V2_QWEN_TIMEOUT_SECONDS", min(int(getattr(settings, "AI_REQUEST_TIMEOUT", 120) or 120), 25))
        self.max_blocks = max_blocks if max_blocks is not None else _env_int("TEMPLATE_V2_QWEN_MAX_BLOCKS", 40)
        self.context_radius = context_radius if context_radius is not None else _env_int("TEMPLATE_V2_QWEN_CONTEXT_RADIUS", 3)
        self.min_confidence = min_confidence if min_confidence is not None else _env_float("TEMPLATE_V2_QWEN_MIN_CONFIDENCE", 0.60)
        self.diagnostics = QwenProviderDiagnostics(endpoint=get_api_base_url(), model=self.model)

    def is_configured(self) -> bool:
        return is_ai_configured()

    def classify_ambiguous(
        self,
        report: DocumentReport,
        ambiguous: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        self.diagnostics.qwen_candidates += len(ambiguous)
        if not ambiguous or not self.is_configured():
            if not self.is_configured():
                self._record_error("not_configured", "AI_BASE_URL is not configured")
            return {}

        paragraphs = [p for p in report.paragraphs if p.normalized_text]
        by_id = {p.id: (index, p) for index, p in enumerate(paragraphs)}
        overrides: dict[str, dict[str, Any]] = {}

        for current in ambiguous[: self.max_blocks]:
            block_id = str(current.get("block_id") or "")
            pair = by_id.get(block_id)
            if pair is None:
                continue
            index, paragraph = pair
            self.diagnostics.qwen_sent += 1
            proposal = self._classify_one(report, paragraphs, index, paragraph, current)
            if proposal is None:
                continue
            role = str(proposal.get("role") or "")
            confidence = _safe_float(proposal.get("confidence"))
            if role not in self.allowed_roles or confidence < self.min_confidence:
                continue
            patch = {
                "role": role,
                "confidence": confidence,
                "reason": str(proposal.get("reason") or "Qwen role classification")[:500],
                "source": "qwen",
            }
            overrides[block_id] = patch
            self.diagnostics.qwen_accepted += 1
            if role != current.get("role_hint"):
                self.diagnostics.qwen_changed += 1
            self.diagnostics.decisions.append(
                {
                    "block_id": block_id,
                    "rule_role": current.get("role_hint"),
                    "qwen_role": role,
                    "confidence": round(confidence, 3),
                    "changed": role != current.get("role_hint"),
                    "reason": patch["reason"],
                }
            )
        return overrides

    def __call__(self, report: DocumentReport, ambiguous: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return self.classify_ambiguous(report, ambiguous)

    def _classify_one(
        self,
        report: DocumentReport,
        paragraphs: list[ParagraphInfo],
        index: int,
        paragraph: ParagraphInfo,
        current: dict[str, Any],
    ) -> dict[str, Any] | None:
        payload = self._payload(report, paragraphs, index, paragraph, current)
        try:
            response, model = generate_content(payload, model=get_configured_model(self.model), timeout=self.timeout)
            self.diagnostics.model = model
            raw = extract_response_text(response).strip()
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("Qwen response is not a JSON object")
            return {
                "role": data.get("role"),
                "confidence": data.get("confidence"),
                "reason": data.get("reason"),
            }
        except json.JSONDecodeError as exc:
            self.diagnostics.qwen_bad_json += 1
            self._record_error("bad_json", str(exc), block_id=paragraph.id)
        except AIProviderError as exc:
            if exc.kind == "timeout":
                self.diagnostics.qwen_timeouts += 1
            self._record_error(exc.kind or "ai_provider_error", str(exc), block_id=paragraph.id, detail=exc.as_dict())
        except (OSError, TimeoutError, ValueError, TypeError) as exc:
            self._record_error(type(exc).__name__, str(exc), block_id=paragraph.id)
        return None

    def _payload(
        self,
        report: DocumentReport,
        paragraphs: list[ParagraphInfo],
        index: int,
        paragraph: ParagraphInfo,
        current: dict[str, Any],
    ) -> dict[str, Any]:
        before = paragraphs[max(0, index - self.context_radius) : index]
        after = paragraphs[index + 1 : index + 1 + self.context_radius]
        request = {
            "task": "classify_one_docx_block_role",
            "document_name": report.source_path,
            "allowed_roles": self.allowed_roles,
            "current": _paragraph_payload(paragraph, current),
            "context_before": [_paragraph_payload(p, {}) for p in before],
            "context_after": [_paragraph_payload(p, {}) for p in after],
            "response_schema": {"role": "heading_2", "confidence": 0.94, "reason": "short explanation"},
            "rules": [
                "Return only strict JSON object, no markdown.",
                "Choose exactly one role from allowed_roles.",
                "Do not rewrite, translate, shorten, or edit the text.",
                "Do not propose formatting or layout changes.",
            ],
        }
        return {
            "systemInstruction": {
                "parts": [
                    {
                        "text": (
                            "You classify semantic roles of scholarly DOCX blocks. "
                            "Return only a strict JSON object with keys role, confidence, reason. "
                            "Never edit text, formatting, DOCX, or layout."
                        )
                    }
                ]
            },
            "contents": [{"role": "user", "parts": [{"text": json.dumps(request, ensure_ascii=False)}]}],
            "generationConfig": {"maxOutputTokens": 256, "temperature": 0, "responseMimeType": "application/json"},
        }

    def _record_error(self, kind: str, message: str, *, block_id: str = "", detail: dict[str, Any] | None = None) -> None:
        self.diagnostics.qwen_errors += 1
        self.diagnostics.errors.append(
            {
                "kind": kind,
                "block_id": block_id,
                "message": message[:1000],
                "detail": detail or {},
            }
        )


def _paragraph_payload(paragraph: ParagraphInfo, current: dict[str, Any]) -> dict[str, Any]:
    return {
        "block_id": paragraph.id,
        "text": paragraph.normalized_text[:1200],
        "zone": current.get("zone"),
        "current_role": current.get("role_hint"),
        "current_confidence": current.get("confidence"),
        "needs_review": current.get("needs_review"),
        "style": paragraph.style_name or paragraph.style_id,
        "numbering": paragraph.numbering,
        "word_features": {
            "properties": paragraph.properties,
            "direct_formatting": paragraph.direct_formatting,
            "effective_formatting": paragraph.effective_formatting,
            "run_count": len(paragraph.runs),
            "has_drawings": bool(paragraph.drawings),
            "has_formulas": bool(paragraph.formulas),
            "has_hyperlinks": bool(paragraph.hyperlinks),
            "text_length": len(paragraph.normalized_text),
        },
    }


def _setting_or_env(name: str, default: str) -> str:
    return str(os.getenv(name) or default or "").strip()


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
