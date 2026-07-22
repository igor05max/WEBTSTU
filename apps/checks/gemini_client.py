import json
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass

from django.conf import settings
from django.db import OperationalError, ProgrammingError


GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_MODELS_ENDPOINT = f"{GEMINI_API_BASE_URL}/models"
PREFERRED_MODELS = (
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-flash-lite-latest",
)
_KEY_QUERY_RE = re.compile(r"([?&]key=)[^&\s]+", re.I)
_OPENAI_COMPATIBLE_PROVIDERS = {"openai", "openai_compatible", "llama_cpp", "local"}


def get_provider():
    provider = str(getattr(settings, "AI_PROVIDER", "gemini") or "gemini").strip().lower()
    return "openai_compatible" if provider in _OPENAI_COMPATIBLE_PROVIDERS else "gemini"


def get_provider_label():
    return "Локальная AI-модель" if get_provider() == "openai_compatible" else "Gemini"


def get_ai_source():
    return "ai" if get_provider() == "openai_compatible" else "gemini"


def get_api_base_url():
    if get_provider() == "openai_compatible":
        return str(getattr(settings, "AI_BASE_URL", "") or "").strip().rstrip("/")
    return GEMINI_API_BASE_URL


def get_models_endpoint():
    base_url = get_api_base_url()
    return f"{base_url}/models" if base_url else ""


def get_api_key(api_key=None):
    if api_key is not None:
        return str(api_key or "").strip()
    if get_provider() == "openai_compatible":
        return str(getattr(settings, "AI_API_KEY", "") or "").strip()
    return str(getattr(settings, "GEMINI_API_KEY", "") or "").strip()


def is_ai_configured():
    if get_provider() == "openai_compatible":
        return bool(get_api_base_url())
    return bool(get_api_key())


def validate_api_key(api_key):
    """Validate only properties required by Gemini, not a historical key prefix."""
    normalized = (api_key or "").strip()
    if not normalized:
        raise ValueError("Ключ Gemini API не задан.")
    if any(character.isspace() for character in normalized):
        raise ValueError("Ключ Gemini API содержит пробельные символы.")
    return normalized


def _validate_configuration(api_key=None):
    if get_provider() == "openai_compatible":
        if not get_api_base_url():
            raise ValueError("Адрес локального AI API не задан в AI_BASE_URL.")
        return get_api_key(api_key)
    return validate_api_key(get_api_key(api_key))


def normalize_model_id(model_name):
    normalized = (model_name or "").strip()
    if normalized.startswith("models/"):
        normalized = normalized[len("models/") :]
    return normalized.strip("/")


def model_resource_name(model_name):
    model_id = normalize_model_id(model_name)
    return f"models/{model_id}" if model_id else ""


def redact_sensitive(value, *, api_key=""):
    safe_value = str(value or "")
    if api_key:
        safe_value = safe_value.replace(api_key, "[API_KEY_REDACTED]")
    return _KEY_QUERY_RE.sub(r"\1[API_KEY_REDACTED]", safe_value)


def _http_error_hint(status):
    service_name = get_provider_label()
    return {
        400: "Сервис отклонил параметры запроса.",
        401: "Сервис не принял учётные данные API.",
        403: "Нет доступа к операции.",
        404: "Модель или endpoint не найдены.",
        429: "Превышена квота или частота запросов.",
    }.get(
        status,
        f"{service_name} временно недоступна." if status and status >= 500 else "Запрос AI завершился ошибкой.",
    )


@dataclass
class GeminiAPIError(Exception):
    """Compatibility name for errors from either configured AI provider."""

    stage: str
    kind: str
    message: str
    endpoint: str
    status: int | None = None
    error_code: str = ""
    model: str = ""
    provider: str = ""

    def __str__(self):
        status = f" HTTP {self.status}." if self.status else ""
        return f"{_http_error_hint(self.status)}{status} {self.message}".strip()

    def as_dict(self):
        return {
            "stage": self.stage,
            "kind": self.kind,
            "http_status": self.status,
            "provider_message": self.message,
            "google_message": self.message,
            "error_code": self.error_code,
            "endpoint": self.endpoint,
            "model": self.model,
            "provider": self.provider or get_provider(),
        }


AIProviderError = GeminiAPIError


def _parse_api_error(raw_body, *, api_key, fallback_message):
    message = fallback_message
    error_code = ""
    try:
        payload = json.loads(raw_body or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        message = str(error.get("message") or message)
        error_code = str(error.get("status") or error.get("code") or error.get("type") or "")
    elif error:
        message = str(error)
    return redact_sensitive(message, api_key=api_key)[:1500], redact_sensitive(error_code, api_key=api_key)[:120]


def _request_json(*, method, endpoint, api_key, timeout, stage, model="", payload=None, opener=None):
    safe_endpoint = redact_sensitive(endpoint, api_key=api_key).split("?", 1)[0]
    headers = {"Accept": "application/json"}
    if get_provider() == "gemini":
        headers["x-goog-api-key"] = validate_api_key(api_key)
    elif api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(endpoint, data=data, headers=headers, method=method)
    open_request = opener or urllib.request.urlopen
    try:
        with open_request(request, timeout=timeout) as response:
            raw_body = response.read().decode("utf-8")
            return json.loads(raw_body)
    except urllib.error.HTTPError as exc:
        raw_body = exc.read().decode("utf-8", errors="ignore")
        message, error_code = _parse_api_error(
            raw_body,
            api_key=api_key,
            fallback_message=getattr(exc, "reason", "HTTP error"),
        )
        raise GeminiAPIError(
            stage=stage,
            kind="http_error",
            status=exc.code,
            message=message,
            error_code=error_code,
            endpoint=safe_endpoint,
            model=normalize_model_id(model),
            provider=get_provider(),
        ) from None
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (socket.timeout, TimeoutError)):
            kind = "timeout"
            message = f"Превышено время ожидания ({timeout} с)."
        elif isinstance(reason, socket.gaierror):
            kind = "dns_error"
            message = "Не удалось определить адрес AI-сервера."
        else:
            kind = "network_error"
            message = "Нет соединения с AI API."
        raise GeminiAPIError(
            stage=stage,
            kind=kind,
            message=message,
            endpoint=safe_endpoint,
            model=normalize_model_id(model),
            provider=get_provider(),
        ) from None
    except (socket.timeout, TimeoutError):
        raise GeminiAPIError(
            stage=stage,
            kind="timeout",
            message=f"Превышено время ожидания ({timeout} с).",
            endpoint=safe_endpoint,
            model=normalize_model_id(model),
            provider=get_provider(),
        ) from None
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise GeminiAPIError(
            stage=stage,
            kind="invalid_response" if isinstance(exc, (ValueError, json.JSONDecodeError)) else "network_error",
            message=redact_sensitive(str(exc), api_key=api_key)[:1500],
            endpoint=safe_endpoint,
            model=normalize_model_id(model),
            provider=get_provider(),
        ) from None


def parse_generation_models(payload):
    result = []
    seen = set()
    provider = get_provider()
    if provider == "openai_compatible":
        raw_models = (payload or {}).get("data") or (payload or {}).get("models") or []
        for item in raw_models:
            if not isinstance(item, dict):
                continue
            model_id = normalize_model_id(item.get("id") or item.get("name") or item.get("model"))
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            result.append(
                {
                    "id": model_id,
                    "name": model_id,
                    "display_name": str(item.get("display_name") or item.get("name") or model_id),
                    "supported_generation_methods": ["chat/completions"],
                }
            )
        return result

    for item in (payload or {}).get("models") or []:
        if not isinstance(item, dict):
            continue
        methods = item.get("supportedGenerationMethods") or []
        if "generateContent" not in methods:
            continue
        model_id = normalize_model_id(item.get("name"))
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        result.append(
            {
                "id": model_id,
                "name": model_resource_name(model_id),
                "display_name": str(item.get("displayName") or model_id),
                "supported_generation_methods": list(methods),
            }
        )
    return result


def fetch_generation_models(*, api_key=None, timeout=None, opener=None):
    api_key = _validate_configuration(api_key)
    default_timeout = getattr(
        settings,
        "AI_MODELS_TIMEOUT",
        getattr(settings, "GEMINI_MODELS_TIMEOUT", 30),
    )
    timeout = max(30, int(timeout if timeout is not None else default_timeout))
    endpoint = get_models_endpoint()
    payload = _request_json(
        method="GET",
        endpoint=endpoint,
        api_key=api_key,
        timeout=timeout,
        stage="list_models",
        opener=opener,
    )
    models = parse_generation_models(payload)
    if not models:
        raise GeminiAPIError(
            stage="list_models",
            kind="no_compatible_models",
            message="AI-сервис не вернул моделей для генерации текста.",
            endpoint=endpoint,
            provider=get_provider(),
        )
    return models


def choose_generation_model(models, saved_model=""):
    available_ids = [normalize_model_id(model.get("id") or model.get("name")) for model in models]
    saved_id = normalize_model_id(saved_model)
    if saved_id and saved_id in available_ids:
        return saved_id
    configured_id = normalize_model_id(getattr(settings, "AI_MODEL", ""))
    if configured_id and configured_id in available_ids:
        return configured_id
    for preferred in PREFERRED_MODELS:
        if preferred in available_ids:
            return preferred
    return available_ids[0] if available_ids else ""


def get_configured_model(fallback=""):
    try:
        from apps.checks.models import GeminiConfiguration

        configured = GeminiConfiguration.objects.filter(pk=1).values_list("model_name", flat=True).first()
    except (OperationalError, ProgrammingError):
        configured = ""
    return normalize_model_id(configured or fallback or getattr(settings, "AI_MODEL", ""))


def _ordered_candidates(models, saved_model=""):
    available_ids = [normalize_model_id(model.get("id") or model.get("name")) for model in models]
    selected = choose_generation_model(models, saved_model)
    configured_id = normalize_model_id(getattr(settings, "AI_MODEL", ""))
    ordered = []
    for model_id in (selected, configured_id, *PREFERRED_MODELS, *available_ids):
        if model_id and model_id in available_ids and model_id not in ordered:
            ordered.append(model_id)
    return ordered[:5]


def _parts_text(parts):
    return "\n".join(str(part.get("text") or "") for part in (parts or []) if part.get("text")).strip()


def _to_openai_payload(payload, model_id):
    messages = []
    system_text = _parts_text(((payload or {}).get("systemInstruction") or {}).get("parts"))
    if system_text:
        messages.append({"role": "system", "content": system_text})
    for content in (payload or {}).get("contents") or []:
        text = _parts_text(content.get("parts"))
        if not text:
            continue
        role = str(content.get("role") or "user").lower()
        if role == "model":
            role = "assistant"
        if role not in {"system", "user", "assistant"}:
            role = "user"
        messages.append({"role": role, "content": text})
    generation_config = (payload or {}).get("generationConfig") or {}
    request_payload = {
        "model": model_id,
        "messages": messages,
        "stream": False,
    }
    max_tokens = generation_config.get("maxOutputTokens")
    if max_tokens:
        request_payload["max_tokens"] = int(max_tokens)
    if generation_config.get("responseMimeType") == "application/json":
        request_payload["response_format"] = {"type": "json_object"}
    if getattr(settings, "AI_DISABLE_THINKING", False):
        request_payload["chat_template_kwargs"] = {"enable_thinking": False}
    if "temperature" in generation_config:
        request_payload["temperature"] = generation_config["temperature"]
    return request_payload


def _from_openai_response(payload):
    choices = (payload or {}).get("choices") or []
    content = ""
    if choices:
        message = choices[0].get("message") or {}
        raw_content = message.get("content")
        if isinstance(raw_content, str):
            content = raw_content
        elif isinstance(raw_content, list):
            content = "\n".join(
                str(item.get("text") or "") for item in raw_content if isinstance(item, dict)
            ).strip()
    return {
        "candidates": [{"content": {"parts": [{"text": content}]}}],
        "usageMetadata": (payload or {}).get("usage") or {},
    }


def generate_content(
    payload,
    *,
    model="",
    api_key=None,
    timeout=None,
    models=None,
    opener=None,
):
    api_key = _validate_configuration(api_key)
    default_timeout = getattr(
        settings,
        "AI_REQUEST_TIMEOUT",
        getattr(settings, "GEMINI_REQUEST_TIMEOUT", 60),
    )
    timeout = max(1, int(timeout if timeout is not None else default_timeout))
    models = models or fetch_generation_models(api_key=api_key, opener=opener)
    candidates = _ordered_candidates(models, model)
    last_error = None
    for model_id in candidates:
        if get_provider() == "openai_compatible":
            endpoint = f"{get_api_base_url()}/chat/completions"
            request_payload = _to_openai_payload(payload, model_id)
        else:
            endpoint = f"{GEMINI_API_BASE_URL}/{model_resource_name(model_id)}:generateContent"
            request_payload = payload
        try:
            response = _request_json(
                method="POST",
                endpoint=endpoint,
                api_key=api_key,
                timeout=timeout,
                stage="generate_content",
                model=model_id,
                payload=request_payload,
                opener=opener,
            )
            if get_provider() == "openai_compatible":
                response = _from_openai_response(response)
            return response, model_id
        except GeminiAPIError as exc:
            last_error = exc
            if exc.kind not in {"http_error", "timeout", "network_error", "dns_error"}:
                break
            if exc.kind in {"network_error", "dns_error"}:
                break
            if exc.status not in {None, 400, 404, 429} and not (exc.status and exc.status >= 500):
                break
    if last_error is not None:
        raise last_error
    raise GeminiAPIError(
        stage="generate_content",
        kind="no_compatible_models",
        message="Не удалось выбрать модель для генерации текста.",
        endpoint=get_models_endpoint(),
        provider=get_provider(),
    )


def extract_response_text(payload):
    for candidate in (payload or {}).get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            text = part.get("text")
            if text:
                return text
    return ""


def test_connection(*, api_key=None, saved_model="", timeout=None, opener=None):
    api_key = _validate_configuration(api_key)
    models = fetch_generation_models(api_key=api_key, timeout=timeout, opener=opener)
    selected_model = choose_generation_model(models, saved_model)
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": "Ответь одним словом: OK"}],
            }
        ],
        "generationConfig": {"maxOutputTokens": 64},
    }
    response, used_model = generate_content(
        payload,
        model=selected_model,
        api_key=api_key,
        timeout=timeout,
        models=models,
        opener=opener,
    )
    response_text = extract_response_text(response).strip()
    if not response_text:
        raise GeminiAPIError(
            stage="generate_content",
            kind="empty_response",
            message="AI-модель вернула ответ без текста.",
            endpoint=(
                f"{get_api_base_url()}/chat/completions"
                if get_provider() == "openai_compatible"
                else f"{GEMINI_API_BASE_URL}/{model_resource_name(used_model)}:generateContent"
            ),
            model=used_model,
            provider=get_provider(),
        )
    return {
        "models": models,
        "selected_model": used_model,
        "response_text": response_text[:200],
        "steps": {
            "list_models": "success",
            "generate_content": "success",
        },
    }
