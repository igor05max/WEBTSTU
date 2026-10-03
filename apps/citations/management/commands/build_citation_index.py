from django.conf import settings
from django.core.management.base import BaseCommand

from apps.citations.index import build_index


class Command(BaseCommand):
    help = "Строит гибридный индекс eLIBRARY или расширенного корпуса ТГТУ."

    def add_arguments(self, parser):
        parser.add_argument("--corpus", default=str(settings.CITATION_CORPUS_ROOT))
        parser.add_argument("--output", default=str(settings.CITATION_INDEX_PATH))
        parser.add_argument("--manifest", default=None, help="Манифест корпуса ТГТУ")
        parser.add_argument("--archives", default=None, help="Каталог годовых ZIP корпуса ТГТУ")

    def handle(self, *args, **options):
        def progress(articles, chunks):
            self.stdout.write(f"Обработано статей: {articles}; фрагментов: {chunks}")

        result = build_index(
            corpus_root=options["corpus"],
            index_path=options["output"],
            manifest_path=options["manifest"],
            archive_root=options["archives"],
            progress=progress,
        )
        self.stdout.write(
            self.style.SUCCESS(
                "Индекс готов: "
                f"{result['article_count']} статей, {result['chunk_count']} фрагментов, "
                f"{result['embedding_backend']}."
            )
        )
