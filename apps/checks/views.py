from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.accounts.access import is_root_admin
from apps.checks.gemini_client import (
    GEMINI_MODELS_ENDPOINT,
    GeminiAPIError,
    choose_generation_model,
    fetch_generation_models,
    normalize_model_id,
    test_connection,
)
from apps.checks.models import GeminiConfiguration


@login_required
def gemini_settings(request):
    if not is_root_admin(request.user):
        raise Http404

    configuration = GeminiConfiguration.load()
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
                    "google_message": f"Получено совместимых моделей: {len(models)}.",
                    "error_code": "",
                    "endpoint": GEMINI_MODELS_ENDPOINT,
                    "model": selected_model,
                }
                configuration.save()
                messages.success(request, f"Получено моделей с поддержкой generateContent: {len(models)}.")
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
                    "google_message": "Список моделей получен, тестовая генерация выполнена.",
                    "error_code": "",
                    "endpoint": GEMINI_MODELS_ENDPOINT,
                    "model": result["selected_model"],
                    "response": result["response_text"],
                    "steps": result["steps"],
                }
                configuration.save()
                messages.success(
                    request,
                    f"Gemini подключён. Тест выполнен моделью {result['selected_model']}.",
                )
            else:
                messages.error(request, "Неизвестное действие настройки Gemini.")
        except ValueError as exc:
            configuration.last_test_status = "error"
            configuration.last_test_details = {
                "stage": "configuration",
                "kind": "invalid_key",
                "http_status": None,
                "google_message": str(exc),
                "error_code": "",
                "endpoint": GEMINI_MODELS_ENDPOINT,
                "model": configuration.model_name,
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
            "api_key_configured": bool((settings.GEMINI_API_KEY or "").strip()),
            "models": configuration.available_models,
            "diagnostics": configuration.last_test_details or {},
        },
    )
