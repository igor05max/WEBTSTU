import json
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.checks.ai_client import AIProviderError
from apps.submissions.formatting_correction import (
    FormattingCorrectionError,
    build_corrected_docx,
)
from apps.submissions.paper_formatter_ai import QwenSemanticProvider
from paper_formatter.config import SemanticSettings
from paper_formatter.exceptions import PaperFormatterError
from paper_formatter.semantic.models import SemanticBlock


def _block(block_id="p1", text="Неоднозначный блок"):
    return SemanticBlock(block_id=block_id, order=0, text=text)


@override_settings(AI_MODEL="Qwen-local.gguf")
class QwenSemanticProviderTests(SimpleTestCase):
    @patch("apps.submissions.paper_formatter_ai.generate_content")
    def test_returns_only_structural_decisions_and_discards_rewritten_text(
        self,
        mocked_generate,
    ):
        response_text = json.dumps(
            {
                "decisions": [
                    {
                        "block_id": "p1",
                        "role": "section",
                        "confidence": 0.91,
                        "heading_level": 1,
                        "normalized_text": "Переписанный моделью текст",
                        "reason": "похож на заголовок",
                    }
                ]
            },
            ensure_ascii=False,
        )
        mocked_generate.return_value = (
            {
                "candidates": [
                    {"content": {"parts": [{"text": response_text}]}}
                ]
            },
            "Qwen-local.gguf",
        )
        provider = QwenSemanticProvider(
            SemanticSettings(enabled=True, provider="qwen")
        )

        result = provider.analyze_document([_block()], document_name="article.docx")

        self.assertEqual(result.provider, "qwen:Qwen-local.gguf")
        self.assertEqual(result.decisions[0].role, "section")
        self.assertIsNone(result.decisions[0].normalized_text)
        request_payload = mocked_generate.call_args.args[0]
        prompt = request_payload["contents"][0]["parts"][0]["text"]
        self.assertIn("Не изменяй и не дополняй содержание текста", prompt)

    @patch("apps.submissions.paper_formatter_ai.generate_content")
    def test_qwen_failure_is_nonblocking_and_returns_no_ai_overrides(
        self,
        mocked_generate,
    ):
        mocked_generate.side_effect = AIProviderError(
            stage="generate_content",
            kind="network_error",
            message="Нет соединения через VPN.",
            endpoint="http://192.0.2.1/v1/chat/completions",
        )
        provider = QwenSemanticProvider(
            SemanticSettings(enabled=True, provider="qwen")
        )

        result = provider.analyze_document([_block()], document_name="article.docx")

        self.assertEqual(result.provider, "qwen")
        self.assertEqual(result.decisions, [])
        self.assertIn("локальными правилами", result.warnings[0])


class FormatterFallbackTests(SimpleTestCase):
    @patch("apps.submissions.formatting_correction._build_with_ported_formatter")
    @patch("apps.submissions.formatting_correction._template_file")
    @patch("apps.submissions.formatting_correction._source_docx_and_rules")
    def test_real_template_error_does_not_fall_back_to_legacy_renderer(
        self,
        mocked_source,
        mocked_template,
        mocked_new_formatter,
    ):
        mocked_source.return_value = (b"source", {"body": {}})
        mocked_template.return_value = ("template.docx", b"template")
        mocked_new_formatter.side_effect = PaperFormatterError("broken template")

        with self.assertRaisesMessage(
            FormattingCorrectionError,
            "Новый редактор не смог применить файл-шаблон",
        ):
            build_corrected_docx(object())
