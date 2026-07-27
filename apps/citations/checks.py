from django.conf import settings

from apps.checks.recommendations import recommend_articles
from apps.citations.analysis import analyze_claims, document_snapshot
from apps.citations.index import search_claim
from apps.citations.matching import (
    build_source_identity,
    claims_with_recommendations,
    remove_source_article,
)
from apps.citations.rerank import rerank_claims
from apps.submissions.document_analysis import read_file_bytes


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
    min_percent=None,
):
    min_percent = max(
        21,
        int(
            min_percent
            if min_percent is not None
            else getattr(settings, "CITATION_MIN_RECOMMENDATION_PERCENT", 21)
        ),
    )
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
    rerank_claims(claims, best_available_limit=8)
    for claim in claims:
        claim["recommendations"] = _filter_recommendations(
            claim.get("recommendations"),
            min_percent,
        )
    remove_source_article(
        claims,
        build_source_identity(
            citation_snapshot,
            source_title=getattr(submission, "title", ""),
            source_authors=getattr(submission, "document_authors", ""),
        ),
    )
    claims = claims_with_recommendations(claims)
    has_best_available = any(
        item.get("best_available")
        for claim in claims
        for item in (claim.get("recommendations") or [])
    )
    has_confirmed_sources = any(
        not item.get("best_available")
        for claim in claims
        for item in (claim.get("recommendations") or [])
    )

    issues = []
    unique_recommendations = {}
    for claim in claims:
        recommendations = claim.get("recommendations") or []
        best = recommendations[0] if recommendations else None
        suggestion = "Подберите подтверждающий источник вручную или уточните формулировку."
        if best:
            identity = str(best.get("article_id") or best.get("doi") or best.get("title"))
            unique_recommendations.setdefault(identity, best)
            doi_part = f", DOI {best['doi']}" if best.get("doi") else ""
            if best.get("best_available"):
                suggestion = (
                    f"Наиболее близкий источник — «{best['title']}» "
                    f"({best.get('year') or 'год не указан'}, {best['score_percent']}%{doi_part}). "
                    "Перед добавлением ссылки проверьте содержание публикации."
                )
            else:
                suggestion = (
                    f"Можно сослаться на «{best['title']}» "
                    f"({best.get('year') or 'год не указан'}, {best['score_percent']}%{doi_part})."
                )
        issues.append(
            {
                "code": f"citation_needed_{claim['id']}",
                "title": (
                    "Наиболее близкий научный источник"
                    if best and best.get("best_available")
                    else "Подходящий научный источник"
                ),
                "severity": "warning",
                "message": (
                    "Точного подтверждения не найдено; показана наиболее близкая публикация."
                    if best and best.get("best_available")
                    else "Для этого фрагмента найдена публикация, которую можно процитировать."
                ),
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
    if has_best_available and has_confirmed_sources:
        message = (
            "Найдены подтверждающие источники. Список дополнен наиболее близкими "
            f"публикациями; всего показано {len(recommendations)}."
        )
    elif has_best_available:
        message = (
            f"Точных подтверждений не найдено. Показано {len(recommendations)} "
            "наиболее близких публикаций из локальной базы."
        )
    elif claims_with_sources:
        message = (
            f"Найдено {claims_with_sources} фрагментов с подходящими научными источниками "
            f"и соответствием не ниже {min_percent}%."
        )
    else:
        message = "Подходящие новые источники не найдены."
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
            "best_available": has_best_available,
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
