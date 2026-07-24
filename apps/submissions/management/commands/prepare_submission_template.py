from django.core.management.base import BaseCommand, CommandError

from apps.submissions.template_processing import prepare_submission_template_by_id


class Command(BaseCommand):
    help = "Extracts formatting rules for a submission template and starts checks."

    def add_arguments(self, parser):
        parser.add_argument("submission_id", type=int)
        parser.add_argument("--template-id", type=int, required=True)
        parser.add_argument("--version-id", type=int, required=True)

    def handle(self, *args, **options):
        try:
            completed = prepare_submission_template_by_id(
                options["submission_id"],
                template_id=options["template_id"],
                expected_version_id=options["version_id"],
            )
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS("completed" if completed else "skipped"))
