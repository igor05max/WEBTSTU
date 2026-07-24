import logging
import os
import subprocess
import sys

from django.conf import settings
from django.db import close_old_connections, transaction

from apps.directory.formatting_templates import (
    build_rules_snapshot,
    process_formatting_template,
)
from apps.directory.models import FormattingTemplate
from apps.submissions.models import Submission


logger = logging.getLogger(__name__)


def prepare_submission_template_by_id(
    submission_id,
    *,
    template_id,
    expected_version_id,
    start_checks=True,
):
    """Extract template rules, refresh the snapshot and optionally launch checks."""
    close_old_connections()
    submission = None
    try:
        template = FormattingTemplate.objects.get(pk=template_id)
        if (
            template.analysis_status not in {"ready", "partial"}
            or not template.extracted_rules
        ):
            process_formatting_template(template)
        submission = Submission.objects.select_related(
            "article_type",
            "journal",
            "current_version",
        ).get(pk=submission_id)
        if submission.current_version_id != expected_version_id:
            logger.info(
                "Skipping template preparation for submission %s: current version changed.",
                submission_id,
            )
            return False

        submission.formatting_rules_snapshot = build_rules_snapshot(
            article_type=submission.article_type,
            template=template,
            journal=submission.journal,
        )
        submission.save(update_fields=["formatting_rules_snapshot", "updated_at"])
    except Exception:
        logger.exception(
            "Failed to prepare formatting template %s for submission %s.",
            template_id,
            submission_id,
        )
        if submission is None:
            try:
                submission = Submission.objects.select_related("current_version").get(
                    pk=submission_id
                )
            except Submission.DoesNotExist:
                return False
    finally:
        close_old_connections()

    submission.refresh_from_db()
    if submission.current_version_id != expected_version_id:
        return False

    if start_checks:
        from apps.checks.services import queue_submission_checks

        queue_submission_checks(submission)
    close_old_connections()
    return True


def launch_submission_template_process(
    submission_id,
    template_id,
    version_id,
    *,
    start_checks=True,
):
    command = [
        sys.executable,
        str(settings.BASE_DIR / "manage.py"),
        "prepare_submission_template",
        str(submission_id),
        "--template-id",
        str(template_id),
        "--version-id",
        str(version_id),
    ]
    if not start_checks:
        command.append("--skip-checks")
    popen_kwargs = {
        "cwd": str(settings.BASE_DIR),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        "env": os.environ.copy(),
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(command, **popen_kwargs)


def queue_submission_template_processing(submission, template, *, start_checks=True):
    submission.refresh_from_db()
    version = submission.current_version
    if version is None:
        raise ValueError("Submission must have a current version before template processing.")

    if not settings.SUBMISSION_CHECKS_ASYNC:
        prepare_submission_template_by_id(
            submission.id,
            template_id=template.id,
            expected_version_id=version.id,
            start_checks=start_checks,
        )
        return

    transaction.on_commit(
        lambda: launch_submission_template_process(
            submission.id,
            template.id,
            version.id,
            start_checks=start_checks,
        )
    )
