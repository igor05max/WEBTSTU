from __future__ import annotations

import json
from pathlib import Path

from paper_formatter.models import ArticleIR, TemplateProfile


def generate_schemas(output_dir: Path | None = None) -> list[Path]:
    target = output_dir or Path(__file__).resolve().parent / "schemas"
    target.mkdir(parents=True, exist_ok=True)
    schemas = {
        "article_ir.schema.json": ArticleIR.model_json_schema(),
        "template_profile.schema.json": TemplateProfile.model_json_schema(),
    }
    paths: list[Path] = []
    for name, schema in schemas.items():
        path = target / name
        path.write_text(
            json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        paths.append(path)
    return paths


if __name__ == "__main__":
    for generated in generate_schemas():
        print(generated)
