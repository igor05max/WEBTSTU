import json
import re

from django.conf import settings

from apps.checks.gemini_client import (
    extract_response_text,
    generate_content,
    get_configured_model,
    is_ai_configured,
)


def _parse_json(raw):
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def _fallback(claims):
    for claim in claims:
        for result in claim.get("recommendations") or []:
            hybrid = float(result.get("hybrid_score") or 0.0)
            semantic = float(result.get("semantic_score") or 0.0)
            overlap_count = len(result.get("matched_terms") or [])
            calibrated = 35 + hybrid * 42 + semantic * 16 + min(overlap_count, 5) * 2
            result["score_percent"] = max(20, min(92, round(calibrated)))
            result["score"] = round(result["score_percent"] / 100, 4)
            result["verdict"] = "possible"
            result["rerank_source"] = "hybrid_local"
    return claims


def _minimum_score_percent():
    return max(
        21,
        int(getattr(settings, "CITATION_MIN_RECOMMENDATION_PERCENT", 21)),
    )


def _best_available_candidates(claims):
    return {
        str(claim.get("id") or ""): [
            dict(item) for item in (claim.get("recommendations") or [])
        ]
        for claim in claims
    }


def _restore_best_available(claims, candidates_by_claim, *, limit):
    if limit <= 0:
        return claims

    minimum = _minimum_score_percent()
    ranked = []
    for claim_index, claim in enumerate(claims):
        claim_id = str(claim.get("id") or "")
        for candidate_index, item in enumerate(candidates_by_claim.get(claim_id) or []):
            score = int(item.get("score_percent") or 0)
            if score < minimum:
                continue
            identity = str(
                item.get("article_id")
                or item.get("doi")
                or item.get("edn")
                or item.get("title")
                or ""
            ).casefold()
            if not identity:
                continue
            ranked.append(
                (
                    -score,
                    claim_index,
                    candidate_index,
                    identity,
                    claim,
                    item,
                )
            )

    ranked.sort(key=lambda row: row[:3])
    seen_articles = {
        str(
            item.get("article_id")
            or item.get("doi")
            or item.get("edn")
            or item.get("title")
            or ""
        ).casefold()
        for claim in claims
        for item in (claim.get("recommendations") or [])
    }
    restored_count = len(seen_articles)
    if restored_count >= limit:
        return claims
    for _, _, _, identity, claim, item in ranked:
        if identity in seen_articles:
            continue
        seen_articles.add(identity)
        restored = dict(item)
        restored["best_available"] = True
        restored["verdict"] = "possible"
        restored["rerank_source"] = "hybrid_local:best_available"
        claim.setdefault("recommendations", []).append(restored)
        restored_count += 1
        if restored_count >= limit:
            break
    return claims


def _limit_unique_results(claims, *, limit):
    if limit <= 0:
        return claims
    ranked = []
    for claim_index, claim in enumerate(claims):
        for candidate_index, item in enumerate(claim.get("recommendations") or []):
            identity = str(
                item.get("article_id")
                or item.get("doi")
                or item.get("edn")
                or item.get("title")
                or ""
            ).casefold()
            if not identity:
                continue
            ranked.append(
                (
                    -int(item.get("score_percent") or 0),
                    claim_index,
                    candidate_index,
                    identity,
                    claim,
                    item,
                )
            )
    ranked.sort(key=lambda row: row[:3])
    for claim in claims:
        claim["recommendations"] = []
    seen_articles = set()
    selected_count = 0
    for _, _, _, identity, claim, item in ranked:
        if identity in seen_articles:
            continue
        seen_articles.add(identity)
        claim["recommendations"].append(item)
        selected_count += 1
        if selected_count >= limit:
            break
    return claims


def _mark_best_available(claims):
    for claim in claims:
        for item in claim.get("recommendations") or []:
            item["best_available"] = True
            item["verdict"] = "possible"
            item["rerank_source"] = "hybrid_local:best_available"
    return claims


def _remove_weak_results(
    claims,
    *,
    fallback_candidates=None,
    best_available_limit=0,
):
    minimum = _minimum_score_percent()
    for claim in claims:
        claim["recommendations"] = [
            item
            for item in (claim.get("recommendations") or [])
            if int(item.get("score_percent") or 0) >= minimum
            and item.get("verdict") != "not_supports"
        ]
    _limit_unique_results(claims, limit=best_available_limit)
    if fallback_candidates:
        _restore_best_available(
            claims,
            fallback_candidates,
            limit=best_available_limit,
        )
    return claims


def rerank_claims(claims, *, best_available_limit=0):
    _fallback(claims)
    fallback_candidates = _best_available_candidates(claims)
    if not settings.CITATION_LLM_RERANK_ENABLED or not is_ai_configured():
        if best_available_limit:
            _mark_best_available(claims)
        return _remove_weak_results(
            claims,
            fallback_candidates=fallback_candidates,
            best_available_limit=best_available_limit,
        )

    items = []
    lookup = {}
    for claim in claims:
        for result in (claim.get("recommendations") or [])[:5]:
            item_id = f"{claim['id']}::{result['article_id']}"
            lookup[item_id] = result
            items.append(
                {
                    "id": item_id,
                    "claim": claim["text"],
                    "need": claim["type"],
                    "title": result["title"],
                    "year": result["year"],
                    "evidence": result["evidence"],
                }
            )
    if not items:
        return claims

    prompt = """
Ты проверяешь рекомендации научных источников. Оцени только приведённый фрагмент источника:
действительно ли он подтверждает утверждение, а не просто совпадает по теме.
Учитывай, что одинаковый метод может быть полезен в другой предметной области.
Независимо от языка утверждения и источника поле reason всегда пиши по-русски.
Поле evidence оставляй точной цитатой на языке источника.

Для каждого id верни:
- verdict: supports, partial или not_supports;
- score: целое 0..100 (насколько источник подходит именно для цитирования утверждения);
- reason: одно конкретное предложение о связи;
- evidence: самая доказательная короткая фраза только из переданного evidence, без выдумывания.

Верни только JSON:
{"items":[{"id":"...","verdict":"supports|partial|not_supports","score":0,
"reason":"...","evidence":"..."}]}

КАНДИДАТЫ:
""".strip() + "\n" + json.dumps(items, ensure_ascii=False)
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.05,
            "maxOutputTokens": 7000,
            "responseMimeType": "application/json",
        },
    }
    try:
        response, model = generate_content(
            payload,
            model=get_configured_model(),
            timeout=settings.CITATION_LLM_TIMEOUT,
        )
        parsed = _parse_json(extract_response_text(response))
    except Exception:
        if best_available_limit:
            _mark_best_available(claims)
        return _remove_weak_results(
            claims,
            fallback_candidates=fallback_candidates,
            best_available_limit=best_available_limit,
        )

    for raw in parsed.get("items") or []:
        if not isinstance(raw, dict):
            continue
        result = lookup.get(str(raw.get("id") or ""))
        if result is None:
            continue
        verdict = raw.get("verdict")
        if verdict not in {"supports", "partial", "not_supports"}:
            continue
        try:
            score = int(float(raw.get("score", 0)))
        except (TypeError, ValueError):
            continue
        result["verdict"] = verdict
        result["score_percent"] = max(0, min(100, score))
        result["score"] = round(result["score_percent"] / 100, 4)
        result["reason"] = str(raw.get("reason") or result["reason"]).strip()[:600]
        quoted_evidence = str(raw.get("evidence") or "").strip()
        if quoted_evidence and quoted_evidence.casefold() in result["evidence"].casefold():
            result["evidence"] = quoted_evidence
        result["rerank_source"] = f"local_llm:{model}"

    verdict_order = {"supports": 0, "partial": 1, "possible": 2, "not_supports": 3}
    for claim in claims:
        claim["recommendations"].sort(
            key=lambda item: (
                verdict_order.get(item.get("verdict"), 2),
                -item.get("score_percent", 0),
                item["title"],
            )
        )
    return _remove_weak_results(
        claims,
        fallback_candidates=fallback_candidates,
        best_available_limit=best_available_limit,
    )
