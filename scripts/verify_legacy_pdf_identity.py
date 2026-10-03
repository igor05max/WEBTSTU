"""Compare locally saved old RAG PDFs with the new Drive manifest by SHA-256.

Also checks that the expected DOI appears on the first PDF page. This checks
article identity, not the current availability of a public citation URL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]


def run(args):
    records = [json.loads(line) for line in args.manifest.open(encoding="utf-8") if line.strip()]
    candidates = [record for record in records if record.get("legacy_elibrary_id")]
    pdfs = list(args.pdf_root.rglob("*.pdf"))
    by_id = {}
    for path in pdfs:
        match = re.match(r"(\d+)\s*-", path.name)
        if match:
            by_id.setdefault(match.group(1), []).append(path)
    results = []
    for record in candidates:
        paths = by_id.get(record["legacy_elibrary_id"], [])
        result = {
            "record_id": record["record_id"],
            "doi": record["doi"],
            "legacy_elibrary_id": record["legacy_elibrary_id"],
            "file_count": len(paths),
            "sha256_match": False,
            "first_page_doi_match": False,
            "error": "",
        }
        if len(paths) != 1:
            result["error"] = "expected_one_local_pdf"
        else:
            path = paths[0]
            result["sha256_match"] = hashlib.sha256(path.read_bytes()).hexdigest() == record["pdf_sha256"]
            try:
                first_page = PdfReader(path).pages[0].extract_text() or ""
                normalized = re.sub(r"\s+", "", first_page).casefold()
                result["first_page_doi_match"] = record["doi"].casefold() in normalized
            except Exception as error:
                result["error"] = type(error).__name__ + ": " + str(error)[:120]
        results.append(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        for result in results:
            handle.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"Checked {len(results)} old PDFs")
    print("SHA-256 matches:", sum(result["sha256_match"] for result in results))
    print("First-page DOI matches:", sum(result["first_page_doi_match"] for result in results))
    print("Errors:", dict(Counter(result["error"] for result in results if result["error"])))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-root", type=Path, default=ROOT / "downloads_elibrary")
    parser.add_argument("--manifest", type=Path, default=ROOT / "data/tgtu_corpus/manifest.jsonl")
    parser.add_argument("--output", type=Path, default=ROOT / "data/tgtu_corpus/legacy_pdf_checks.jsonl")
    run(parser.parse_args())
