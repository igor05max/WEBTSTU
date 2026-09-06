from django.core.management.base import BaseCommand
import os
import signal

from apps.template_workspace.services import run_job


class Command(BaseCommand):
    help = "Build a saved article/template pair as a LaTeX project and PDF."

    def add_arguments(self, parser):
        parser.add_argument("job_id")

    def handle(self, *args, **options):
        if os.name != "nt":
            import resource
            resource.setrlimit(resource.RLIMIT_CPU, (900, 900))
            resource.setrlimit(resource.RLIMIT_FSIZE, (250 * 1024 * 1024, 250 * 1024 * 1024))
            signal.signal(signal.SIGALRM, self.timeout)
            signal.alarm(18 * 60)
        try:
            run_job(options["job_id"])
        finally:
            if os.name != "nt":
                signal.alarm(0)

    @staticmethod
    def timeout(signum, frame):
        raise TimeoutError("Template job exceeded the processing deadline")
