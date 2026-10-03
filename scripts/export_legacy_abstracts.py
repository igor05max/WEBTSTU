"""Export old RAG abstracts for DOI-matched TGTU articles.

The exported text is used only for retrieval; the archived journal PDF remains
the evidence shown to users and the PDF served by the citation link.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(args):
    records = [json.loads(line) for line in args.manifest.open(encoding="utf-8") if line.strip()]
    connection = sqlite3.connect(args.legacy_index)
    exported = []
    try:
        for record in records:
            article_id = record.get("legacy_elibrary_id")
            if not article_id or record["decision"] != "candidate":
                continue
            existing = connection.execute(
                "SELECT doi FROM articles WHERE article_id = ?", (article_id,)
            ).fetchone()
            if not existing or existing[0].casefold() != record["doi"].casefold():
                raise ValueError(f"Legacy article DOI mismatch: {article_id}")
            abstracts = [
                {"kind": kind, "text": text}
                for kind, text in connection.execute(
                    "SELECT kind, text FROM chunks WHERE article_id = ? "
                    "AND kind IN ('abstract', 'english_abstract') ORDER BY position",
                    (article_id,),
                )
                if text.strip()
            ]
            exported.append({"record_id": record["record_id"], "abstracts": abstracts})
    finally:
        connection.close()
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        for item in exported:
            handle.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
    print("Matched articles:", len(exported))
    print("Abstract passages:", sum(len(item["abstracts"]) for item in exported))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "data/tgtu_corpus/manifest.jsonl")
    parser.add_argument("--legacy-index", type=Path, default=ROOT / "tmp/citation_index.sqlite3")
    parser.add_argument("--output", type=Path, default=ROOT / "data/tgtu_corpus/legacy_abstracts.jsonl")
    run(parser.parse_args())
