from io import BytesIO
import json
import urllib.error
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from apps.checks.ai_client import (
    AIProviderError,
    choose_generation_model,
    fetch_generation_models,
    generate_content,
    normalize_model_id,
    parse_generation_models,
    validate_api_key,
)


class _JSONResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


@override_settings(AI_BASE_URL="http://192.0.2.10:8088/v1")
class AIClientTests(SimpleTestCase):
    def test_optional_local_key_passes_validation(self):
        self.assertEqual(validate_api_key("local-secret"), "local-secret")

    def test_model_list_uses_openai_compatible_ids(self):
        models = parse_generation_models(
            {
                "data": [
                    {
                        "id": "Qwen3.6-27B-IQ4_XS.gguf",
                        "display_name": "Qwen local",
                    },
                ]
            }
        )

        self.assertEqual(
            [model["id"] for model in models],
            ["Qwen3.6-27B-IQ4_XS.gguf"],
        )
        self.assertEqual(models[0]["name"], "Qwen3.6-27B-IQ4_XS.gguf")

    def test_models_prefix_is_removed_for_sdk_style_identifier(self):
        self.assertEqual(
            normalize_model_id("models/Qwen3.6-27B-IQ4_XS.gguf"),
            "Qwen3.6-27B-IQ4_XS.gguf",
        )

    def test_missing_saved_model_is_replaced_with_available_qwen_model(self):
        models = [
            {"id": "other-local-model"},
            {"id": "Qwen3.6-27B-IQ4_XS.gguf"},
        ]
        self.assertEqual(
            choose_generation_model(models, "removed-model"),
            "Qwen3.6-27B-IQ4_XS.gguf",
        )

    def test_fetch_models_honors_short_diagnostic_timeout(self):
        observed = {}

        def opener(_request, timeout):
            observed["timeout"] = timeout
            return _JSONResponse(
                b'{"data":[{"id":"Qwen3.6-27B-IQ4_XS.gguf"}]}'
            )

        fetch_generation_models(api_key="local-secret", timeout=1, opener=opener)

        self.assertEqual(observed["timeout"], 1)

    def test_api_key_is_redacted_from_error_message_and_diagnostics(self):
        api_key = "local-secret-value-never-show"

        def opener(request, timeout):
            body = (
                '{"error":{"code":400,"status":"INVALID_ARGUMENT",'
                f'"message":"bad key {api_key}"}}}}'
            ).encode("utf-8")
            raise urllib.error.HTTPError(request.full_url, 400, "Bad Request", {}, BytesIO(body))

        with self.assertRaises(AIProviderError) as caught:
            fetch_generation_models(api_key=api_key, opener=opener)

        rendered = f"{caught.exception} {caught.exception.as_dict()}"
        self.assertNotIn(api_key, rendered)
        self.assertIn("[API_KEY_REDACTED]", rendered)


@override_settings(
    AI_PROVIDER="openai_compatible",
    AI_BASE_URL="http://192.0.2.10:8088/v1",
    AI_API_KEY="",
    AI_MODEL="Qwen-local.gguf",
    AI_DISABLE_THINKING=True,
)
class OpenAICompatibleClientTests(SimpleTestCase):
    def test_fetches_openai_compatible_models_without_api_key(self):
        observed = {}

        def opener(request, timeout):
            observed["url"] = request.full_url
            observed["authorization"] = request.get_header("Authorization")
            return _JSONResponse(b'{"data":[{"id":"Qwen-local.gguf","object":"model"}]}')

        models = fetch_generation_models(opener=opener)

        self.assertEqual(models[0]["id"], "Qwen-local.gguf")
        self.assertEqual(observed["url"], "http://192.0.2.10:8088/v1/models")
        self.assertIsNone(observed["authorization"])

    def test_translates_structured_payload_to_chat_completions(self):
        observed = {}

        def opener(request, timeout):
            observed["url"] = request.full_url
            observed["payload"] = json.loads(request.data.decode("utf-8"))
            return _JSONResponse(
                b'{"choices":[{"message":{"role":"assistant","content":"{\\"ok\\":true}"}}]}'
            )

        response, model = generate_content(
            {
                "systemInstruction": {"parts": [{"text": "Return JSON."}]},
                "contents": [{"role": "user", "parts": [{"text": "Test"}]}],
                "generationConfig": {
                    "maxOutputTokens": 128,
                    "responseMimeType": "application/json",
                },
            },
            models=[{"id": "Qwen-local.gguf"}],
            opener=opener,
        )

        self.assertEqual(model, "Qwen-local.gguf")
        self.assertEqual(observed["url"], "http://192.0.2.10:8088/v1/chat/completions")
        self.assertEqual(observed["payload"]["model"], "Qwen-local.gguf")
        self.assertEqual(observed["payload"]["messages"][0]["role"], "system")
        self.assertEqual(observed["payload"]["response_format"], {"type": "json_object"})
        self.assertEqual(
            observed["payload"]["chat_template_kwargs"],
            {"enable_thinking": False},
        )
        self.assertEqual(
            response["candidates"][0]["content"]["parts"][0]["text"],
            '{"ok":true}',
        )


@override_settings(
    ROOT_ADMIN_USERNAME="rootUser",
    AI_BASE_URL="http://192.0.2.10:8088/v1",
    AI_API_KEY="local-ui-secret-key",
)
class AISettingsViewTests(TestCase):
    def setUp(self):
        self.root_user = get_user_model().objects.create_superuser(
            username="rootUser",
            password="1234",
        )
        self.regular_user = get_user_model().objects.create_user(
            username="regular",
            password="1234",
        )

    def test_only_root_admin_can_open_settings_and_key_is_never_rendered(self):
        self.client.force_login(self.regular_user)
        self.assertEqual(self.client.get(reverse("checks:ai_settings")).status_code, 404)

        self.client.force_login(self.root_user)
        response = self.client.get(reverse("checks:ai_settings"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Подключение настроено")
        self.assertNotContains(response, "local-ui-secret-key")

    @patch("apps.checks.views.test_connection")
    def test_connection_action_saves_selected_available_model(self, mocked_test_connection):
        mocked_test_connection.return_value = {
            "models": [
                {
                    "id": "Qwen3.6-27B-IQ4_XS.gguf",
                    "name": "Qwen3.6-27B-IQ4_XS.gguf",
                    "display_name": "Qwen local",
                    "supported_generation_methods": ["chat/completions"],
                }
            ],
            "selected_model": "Qwen3.6-27B-IQ4_XS.gguf",
            "response_text": "OK",
            "steps": {"list_models": "success", "generate_content": "success"},
        }
        self.client.force_login(self.root_user)

        response = self.client.post(
            reverse("checks:ai_settings"),
            {"action": "test_connection"},
            follow=True,
        )

        self.assertContains(response, "Локальная модель Qwen подключена")
        self.assertContains(response, "Qwen3.6-27B-IQ4_XS.gguf")
        self.assertNotContains(response, "local-ui-secret-key")
