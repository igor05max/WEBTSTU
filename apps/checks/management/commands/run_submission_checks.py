from django.core.management.base import BaseCommand, CommandError

from apps.checks.services import run_submission_checks_by_id


class Command(BaseCommand):
    help = "Runs background checks for a single submission version."

    def add_arguments(self, parser):
        parser.add_argument("submission_id", type=int)
        parser.add_argument("--version-id", type=int, required=True)
        parser.add_argument(
            "--resume-workflow-after-success",
            action="store_true",
            help="Resume workflow automatically after successful checks.",
        )

    def handle(self, *args, **options):
        try:
            completed = run_submission_checks_by_id(
                options["submission_id"],
                version_id=options["version_id"],
                resume_workflow_after_success=options["resume_workflow_after_success"],
            )
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS("completed" if completed else "skipped"))
