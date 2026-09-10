from __future__ import annotations

import os
import signal

from django.core.management.base import BaseCommand

from apps.template_workspace.v2.services import run_v2_job


class Command(BaseCommand):
    help = "Run Word-first V2 analysis for a saved article/template pair."

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
            run_v2_job(options["job_id"])
        finally:
            if os.name != "nt":
                signal.alarm(0)

    @staticmethod
    def timeout(signum, frame):
        raise TimeoutError("Template V2 job exceeded the processing deadline")
