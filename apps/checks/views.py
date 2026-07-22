from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.accounts.access import is_root_admin
from apps.checks.gemini_client import (
    GeminiAPIError,
    choose_generation_model,
    fetch_generation_models,
    get_models_endpoint,
    get_provider,
    get_provider_label,
    is_ai_configured,
    normalize_model_id,
    test_connection,
)
from apps.checks.models import GeminiConfiguration


@login_required
def gemini_settings(request):
    if not is_root_admin(request.user):
        raise Http404

    configuration = GeminiConfiguration.load()
    provider = get_provider()
    provider_label = get_provider_label()
    models_endpoint = get_models_endpoint()
    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        try:
            if action == "fetch_models":
                models = fetch_generation_models()
                selected_model = choose_generation_model(
                    models,
                    configuration.model_name or settings.SUBMISSION_ROUTE_SUGGESTION_MODEL,
                )
                configuration.available_models = models
                configuration.model_name = selected_model
                configuration.models_refreshed_at = timezone.now()
                configuration.last_test_status = "models_loaded"
                configuration.last_test_details = {
                    "stage": "list_models",
                    "kind": "success",
                    "http_status": 200,
                    "provider_message": f"Получено совместимых моделей: {len(models)}.",
                    "google_message": f"Получено совместимых моделей: {len(models)}.",
                    "error_code": "",
                    "endpoint": models_endpoint,
                    "model": selected_model,
                    "provider": provider,
                }
                configuration.save()
                messages.success(request, f"Получено доступных моделей: {len(models)}.")
            elif action == "save_model":
                selected_model = normalize_model_id(request.POST.get("model_name"))
                allowed_models = {
                    normalize_model_id(item.get("id") or item.get("name"))
                    for item in configuration.available_models
                }
                if not selected_model or selected_model not in allowed_models:
                    messages.error(request, "Сначала обновите список моделей и выберите модель из него.")
                else:
                    configuration.model_name = selected_model
                    configuration.save(update_fields=["model_name", "updated_at"])
                    messages.success(request, f"Для запросов выбрана модель {selected_model}.")
            elif action == "test_connection":
                result = test_connection(saved_model=configuration.model_name)
                configuration.available_models = result["models"]
                configuration.model_name = result["selected_model"]
                configuration.models_refreshed_at = timezone.now()
                configuration.last_test_status = "success"
                configuration.last_test_details = {
                    "stage": "complete",
                    "kind": "success",
                    "http_status": 200,
                    "provider_message": "Список моделей получен, тестовая генерация выполнена.",
                    "google_message": "Список моделей получен, тестовая генерация выполнена.",
                    "error_code": "",
                    "endpoint": models_endpoint,
                    "model": result["selected_model"],
                    "provider": provider,
                    "response": result["response_text"],
                    "steps": result["steps"],
                }
                configuration.save()
                messages.success(
                    request,
                    (
                        f"Gemini подключён. Тест выполнен моделью {result['selected_model']}."
                        if provider == "gemini"
                        else f"{provider_label} подключена. Тест выполнен моделью {result['selected_model']}."
                    ),
                )
            else:
                messages.error(request, "Неизвестное действие настройки AI-модели.")
        except ValueError as exc:
            configuration.last_test_status = "error"
            configuration.last_test_details = {
                "stage": "configuration",
                "kind": "invalid_key",
                "http_status": None,
                "provider_message": str(exc),
                "google_message": str(exc),
                "error_code": "",
                "endpoint": models_endpoint,
                "model": configuration.model_name,
                "provider": provider,
            }
            configuration.save(update_fields=["last_test_status", "last_test_details", "updated_at"])
            messages.error(request, str(exc))
        except GeminiAPIError as exc:
            configuration.last_test_status = "error"
            configuration.last_test_details = exc.as_dict()
            configuration.save(update_fields=["last_test_status", "last_test_details", "updated_at"])
            stage_name = "получения списка моделей" if exc.stage == "list_models" else "тестовой генерации"
            messages.error(request, f"Ошибка на этапе {stage_name}: {exc}")
        return redirect("checks:gemini_settings")

    return render(
        request,
        "checks/gemini_settings.html",
        {
            "configuration": configuration,
            "connection_configured": is_ai_configured(),
            "api_key_configured": is_ai_configured(),
            "provider": provider,
            "provider_label": provider_label,
            "models_endpoint": models_endpoint,
            "generation_method": "chat/completions" if provider == "openai_compatible" else "generateContent",
            "models": configuration.available_models,
            "diagnostics": configuration.last_test_details or {},
            "diagnostic_message": (
                (configuration.last_test_details or {}).get("provider_message")
                or (configuration.last_test_details or {}).get("google_message")
                or ""
            ),
        },
    )
