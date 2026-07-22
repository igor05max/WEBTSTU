import base64
import io
import json
import mimetypes
import re
import urllib.error
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from django.conf import settings

from apps.checks.gemini_client import (
    GeminiAPIError,
    generate_content,
    get_ai_source,
    get_configured_model,
    get_provider,
    get_provider_label,
    is_ai_configured,
)


_TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
    ".tsv",
    ".json",
    ".xml",
    ".html",
    ".htm",
    ".tex",
    ".rtf",
}
_INLINE_FILE_EXTENSIONS = {
    ".doc",
    ".docx",
    ".htm",
    ".html",
    ".md",
    ".pdf",
    ".rtf",
    ".tex",
    ".txt",
}
_INLINE_FILE_SIZE_LIMIT = 4 * 1024 * 1024
_TEXT_EXCERPT_LIMIT = 8000
_JSON_PREFIX_RE = re.compile(r"^[^{]*")
_JSON_SUFFIX_RE = re.compile(r"[^}]*$")
_DIRECTION_CODE_RE = re.compile(r'"direction_code"\s*:\s*"([^"]+)"')
_CONFIDENCE_RE = re.compile(r'"confidence"\s*:\s*([0-9]+(?:\.[0-9]+)?)')
_REASONING_RE = re.compile(r'"reasoning"\s*:\s*"([^"]*)"')
_DIRECTION_KEYWORDS = {
    "mechanical-engineering": (
        "машиностро",
        "станок",
        "механизм",
        "детал",
        "обработк метал",
    ),
    "biotech-food": (
        "биотехнолог",
        "пищев",
        "растительн сыр",
        "сырья",
        "зерн",
    ),
    "transport-service": (
        "автомобил",
        "автосервис",
        "транспорт",
        "двигател",
        "перевоз",
    ),
    "informatics-security": (
        "информатик",
        "вычислител",
        "программ",
        "данн",
        "кибер",
        "безопасност",
    ),
    "mechatronics-quality": (
        "мехатрон",
        "робот",
        "управлен качеств",
        "автоматизац",
    ),
    "systems-control": (
        "системн анализ",
        "управлен",
        "автоматик",
        "моделирован",
        "регулятор",
        "идентификац",
    ),
    "agriculture": (
        "сельск",
        "аграр",
        "почв",
        "урож",
        "животновод",
    ),
    "architecture": (
        "архитектур",
        "здани",
        "градостро",
        "планировк",
        "дизайн сред",
    ),
    "construction": (
        "строительств",
        "строительн",
        "бетон",
        "фундамент",
        "сооружен",
    ),
    "industrial-machines": (
        "технологическ машин",
        "оборудован",
        "агрегат",
        "конвейер",
        "аппарат",
    ),
    "nanoengineering": (
        "нано",
        "наночаст",
        "нанострукт",
        "тонк пленк",
    ),
    "technosphere-safety": (
        "техносфер",
        "охрана труда",
        "пожарн",
        "авар",
        "производственн безопасност",
    ),
    "ecology": (
        "эколог",
        "природопольз",
        "окружающ сред",
        "загрязнен",
        "экосистем",
    ),
    "chemical-technology": (
        "химическ",
        "катализ",
        "реактор",
        "синтез",
        "полимер",
    ),
    "materials-science": (
        "материал",
        "сплав",
        "композит",
        "прочност",
        "структур материал",
    ),
    "packaging": (
        "упаков",
        "тара",
        "маркировк",
    ),
    "finance": (
        "финанс",
        "кредит",
        "банк",
        "денежн",
        "инвестици",
    ),
    "economics": (
        "экономик",
        "рынок",
        "предприяти",
        "менеджмент",
        "логистик",
    ),
    "electrical-engineering": (
        "электротех",
        "электроснабж",
        "электрическ",
        "подстанц",
        "энергосистем",
    ),
    "radio-engineering": (
        "радиотех",
        "антенн",
        "сигнал",
        "свч",
        "приемопередат",
    ),
    "biotechnical-systems": (
        "биотехнич",
        "биомедицин",
        "медицин",
        "томограф",
        "окт",
        "офтальм",
        "биологическ жидк",
        "доплер",
        "изображени",
    ),
    "heat-engineering": (
        "теплотех",
        "теплообмен",
        "котел",
        "энергообесп",
        "теплоснабж",
    ),
    "electronics-comms": (
        "электронн средств",
        "связи",
        "телеком",
        "коммуникац",
        "мобильн",
    ),
    "law": (
        "юрид",
        "право",
        "закон",
        "суд",
        "уголов",
        "гражданск",
    ),
    "advertising-pr": (
        "реклам",
        "связи с общественност",
        "бренд",
        "медиа",
        "маркетингов",
    ),
    "legal-informatics": (
        "юридическ",
        "правов",
        "legaltech",
        "информационн систем",
        "цифров",
    ),
    "law-history": (
        "юридическ",
        "право",
        "истор",
        "государств",
    ),
    "higher-math": (
        "математ",
        "алгебр",
        "геометр",
        "уравнен",
        "интеграл",
    ),
    "physics": (
        "физик",
        "квант",
        "оптик",
        "лазер",
        "электромагнит",
    ),
    "languages": (
        "лингвист",
        "перевод",
        "иностранн язык",
        "английск",
        "немецк",
        "француз",
    ),
    "history-society": (
        "истор",
        "социальн",
        "общество",
        "политик",
        "социолог",
    ),
    "russian-language": (
        "русск",
        "язык",
        "филолог",
        "литератур",
        "реч",
    ),
}


def _normalize_space(value):
    value = value.replace("\x00", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _normalize_match_text(value):
    return _normalize_space(value).lower().replace("ё", "е")


def _decode_text_bytes(file_bytes):
    variants = []
    for encoding in ("utf-8", "cp1251", "utf-16le"):
        try:
            decoded = file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
        cleaned = _normalize_space(decoded)
        if cleaned:
            variants.append(cleaned)

    if not variants:
        cleaned = _normalize_space(file_bytes.decode("utf-8", errors="ignore"))
        return cleaned

    def score(text):
        letters = sum(1 for char in text if char.isalpha())
        cyrillic = sum(1 for char in text if "А" <= char <= "я" or char in "Ёё")
        return (cyrillic, letters, len(text))

    return max(variants, key=score)


def _extract_docx_text(file_bytes):
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as archive:
            chunks = []
            for member_name in ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml"):
                if member_name not in archive.namelist():
                    continue
                xml_bytes = archive.read(member_name)
                root = ElementTree.fromstring(xml_bytes)
                text_nodes = []
                for element in root.iter():
                    if element.tag.endswith("}t") and element.text:
                        text_nodes.append(element.text)
                    elif element.tag.endswith("}tab"):
                        text_nodes.append(" ")
                    elif element.tag.endswith("}br") or element.tag.endswith("}p"):
                        text_nodes.append("\n")
                chunks.append("".join(text_nodes))
    except (OSError, ValueError, zipfile.BadZipFile, ElementTree.ParseError):
        return ""

    return _normalize_space("\n".join(chunks))


def _extract_submission_file_text(submission):
    version = submission.current_version
    if version is None or not version.file:
        return ""

    suffix = Path(version.file.name).suffix.lower()
    try:
        with version.file.open("rb") as source:
            file_bytes = source.read()
    except OSError:
        return ""

    if suffix == ".docx":
        return _extract_docx_text(file_bytes)
    if suffix in _TEXT_EXTENSIONS or suffix == ".doc":
        return _decode_text_bytes(file_bytes)
    return ""


def _build_document_excerpt(submission):
    parts = []
    if submission.title:
        parts.append(f"Название: {submission.title}")
    if submission.abstract:
        parts.append(f"Аннотация: {submission.abstract}")

    file_text = _extract_submission_file_text(submission)
    if file_text:
        parts.append(f"Фрагмент текста материала: {file_text[:_TEXT_EXCERPT_LIMIT]}")

    excerpt = "\n\n".join(part.strip() for part in parts if part.strip())
    return excerpt[:_TEXT_EXCERPT_LIMIT]


def _detect_direction_locally(submission, directions, *, details=""):
    excerpt = _normalize_match_text(_build_document_excerpt(submission))
    if not excerpt:
        return None

    best_direction = None
    best_score = 0
    best_matches = []
    for direction in directions:
        keywords = _DIRECTION_KEYWORDS.get(direction.code, ())
        matches = []
        score = 0
        for keyword in keywords:
            normalized_keyword = _normalize_match_text(keyword)
            if not normalized_keyword or normalized_keyword not in excerpt:
                continue
            matches.append(keyword)
            score += 3 if " " in normalized_keyword else 1

        if score > best_score:
            best_direction = direction
            best_score = score
            best_matches = matches

    if best_direction is None or best_score <= 0:
        return None

    confidence = min(85, 35 + best_score * 8)
    reasoning = f"Локальный фолбэк по ключевым словам: {', '.join(best_matches[:6])}."
    provider_unavailable = (
        "Gemini недоступен" if get_provider() == "gemini" else "Локальная AI-модель недоступна"
    )
    fallback_message = f"{provider_unavailable}, поэтому область экспертизы определена локально по ключевым словам."
    try:
        diagnostic = json.loads(details or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        diagnostic = {}
    http_status = diagnostic.get("http_status") if isinstance(diagnostic, dict) else None
    if http_status:
        fallback_message = (
            f"{provider_unavailable} (HTTP {http_status}), поэтому область экспертизы "
            "определена локально по ключевым словам."
        )
    return {
        "matched": True,
        "source": "local_keywords",
        "execution_status": "partial",
        "ai_check_performed": False,
        "message": fallback_message,
        "direction_code": best_direction.code,
        "direction_name": best_direction.name,
        "confidence": confidence,
        "reasoning": reasoning,
        "details": details,
    }


def _guess_inline_mime_type(file_name):
    suffix = Path(file_name).suffix.lower()
    if suffix == ".docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if suffix == ".doc":
        return "application/msword"
    if suffix == ".rtf":
        return "application/rtf"

    guessed, _ = mimetypes.guess_type(file_name)
    return guessed or "application/octet-stream"


def _build_inline_file_part(submission):
    version = submission.current_version
    if version is None or not version.file:
        return None

    suffix = Path(version.file.name).suffix.lower()
    if suffix not in _INLINE_FILE_EXTENSIONS:
        return None

    try:
        with version.file.open("rb") as source:
            file_bytes = source.read()
    except OSError:
        return None

    if not file_bytes or len(file_bytes) > _INLINE_FILE_SIZE_LIMIT:
        return None

    return {
        "inline_data": {
            "mime_type": _guess_inline_mime_type(version.file.name),
            "data": base64.b64encode(file_bytes).decode("ascii"),
        }
    }


def _build_prompt(submission, directions, *, excerpt):
    direction_lines = []
    for direction in directions:
        direction_lines.append(
            f"- code: {direction.code}; name: {direction.name}; description: {direction.description or '-'}"
        )

    return "\n".join(
        [
            "Определи одну предметную область для научного материала.",
            "Нужно выбрать ровно один code из списка доступных областей.",
            "Если текст краткий, опирайся на основную тему из названия и аннотации.",
            "Ответ верни строго в JSON без пояснений вне JSON.",
            "Формат ответа:",
            '{"direction_code": "...", "confidence": 0, "reasoning": "..."}',
            "",
            "Доступные области:",
            *direction_lines,
            "",
            "Материал:",
            excerpt or f"Название: {submission.title}",
        ]
    )


def _extract_response_text(response_payload):
    candidates = response_payload.get("candidates") or []
    for candidate in candidates:
        content = candidate.get("content") or {}
        for part in content.get("parts") or []:
            text = part.get("text")
            if text:
                return text
    return ""


def _parse_direction_response(response_text, directions_by_code):
    if not response_text:
        return None

    normalized = response_text.strip()
    if "{" in normalized:
        normalized = normalized[normalized.find("{") :]
    if "}" in normalized:
        normalized = normalized[: normalized.rfind("}") + 1]

    try:
        payload = json.loads(normalized)
        direction_code = str(payload.get("direction_code") or "").strip()
        confidence = payload.get("confidence")
        reasoning = str(payload.get("reasoning") or "").strip()
    except json.JSONDecodeError:
        match = _DIRECTION_CODE_RE.search(normalized)
        if match is None:
            return None
        direction_code = match.group(1).strip()
        confidence_match = _CONFIDENCE_RE.search(normalized)
        confidence = confidence_match.group(1) if confidence_match else 0
        reasoning_match = _REASONING_RE.search(normalized)
        reasoning = reasoning_match.group(1).strip() if reasoning_match else ""

    if direction_code not in directions_by_code:
        return None

    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0
    else:
        if 0 <= confidence <= 1:
            confidence *= 100

    return {
        "direction_code": direction_code,
        "confidence": int(max(0, min(confidence, 100))),
        "reasoning": reasoning,
    }


def _call_gemini(prompt, *, inline_file_part=None, timeout=45):
    model_name = get_configured_model(settings.SUBMISSION_ROUTE_SUGGESTION_MODEL)
    payload = {
        "systemInstruction": {
            "parts": [
                {
                    "text": (
                        "Ты классификатор научных материалов. "
                        "Выбирай только один direction_code из предложенного списка и отвечай строго JSON."
                    )
                }
            ]
        },
        "generationConfig": {
            "maxOutputTokens": 512,
            "responseMimeType": "application/json",
        },
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": prompt},
                ],
            }
        ],
    }
    if inline_file_part is not None:
        payload["contents"][0]["parts"].append(inline_file_part)

    response, used_model = generate_content(
        payload,
        model=model_name,
        timeout=max(timeout, int(getattr(settings, "SUBMISSION_ROUTE_SUGGESTION_TIMEOUT", 45))),
    )
    response["_selected_model"] = used_model
    return response


def _build_unmatched_payload(
    message,
    *,
    source="unavailable",
    reasoning="",
    details="",
    execution_status="not_performed",
):
    return {
        "matched": False,
        "source": source,
        "execution_status": execution_status,
        "ai_check_performed": False,
        "message": message,
        "direction_code": "",
        "direction_name": "",
        "confidence": 0,
        "reasoning": reasoning,
        "details": details,
    }


def detect_direction_for_submission(submission, *, directions):
    directions = list(directions)
    if not directions:
        return _build_unmatched_payload("Для выбранного типа материала не настроены активные области экспертизы.")

    if len(directions) == 1:
        direction = directions[0]
        return {
            "matched": True,
            "source": "single_direction",
            "execution_status": "completed",
            "ai_check_performed": False,
            "message": "Для выбранного типа материала доступна только одна область экспертизы.",
            "direction_code": direction.code,
            "direction_name": direction.name,
            "confidence": 100,
            "reasoning": "",
            "details": "",
        }

    if not settings.SUBMISSION_ROUTE_SUGGESTION_ENABLED:
        return _build_unmatched_payload(
            "Автоматическое определение области экспертизы отключено.",
            source="disabled",
        )

    if not is_ai_configured():
        return _build_unmatched_payload(
            "Подключение к AI-модели не настроено, поэтому область экспертизы не определена автоматически.",
            source="missing_ai_configuration",
        )

    excerpt = _build_document_excerpt(submission)
    prompt = _build_prompt(submission, directions, excerpt=excerpt)
    directions_by_code = {direction.code: direction for direction in directions}
    # Gemini умеет принимать файлы inline. OpenAI-совместимому локальному API
    # передаём уже извлечённый текст, чтобы не отправлять ему неподдерживаемый base64.
    inline_file_part = _build_inline_file_part(submission) if get_provider() == "gemini" else None

    response_text = ""
    response_details = ""
    response_model = ""
    request_variants = [inline_file_part]
    if inline_file_part is not None:
        request_variants.append(None)

    for request_inline_file_part in request_variants:
        try:
            response_payload = _call_gemini(
                prompt,
                inline_file_part=request_inline_file_part,
            )
            response_text = _extract_response_text(response_payload)
            response_model = str(response_payload.get("_selected_model") or "")
            response_details = ""
            break
        except GeminiAPIError as exc:
            response_details = json.dumps(exc.as_dict(), ensure_ascii=False)
        except urllib.error.HTTPError as exc:
            response_details = exc.read().decode("utf-8", errors="ignore")
        except (OSError, TimeoutError, ValueError) as exc:
            response_details = str(exc)

    parsed = _parse_direction_response(response_text, directions_by_code)
    if parsed is None:
        local_payload = _detect_direction_locally(
            submission,
            directions,
            details=response_details or response_text,
        )
        if local_payload is not None:
            return local_payload

        return _build_unmatched_payload(
            f"{get_provider_label()} не смогла уверенно определить область экспертизы по материалу.",
            source=f"{get_ai_source()}_error" if response_details else f"{get_ai_source()}_parse_error",
            details=response_details or response_text,
        )

    direction = directions_by_code[parsed["direction_code"]]
    confidence = parsed["confidence"]
    message = (
        "Gemini определил предметную область по материалу."
        if get_provider() == "gemini"
        else "Локальная AI-модель определила предметную область по материалу."
    )
    if confidence:
        message = f"{message} Уверенность модели: {confidence}%."

    return {
        "matched": True,
        "source": get_ai_source(),
        "execution_status": "completed",
        "ai_check_performed": True,
        "message": message,
        "direction_code": direction.code,
        "direction_name": direction.name,
        "confidence": confidence,
        "reasoning": parsed["reasoning"],
        "details": response_text,
        "model": response_model,
    }
