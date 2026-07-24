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


def _remove_weak_results(claims):
    minimum = int(getattr(settings, "CITATION_MIN_RECOMMENDATION_PERCENT", 20))
    for claim in claims:
        claim["recommendations"] = [
            item
            for item in (claim.get("recommendations") or [])
            if int(item.get("score_percent") or 0) >= minimum
            and item.get("verdict") != "not_supports"
        ]
    return claims


def rerank_claims(claims):
    _fallback(claims)
    if not settings.CITATION_LLM_RERANK_ENABLED or not is_ai_configured():
        return _remove_weak_results(claims)

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
        return claims

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
    return _remove_weak_results(claims)
