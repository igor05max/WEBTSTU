"""Copy audited citation status into the TGTU corpus manifest.

Only publisher/DOI URLs with a verified article identity become
``verified_source_url``. Old eLIBRARY URLs remain access-limited fallbacks.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_index(path: Path) -> dict:
    rows = [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]
    index = {row["record_id"]: row for row in rows}
    if len(index) != len(rows):
        raise ValueError(f"Duplicate record IDs in {path}")
    return index


def run(args):
    audit = load_index(args.audit)
    old_pdfs = load_index(args.legacy_pdf_checks)
    manifest = [json.loads(line) for line in args.manifest.open(encoding="utf-8") if line.strip()]
    if set(audit) != {record["record_id"] for record in manifest}:
        raise ValueError("Audit does not cover the current manifest exactly")
    for record in manifest:
        rid = record["record_id"]
        result = audit[rid]
        record["link_verification"] = result["status"]
        record["link_issue"] = result["reason"]
        record["verified_source_url"] = (
            result["recommended_url"] if result["status"] in {"verified", "verified_alternate"} else ""
        )
        if rid in old_pdfs:
            check = old_pdfs[rid]
            record["legacy_pdf_identity_verified"] = check["sha256_match"] and check["first_page_doi_match"]
            record["legacy_fallback_url"] = record["legacy_elibrary_url"] if record["legacy_pdf_identity_verified"] else ""
        else:
            record.pop("legacy_pdf_identity_verified", None)
            record.pop("legacy_fallback_url", None)
    tmp = args.manifest.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        for record in manifest:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    tmp.replace(args.manifest)
    print("Statuses:", dict(Counter(record["link_verification"] for record in manifest)))
    print("Verified source URLs:", sum(bool(record["verified_source_url"]) for record in manifest))
    print("Identity-checked eLIBRARY fallbacks:", sum(bool(record.get("legacy_fallback_url")) for record in manifest))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "data/tgtu_corpus/manifest.jsonl")
    parser.add_argument("--audit", type=Path, default=ROOT / "data/tgtu_corpus/link_audit.jsonl")
    parser.add_argument("--legacy-pdf-checks", type=Path, default=ROOT / "data/tgtu_corpus/legacy_pdf_checks.jsonl")
    run(parser.parse_args())
