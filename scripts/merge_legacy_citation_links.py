"""Join the old eLIBRARY citation catalog to the new TGTU manifest by DOI.

Run with --legacy-csv pointing at downloads_elibrary/journal_articles_full_metadata.csv.
No eLIBRARY URL is marked live merely because it exists in the old catalog.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data/tgtu_corpus/manifest.jsonl"


def pages(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", value or "")[:2])


def title(value: str) -> str:
    return "".join(char for char in value.casefold().replace("ё", "е") if char.isalnum())


def valid_item_url(url: str, article_id: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and parsed.netloc in {"elibrary.ru", "www.elibrary.ru"}
        and parsed.path == "/item.asp"
        and parse_qs(parsed.query).get("id") == [article_id]
        and article_id.isdecimal()
    )


def run(args):
    with args.legacy_csv.open(encoding="utf-8-sig", newline="") as handle:
        old_rows = list(csv.DictReader(handle, delimiter=";"))
    by_doi = {}
    for row in old_rows:
        doi = row["doi"].strip().casefold()
        if doi:
            if doi in by_doi:
                raise ValueError(f"Duplicate DOI in old catalog: {doi}")
            by_doi[doi] = row

    records = [json.loads(line) for line in args.manifest.open(encoding="utf-8") if line.strip()]
    counts = Counter()
    for record in records:
        for key in ("legacy_elibrary_url", "legacy_elibrary_id", "legacy_match", "legacy_link_verification"):
            record.pop(key, None)
        if record["decision"] != "candidate":
            continue
        old = by_doi.get(record["doi"].casefold())
        if not old:
            continue
        if str(record["year"]) != old["article_year"].strip():
            raise ValueError(f"Year mismatch for {record['doi']}")
        if pages(record["pages"]) != pages(old["pages"]):
            raise ValueError(f"Pages mismatch for {record['doi']}")
        article_id = old["article_id"].strip()
        url = old["article_url"].strip()
        if not valid_item_url(url, article_id):
            raise ValueError(f"Invalid old article link for {record['doi']}: {url}")
        record["legacy_elibrary_url"] = url
        record["legacy_elibrary_id"] = article_id
        record["legacy_match"] = (
            "doi_year_pages_title" if title(record["title"]) == title(old["title"])
            else "doi_year_pages_title_variant"
        )
        record["legacy_link_verification"] = "unverified"
        counts[record["legacy_match"]] += 1

    tmp = args.manifest.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    tmp.replace(args.manifest)
    print(f"Matched {sum(counts.values())} articles: {dict(counts)}")
    print("Old eLIBRARY URLs are catalog matches; live access has not been verified.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-csv", type=Path, default=ROOT / "downloads_elibrary/journal_articles_full_metadata.csv")
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    run(parser.parse_args())
