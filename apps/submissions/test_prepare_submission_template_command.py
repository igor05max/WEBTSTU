import sys
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase

from apps.submissions.management.commands.prepare_submission_template import (
    Command,
)
from apps.submissions.template_processing import (
    launch_submission_template_process,
)


class PrepareSubmissionTemplateCommandTests(SimpleTestCase):
    def test_parser_exposes_distinct_submission_skip_option(self):
        parser = Command().create_parser(
            "manage.py",
            "prepare_submission_template",
        )

        options = vars(
            parser.parse_args(
                [
                    "67",
                    "--template-id",
                    "16",
                    "--version-id",
                    "85",
                    "--skip-submission-checks",
                ]
            )
        )

        self.assertTrue(options["skip_submission_checks"])
        self.assertFalse(options["skip_checks"])
        help_text = parser.format_help()
        self.assertIn("--skip-submission-checks", help_text)
        self.assertIn("--skip-checks", help_text)

    @patch(
        "apps.submissions.management.commands."
        "prepare_submission_template.prepare_submission_template_by_id"
    )
    def test_handle_maps_submission_skip_option_to_start_checks(self, prepare):
        prepare.return_value = True

        Command().handle(
            submission_id=67,
            template_id=16,
            version_id=85,
            skip_submission_checks=True,
        )

        prepare.assert_called_once_with(
            67,
            template_id=16,
            expected_version_id=85,
            start_checks=False,
        )

    @patch("apps.submissions.template_processing.subprocess.Popen")
    def test_launcher_uses_exact_custom_skip_option_argv(self, popen):
        launch_submission_template_process(
            67,
            16,
            85,
            start_checks=False,
        )

        self.assertEqual(
            popen.call_args.args[0],
            [
                sys.executable,
                str(settings.BASE_DIR / "manage.py"),
                "prepare_submission_template",
                "67",
                "--template-id",
                "16",
                "--version-id",
                "85",
                "--skip-submission-checks",
            ],
        )
