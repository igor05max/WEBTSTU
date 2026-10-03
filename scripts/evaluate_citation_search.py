"""Run a small, fixed retrieval smoke check and report latency and hit@5."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from apps.citations.index import search_claim  # noqa: E402


def run(args):
    cases = [json.loads(line) for line in args.cases.open(encoding="utf-8") if line.strip()]
    durations = []
    ranks = []
    for case in cases:
        claim = {"type": case["kind"], "text": case["query"], "query_ru": case["query"]}
        start = time.perf_counter()
        results = search_claim(claim, limit=5, index_path=args.index)
        durations.append(time.perf_counter() - start)
        dois = [result["doi"] for result in results]
        rank = dois.index(case["doi"]) + 1 if case["doi"] in dois else None
        ranks.append(rank)
        print(f"{case['doi']}: rank {rank or '-'}; {durations[-1]:.3f} s")
    print(f"Queries: {len(cases)}")
    print(f"Hit@1: {sum(rank == 1 for rank in ranks)}/{len(ranks)}")
    print(f"Hit@5: {sum(rank is not None for rank in ranks)}/{len(ranks)}")
    print(f"Median latency: {statistics.median(durations):.3f} s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=ROOT / "tmp/tgtu_citation_index.sqlite3")
    parser.add_argument("--cases", type=Path, default=ROOT / "data/tgtu_corpus/search_eval.jsonl")
    run(parser.parse_args())
