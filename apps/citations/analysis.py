import io
import json
import re
from pathlib import Path

from django.conf import settings
from pypdf import PdfReader

from apps.checks.gemini_client import (
    extract_response_text,
    generate_content,
    get_configured_model,
    is_ai_configured,
)
from apps.submissions.document_analysis import analyze_document_bytes


SPACE_RE = re.compile(r"\s+")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z0-9])")
EXISTING_CITATION_RE = re.compile(r"\[(?:\d+(?:\s*[-,;]\s*\d+)*)\]|\([A-ZА-ЯЁ][^)]*,\s*\d{4}\)")
SECTION_NAMES = {
    "введение": "Введение",
    "introduction": "Введение",
    "обзор литературы": "Обзор литературы",
    "литературный обзор": "Обзор литературы",
    "методы": "Методы",
    "материалы и методы": "Методы",
    "methods": "Методы",
    "результаты": "Результаты",
    "results": "Результаты",
    "обсуждение": "Обсуждение",
    "discussion": "Обсуждение",
    "заключение": "Заключение",
    "выводы": "Заключение",
    "conclusion": "Заключение",
    "список литературы": "Список литературы",
    "references": "Список литературы",
}
TYPE_PATTERNS = {
    "method": (
        "метод", "алгоритм", "модель", "подход", "методик", "архитектур",
        "регресси", "классификац", "оптимизац", "нейронн", "method", "algorithm",
    ),
    "data": (
        "данн", "выборк", "корпус", "датасет", "измерен", "наблюден", "опрос",
        "экспериментальн", "data", "dataset", "sample",
    ),
    "task": (
        "задач", "проблем", "необходим", "требует", "актуальн", "цель",
        "challenge", "problem", "task",
    ),
    "result": (
        "показал", "показано", "установлено", "выявлено", "доказано", "повышает",
        "снижает", "превышает", "эффектив", "результат", "demonstrat", "improv",
    ),
}
EVIDENCE_CUES = (
    "известно", "широко", "как правило", "позволяет", "применяется", "используется",
    "характеризуется", "влияет", "приводит", "является", "обеспечивает", "составляет",
    "according", "typically", "is used", "enables", "affects", "leads to",
)
ENGLISH_TERMS = {
    "метод": "method",
    "методы": "methods",
    "модель": "model",
    "алгоритм": "algorithm",
    "данные": "data",
    "выборка": "sample",
    "нейронная": "neural",
    "нейронные": "neural",
    "сеть": "network",
    "сети": "networks",
    "анализ": "analysis",
    "система": "system",
    "управление": "control",
    "обучение": "learning",
    "машинное": "machine",
    "изображений": "images",
    "изображения": "images",
    "эффективность": "efficiency",
    "результат": "result",
    "результаты": "results",
    "оптимизация": "optimization",
    "классификация": "classification",
    "прогнозирование": "forecasting",
    "исследование": "study",
}


def _normalize(value):
    return SPACE_RE.sub(" ", (value or "").replace("\x00", " ")).strip()


def document_snapshot(data, file_name):
    suffix = Path(file_name or "").suffix.casefold()
    if suffix == ".pdf":
        try:
            reader = PdfReader(io.BytesIO(data))
            paragraphs = []
            for page_number, page in enumerate(reader.pages, start=1):
                for value in re.split(r"[\r\n]+", page.extract_text() or ""):
                    text = _normalize(value)
                    if text:
                        paragraphs.append(
                            {
                                "index": len(paragraphs),
                                "text": text,
                                "style": "",
                                "page": page_number,
                            }
                        )
            return {
                "file_name": Path(file_name).name,
                "suffix": suffix,
                "paragraphs": paragraphs,
                "text": "\n".join(item["text"] for item in paragraphs),
                "parse_error": "" if paragraphs else "В PDF не найден текстовый слой.",
            }
        except Exception as exc:
            return {
                "file_name": Path(file_name).name,
                "suffix": suffix,
                "paragraphs": [],
                "text": "",
                "parse_error": f"Не удалось прочитать PDF: {exc}",
            }
    return analyze_document_bytes(data, file_name)


def text_snapshot(text):
    paragraphs = [
        {"index": index, "text": _normalize(value), "style": ""}
        for index, value in enumerate(re.split(r"[\r\n]+", text or ""))
        if _normalize(value)
    ]
    return {
        "file_name": "Вставленный текст",
        "suffix": ".txt",
        "paragraphs": paragraphs,
        "text": "\n".join(item["text"] for item in paragraphs),
        "parse_error": "",
    }


def _classify(sentence):
    lowered = sentence.casefold().replace("ё", "е")
    scored = {
        claim_type: sum(1 for pattern in patterns if pattern in lowered)
        for claim_type, patterns in TYPE_PATTERNS.items()
    }
    best = max(scored, key=scored.get)
    return best if scored[best] else "topic"


def _english_query(sentence, claim_type):
    translated = [
        ENGLISH_TERMS[token]
        for token in re.findall(r"[а-яёa-z]+", sentence.casefold())
        if token in ENGLISH_TERMS
    ]
    type_context = {
        "topic": "research topic evidence",
        "method": "scientific method application",
        "data": "dataset experimental data",
        "task": "research problem task",
        "result": "research result evidence",
    }.get(claim_type, "scientific evidence")
    unique = list(dict.fromkeys(translated))
    return " ".join([*unique[:12], type_context]).strip()


def _heuristic_claims(paragraphs, max_claims):
    claims = []
    current_section = ""
    bibliography_started = False
    candidates = []
    for paragraph in paragraphs:
        paragraph_text = _normalize(paragraph.get("text"))
        heading_key = paragraph_text.casefold().strip(" .:")
        if heading_key in SECTION_NAMES:
            current_section = SECTION_NAMES[heading_key]
            bibliography_started = current_section == "Список литературы"
            continue
        if bibliography_started:
            continue
        for sentence in SENTENCE_RE.split(paragraph_text):
            sentence = _normalize(sentence)
            if len(sentence) < 55 or len(sentence) > 650:
                continue
            if EXISTING_CITATION_RE.search(sentence):
                continue
            lowered = sentence.casefold()
            claim_type = _classify(sentence)
            priority = 0
            priority += 3 if any(cue in lowered for cue in EVIDENCE_CUES) else 0
            priority += 2 if claim_type in {"method", "data", "task"} else 1
            priority += 2 if current_section in {"Введение", "Обзор литературы", "Обсуждение"} else 0
            priority -= 2 if current_section in {"Результаты", "Заключение"} and claim_type == "result" else 0
            if re.search(r"\b\d+(?:[.,]\d+)?\s*%", sentence):
                priority += 2
            if priority < 2:
                continue
            candidates.append(
                (
                    priority,
                    {
                        "text": sentence,
                        "paragraph_index": paragraph.get("index", 0),
                        "section": current_section or "Текст статьи",
                        "type": claim_type,
                        "needs_citation": True,
                        "reason": {
                            "method": "Нужно подтвердить применимость или происхождение метода.",
                            "data": "Нужен источник о данных, выборке или измерениях.",
                            "task": "Нужно обосновать постановку и актуальность задачи.",
                            "result": "Нужно сопоставить результат с опубликованными данными.",
                            "topic": "Нужен источник для фонового или предметного утверждения.",
                        }[claim_type],
                        "query_ru": sentence,
                        "query_en": _english_query(sentence, claim_type),
                        "analysis_source": "local_rules",
                    },
                )
            )
    candidates.sort(key=lambda item: (-item[0], item[1]["paragraph_index"]))
    for _priority, claim in candidates[:max_claims]:
        claim["id"] = f"claim-{len(claims) + 1}"
        claims.append(claim)
    return claims


def _parse_json_response(raw):
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


def _llm_claims(paragraphs, max_claims):
    if not settings.CITATION_LLM_ANALYSIS_ENABLED or not is_ai_configured():
        return []
    compact = []
    total_length = 0
    for paragraph in paragraphs:
        text = _normalize(paragraph.get("text"))
        if not text:
            continue
        line = f"[P{paragraph.get('index', 0)}] {text}"
        if total_length + len(line) > 28000:
            break
        compact.append(line)
        total_length += len(line)
    prompt = f"""
Ты — научный редактор и специалист по evidence retrieval.
Найди не более {max_claims} самостоятельных утверждений, которым действительно нужна внешняя
научная ссылка. Не выбирай заголовки, библиографию, уже процитированные фразы и собственные
результаты автора, если они сформулированы только как результат текущего эксперимента.

Для каждого утверждения определи, что подтверждает источник:
topic — предмет/фон; method — метод или алгоритм; data — данные/выборка;
task — постановка/актуальность задачи; result — опубликованный эффект/результат.
Сделай точный поисковый запрос на русском и английском. Английский запрос должен быть
естественным переводом научных понятий, а не транслитерацией.

Верни только JSON:
{{"claims":[{{"text":"точная цитата из P","paragraph_index":0,"type":"topic|method|data|task|result",
"reason":"что требуется подтвердить","query_ru":"короткий запрос","query_en":"English query"}}]}}

ТЕКСТ:
{chr(10).join(compact)}
""".strip()
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 5000,
            "responseMimeType": "application/json",
        },
    }
    response, _model = generate_content(
        payload,
        model=get_configured_model(),
        timeout=settings.CITATION_LLM_TIMEOUT,
    )
    parsed = _parse_json_response(extract_response_text(response))
    allowed_types = {"topic", "method", "data", "task", "result"}
    claims = []
    paragraph_by_index = {
        int(item.get("index", 0)): _normalize(item.get("text"))
        for item in paragraphs
    }
    for raw in (parsed.get("claims") or [])[:max_claims]:
        if not isinstance(raw, dict):
            continue
        text = _normalize(raw.get("text"))
        try:
            paragraph_index = int(raw.get("paragraph_index", 0))
        except (TypeError, ValueError):
            paragraph_index = 0
        original = paragraph_by_index.get(paragraph_index, "")
        if not text or (original and text not in original):
            continue
        claim_type = raw.get("type") if raw.get("type") in allowed_types else _classify(text)
        claims.append(
            {
                "id": f"claim-{len(claims) + 1}",
                "text": text,
                "paragraph_index": paragraph_index,
                "section": "",
                "type": claim_type,
                "needs_citation": True,
                "reason": _normalize(raw.get("reason")) or "Требуется внешнее научное подтверждение.",
                "query_ru": _normalize(raw.get("query_ru")) or text,
                "query_en": _normalize(raw.get("query_en")) or _english_query(text, claim_type),
                "analysis_source": "local_llm",
            }
        )
    return claims


def _attach_locations(claims, paragraphs):
    paragraph_by_index = {
        int(item.get("index", 0)): _normalize(item.get("text"))
        for item in paragraphs
    }
    for claim in claims:
        paragraph = paragraph_by_index.get(int(claim.get("paragraph_index", 0)), "")
        claim_text = _normalize(claim.get("text"))
        start = paragraph.find(claim_text) if paragraph and claim_text else -1
        end = start + len(claim_text) if start >= 0 else -1
        claim["char_start"] = start
        claim["char_end"] = end
        claim["context_before"] = (
            ("…" if start > 150 else "") + paragraph[max(0, start - 150) : start]
            if start >= 0
            else ""
        )
        claim["context_after"] = (
            paragraph[end : end + 150] + ("…" if len(paragraph) > end + 150 else "")
            if end >= 0
            else ""
        )
        claim["placement_hint"] = (
            "Поставить ссылку сразу после этого утверждения."
            if start >= 0
            else "Поставить ссылку в конце указанного абзаца."
        )
    return claims


def analyze_claims(snapshot, *, max_claims=8):
    paragraphs = snapshot.get("paragraphs") or []
    if not paragraphs and snapshot.get("text"):
        paragraphs = text_snapshot(snapshot["text"])["paragraphs"]
    try:
        claims = _llm_claims(paragraphs, max_claims)
    except Exception:
        claims = []
    if not claims:
        claims = _heuristic_claims(paragraphs, max_claims)
    _attach_locations(claims, paragraphs)
    return {
        "claims": claims,
        "source": claims[0]["analysis_source"] if claims else "none",
        "paragraph_count": len(paragraphs),
        "text_length": len(snapshot.get("text") or ""),
    }
