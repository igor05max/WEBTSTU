from io import BytesIO
import urllib.error
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from apps.checks.gemini_client import (
    GeminiAPIError,
    choose_generation_model,
    fetch_generation_models,
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


class GeminiClientTests(SimpleTestCase):
    def test_aq_key_passes_local_validation(self):
        self.assertEqual(validate_api_key("AQ.example-working-key"), "AQ.example-working-key")

    def test_model_list_uses_name_and_excludes_models_without_generate_content(self):
        models = parse_generation_models(
            {
                "models": [
                    {
                        "name": "models/gemini-2.5-flash",
                        "displayName": "Friendly display name",
                        "supportedGenerationMethods": ["generateContent", "countTokens"],
                    },
                    {
                        "name": "models/text-embedding-004",
                        "displayName": "Embedding",
                        "supportedGenerationMethods": ["embedContent"],
                    },
                ]
            }
        )

        self.assertEqual([model["id"] for model in models], ["gemini-2.5-flash"])
        self.assertEqual(models[0]["name"], "models/gemini-2.5-flash")
        self.assertNotEqual(models[0]["id"], models[0]["display_name"])

    def test_models_prefix_is_removed_for_sdk_style_identifier(self):
        self.assertEqual(normalize_model_id("models/gemini-3.1-flash-lite"), "gemini-3.1-flash-lite")

    def test_missing_saved_model_is_replaced_with_available_preferred_model(self):
        models = [
            {"id": "gemini-experimental"},
            {"id": "gemini-2.5-flash-lite"},
        ]
        self.assertEqual(
            choose_generation_model(models, "gemini-removed-model"),
            "gemini-2.5-flash-lite",
        )

    def test_fetch_models_timeout_is_never_below_thirty_seconds(self):
        observed = {}

        def opener(_request, timeout):
            observed["timeout"] = timeout
            return _JSONResponse(
                b'{"models":[{"name":"models/gemini-2.5-flash","supportedGenerationMethods":["generateContent"]}]}'
            )

        fetch_generation_models(api_key="AQ.example", timeout=1, opener=opener)

        self.assertEqual(observed["timeout"], 30)

    def test_api_key_is_redacted_from_error_message_and_diagnostics(self):
        api_key = "AQ.secret-value-never-show"

        def opener(request, timeout):
            body = (
                '{"error":{"code":400,"status":"INVALID_ARGUMENT",'
                f'"message":"bad key {api_key} at https://example.test?key={api_key}"}}}}'
            ).encode("utf-8")
            raise urllib.error.HTTPError(request.full_url, 400, "Bad Request", {}, BytesIO(body))

        with self.assertRaises(GeminiAPIError) as caught:
            fetch_generation_models(api_key=api_key, opener=opener)

        rendered = f"{caught.exception} {caught.exception.as_dict()}"
        self.assertNotIn(api_key, rendered)
        self.assertIn("[API_KEY_REDACTED]", rendered)


@override_settings(ROOT_ADMIN_USERNAME="rootUser", GEMINI_API_KEY="AQ.ui-secret-key")
class GeminiSettingsViewTests(TestCase):
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
        self.assertEqual(self.client.get(reverse("checks:gemini_settings")).status_code, 404)

        self.client.force_login(self.root_user)
        response = self.client.get(reverse("checks:gemini_settings"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ключ настроен")
        self.assertNotContains(response, "AQ.ui-secret-key")

    @patch("apps.checks.views.test_connection")
    def test_connection_action_saves_selected_available_model(self, mocked_test_connection):
        mocked_test_connection.return_value = {
            "models": [
                {
                    "id": "gemini-2.5-flash",
                    "name": "models/gemini-2.5-flash",
                    "display_name": "Gemini 2.5 Flash",
                    "supported_generation_methods": ["generateContent"],
                }
            ],
            "selected_model": "gemini-2.5-flash",
            "response_text": "OK",
            "steps": {"list_models": "success", "generate_content": "success"},
        }
        self.client.force_login(self.root_user)

        response = self.client.post(
            reverse("checks:gemini_settings"),
            {"action": "test_connection"},
            follow=True,
        )

        self.assertContains(response, "Gemini подключён")
        self.assertContains(response, "gemini-2.5-flash")
        self.assertNotContains(response, "AQ.ui-secret-key")
