from django.conf import settings

from apps.checks.recommendations import recommend_articles
from apps.citations.analysis import analyze_claims, document_snapshot
from apps.citations.index import search_claim
from apps.citations.rerank import rerank_claims
from apps.submissions.document_analysis import read_file_bytes


TYPE_LABELS = {
    "topic": "тема и научный контекст",
    "method": "метод",
    "data": "данные",
    "task": "постановка задачи",
    "result": "результат",
}


def _summary(issues):
    counts = {"info": 0, "warning": 0, "error": 0, "critical": 0}
    for issue in issues:
        severity = issue.get("severity", "info")
        if severity in counts:
            counts[severity] += 1
    counts["total"] = sum(counts.values())
    return counts


def _load_citation_snapshot(version, snapshot):
    if snapshot and (snapshot.get("text") or "").strip():
        return snapshot
    if version is None or not version.file:
        return snapshot or {}
    with version.file.open("rb") as source:
        data = read_file_bytes(source)
    return document_snapshot(data, version.file.name)


def _filter_recommendations(recommendations, min_percent):
    return [
        item
        for item in (recommendations or [])
        if int(item.get("score_percent") or 0) >= min_percent
        and item.get("verdict") != "not_supports"
    ]


def _legacy_fallback(submission, *, min_percent):
    payload = recommend_articles(
        title=submission.title,
        abstract=submission.abstract or "",
    )
    payload["recommendations"] = _filter_recommendations(
        payload.get("recommendations"),
        min_percent,
    )
    payload.update(
        {
            "schema_version": "2.0",
            "check_code": "article_recommendations",
            "summary": {"info": 1, "warning": 0, "error": 0, "critical": 0, "total": 1},
            "issues": [
                {
                    "code": "citation_analysis_fallback",
                    "title": "Доступен тематический подбор",
                    "severity": "info",
                    "message": (
                        "В файле недостаточно связного текста для поиска точных мест цитирования. "
                        "Выполнен подбор по названию и аннотации."
                    ),
                    "location": "Метаданные материала",
                    "context": "",
                    "context_before": "",
                    "context_highlight": "",
                    "context_after": "",
                    "suggestion": "Для точного анализа загрузите DOCX, PDF или полный текст статьи.",
                }
            ],
            "metrics": {
                "claims_needing_citation": 0,
                "recommended_sources": len(payload["recommendations"]),
            },
            "details": {"analysis_source": "title_abstract_fallback", "citation_claims": []},
            "citation_claims": [],
        }
    )
    return payload


def build_citation_coverage_report(
    submission,
    version,
    *,
    snapshot=None,
    max_claims=8,
    results_per_claim=4,
    min_percent=20,
):
    citation_snapshot = _load_citation_snapshot(version, snapshot)
    if len((citation_snapshot.get("text") or "").strip()) < settings.CITATION_CHECK_MIN_TEXT_LENGTH:
        return True, _legacy_fallback(submission, min_percent=min_percent)
    analysis = analyze_claims(citation_snapshot, max_claims=max_claims)
    claims = analysis.get("claims") or []
    if not claims:
        return True, _legacy_fallback(submission, min_percent=min_percent)

    for claim in claims:
        claim["recommendations"] = search_claim(
            claim,
            limit=results_per_claim,
        )
    rerank_claims(claims)
    for claim in claims:
        claim["recommendations"] = _filter_recommendations(
            claim.get("recommendations"),
            min_percent,
        )

    issues = []
    unique_recommendations = {}
    for claim in claims:
        recommendations = claim.get("recommendations") or []
        best = recommendations[0] if recommendations else None
        type_label = TYPE_LABELS.get(claim.get("type"), "научное утверждение")
        suggestion = "Подберите подтверждающий источник вручную или уточните формулировку."
        if best:
            identity = str(best.get("article_id") or best.get("doi") or best.get("title"))
            unique_recommendations.setdefault(identity, best)
            doi_part = f", DOI {best['doi']}" if best.get("doi") else ""
            suggestion = (
                f"Можно сослаться на «{best['title']}» "
                f"({best.get('year') or 'год не указан'}, {best['score_percent']}%{doi_part})."
            )
        issues.append(
            {
                "code": f"citation_needed_{claim['id']}",
                "title": f"Нужна ссылка: {type_label}",
                "severity": "warning",
                "message": claim.get("reason") or "Утверждение требует внешнего подтверждения.",
                "location": (
                    f"{claim.get('section') or 'Текст статьи'}, "
                    f"абзац {int(claim.get('paragraph_index', 0)) + 1}"
                ),
                "context": claim.get("text", ""),
                "context_before": claim.get("context_before", ""),
                "context_highlight": claim.get("text", ""),
                "context_after": claim.get("context_after", ""),
                "suggestion": suggestion,
            }
        )

    for claim in claims:
        for item in claim.get("recommendations") or []:
            identity = str(item.get("article_id") or item.get("doi") or item.get("title"))
            unique_recommendations.setdefault(identity, item)
    recommendations = sorted(
        unique_recommendations.values(),
        key=lambda item: (-int(item.get("score_percent") or 0), item.get("title", "")),
    )
    claims_with_sources = sum(bool(claim.get("recommendations")) for claim in claims)
    message = (
        f"Найдено {len(claims)} мест, где нужна научная ссылка. "
        f"Для {claims_with_sources} из них найдены источники с соответствием не ниже {min_percent}%."
    )
    payload = {
        "schema_version": "2.0",
        "check_code": "article_recommendations",
        "message": message,
        "summary": _summary(issues),
        "issues": issues,
        "metrics": {
            "claims_needing_citation": len(claims),
            "claims_with_sources": claims_with_sources,
            "recommended_sources": len(recommendations),
            "minimum_score_percent": min_percent,
        },
        "details": {
            "analysis_source": analysis.get("source", "none"),
            "citation_claims": claims,
        },
        "citation_claims": claims,
        "recommendations": recommendations,
        "source": "citation_rag_v2",
    }
    return True, payload
