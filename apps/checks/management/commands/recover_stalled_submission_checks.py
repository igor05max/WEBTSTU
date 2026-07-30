from django.core.management.base import BaseCommand

from apps.checks.services import recover_stalled_submission_checks


class Command(BaseCommand):
    help = (
        "Recover submissions left in auto_checking when their background "
        "check process did not start."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--submission-id",
            action="append",
            type=int,
            dest="submission_ids",
            help="Limit recovery to one or more submission IDs.",
        )
        parser.add_argument(
            "--no-run-grace-seconds",
            type=int,
            default=90,
        )
        parser.add_argument(
            "--pending-grace-seconds",
            type=int,
            default=300,
        )
        parser.add_argument(
            "--processing-template-grace-seconds",
            type=int,
            default=600,
        )

    def handle(self, *args, **options):
        result = recover_stalled_submission_checks(
            no_run_grace_seconds=max(0, options["no_run_grace_seconds"]),
            pending_grace_seconds=max(0, options["pending_grace_seconds"]),
            processing_template_grace_seconds=max(
                0,
                options["processing_template_grace_seconds"],
            ),
            submission_ids=options.get("submission_ids"),
        )
        recovered = ", ".join(
            f"{submission_id}:{'ok' if completed else 'skipped'}"
            for submission_id, completed in result["recovered"]
        )
        skipped = ", ".join(
            f"{submission_id}:{reason}"
            for submission_id, reason in result["skipped"]
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Recovered [{recovered or '-'}]; skipped [{skipped or '-'}]."
            )
        )
