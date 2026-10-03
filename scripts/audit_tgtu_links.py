"""Audit public citation links in the TGTU Drive corpus.

Reads only the metadata manifest. It does not download or index the Drive PDFs.
Run from the repository root: python scripts/audit_tgtu_links.py
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data/tgtu_corpus/manifest.jsonl"
OUTPUT = ROOT / "data/tgtu_corpus/link_audit.jsonl"
AGENT = "TGTU-corpus-link-audit/1.0 (metadata and short HTTP probes)"
CROSSREF_URL = (
    "https://api.crossref.org/works?"
    "filter=prefix:10.17277,from-pub-date:2022-01-01,until-pub-date:2026-12-31"
    "&rows=1000&select=DOI,title,published,page,container-title"
)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def request(url: str, timeout: float, *, method: str = "GET", range_bytes=False, no_redirect=False):
    headers = {"User-Agent": AGENT}
    if range_bytes:
        headers["Range"] = "bytes=0-255"
    opener = urllib.request.build_opener(NoRedirect) if no_redirect else urllib.request.build_opener()
    req = urllib.request.Request(url, headers=headers, method=method)
    try:
        with opener.open(req, timeout=timeout) as response:
            return response.status, response.geturl(), {key.lower(): value for key, value in response.headers.items()}, response.read(256 if range_bytes else 2_000_000)
    except urllib.error.HTTPError as error:
        return error.code, url, {key.lower(): value for key, value in error.headers.items()}, b""
    except (OSError, TimeoutError, ValueError) as error:
        return None, url, {}, type(error).__name__ + ": " + str(error)[:160]


def crossref_index(timeout: float) -> dict:
    status, _, _, body = request(CROSSREF_URL, timeout)
    if status != 200 or not isinstance(body, bytes):
        raise RuntimeError(f"Crossref collection unavailable: HTTP {status}; {body!r}")
    message = json.loads(body)["message"]
    if message["total-results"] > len(message["items"]):
        raise RuntimeError("Crossref collection was truncated; increase rows or paginate")
    return {item["DOI"].casefold(): item for item in message["items"]}


def normalized_pages(value: str) -> str:
    values = re.findall(r"\d+", value or "")
    return "-".join(str(int(part)) for part in values)


def metadata_check(record: dict, crossref: dict) -> dict:
    item = crossref.get(record["doi"].casefold()) if record["doi"] else None
    if item is None:
        return {"registered": False, "year_match": None, "pages_match": None, "registry_title": ""}
    published = item.get("published", {}).get("date-parts", [[None]])[0][0]
    return {
        "registered": True,
        "year_match": published == record["year"],
        "pages_match": normalized_pages(item.get("page", "")) == normalized_pages(record["pages"]),
        "registry_title": (item.get("title") or [""])[0],
    }


def doi_probe(record: dict, timeout: float) -> dict:
    if not record["doi"]:
        return {"http": None, "location": "", "error": "no_doi"}
    status, _, headers, body = request(record["reference_url"], timeout, method="HEAD", no_redirect=True)
    if status == 405:
        status, _, headers, body = request(record["reference_url"], timeout, range_bytes=True, no_redirect=True)
    return {
        "http": status,
        "location": headers.get("location", ""),
        "error": body if isinstance(body, str) else "",
    }


def pdf_probe(url: str, timeout: float) -> dict:
    status, final, headers, body = request(url, timeout, range_bytes=True)
    return {
        "http": status,
        "final_url": final if status is not None else "",
        "pdf_magic": isinstance(body, bytes) and body.startswith(b"%PDF-"),
        "error": body if isinstance(body, str) else "",
    }


def publisher_probe(url: str, timeout: float) -> dict:
    status, final, headers, body = request(url, timeout)
    if not isinstance(body, bytes):
        return {"http": status, "final_url": final, "html": "", "error": body}
    content_type = headers.get("content-type", "").lower()
    if "html" not in content_type:
        return {"http": status, "final_url": final, "html": "", "error": "not_html"}
    match = re.search(r"charset\s*=\s*([\w-]+)", content_type)
    charset = match.group(1) if match else "utf-8"
    try:
        html = body.decode(charset, errors="replace").casefold()
    except LookupError:
        html = body.decode("utf-8", errors="replace").casefold()
    return {"http": status, "final_url": final, "html": html, "error": ""}


def equivalent_url(a: str, b: str) -> bool:
    def canonical(url):
        parts = urllib.parse.urlsplit(url)
        return parts.netloc.casefold(), parts.path, parts.query

    return bool(a and b and canonical(a) == canonical(b))


def classify(record: dict, registry: dict, doi: dict, pdf: dict, publisher: dict, article_specific: bool) -> tuple[str, str, str]:
    if record["decision"] == "exclude":
        return "excluded", "", record["decision_reason"]
    if registry["registered"] and (registry["year_match"] is False or registry["pages_match"] is False):
        return "mismatch", "", "crossref_year_or_pages_mismatch"
    doi_resolves = doi["http"] in (301, 302, 303, 307, 308)
    if registry["registered"] and doi_resolves and pdf["pdf_magic"] and equivalent_url(doi["location"], record["pdf_source_url"]):
        return "verified", record["reference_url"], "doi_redirects_to_verified_pdf"
    if publisher["http"] == 200 and publisher["contains_doi"] and article_specific:
        status = "verified" if registry["registered"] and doi_resolves else "verified_alternate"
        return status, record["article_url"], "article_page_names_doi"
    if publisher["http"] == 200 and publisher["contains_doi"] and publisher["contains_pdf_ref"] and pdf["pdf_magic"]:
        status = "verified" if registry["registered"] and doi_resolves else "verified_alternate"
        return status, record["pdf_source_url"], "issue_page_links_article_pdf"
    if not registry["registered"]:
        return "broken", "", "doi_absent_from_crossref_and_no_verified_alternate"
    if doi["http"] == 404:
        return "broken", "", "doi_resolver_404_and_no_verified_alternate"
    if pdf["pdf_magic"]:
        return "needs_identity_check", "", "pdf_reachable_but_article_identity_unconfirmed"
    return "unverified", "", "publisher_or_pdf_unavailable"


def run(args):
    records = [json.loads(line) for line in args.manifest.open(encoding="utf-8") if line.strip()]
    page_counts = __import__("collections").Counter(record["article_url"] for record in records)
    if args.reclassify or args.retry_doi:
        audited = [json.loads(line) for line in args.output.open(encoding="utf-8") if line.strip()]
        by_id = {record["record_id"]: record for record in records}
        if args.retry_doi:
            retry = [
                item for item in audited
                if by_id[item["record_id"]]["decision"] == "candidate"
                and (item["doi_link"]["http"] is None or (
                    item["doi_link"]["http"] in (301, 302, 303, 307, 308)
                    and not item["doi_link"]["location"]
                ))
            ]
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
                results = list(pool.map(lambda item: doi_probe(by_id[item["record_id"]], args.timeout), retry))
            for item, result in zip(retry, results):
                if result["http"] is not None:
                    item["doi_link"] = result
            print(f"Retried {len(retry)} DOI redirects", flush=True)
        for item in audited:
            record = by_id[item["record_id"]]
            item["status"], item["recommended_url"], item["reason"] = classify(
                record, item["crossref"], item["doi_link"], item["pdf_link"],
                item["publisher_page"], page_counts[record["article_url"]] == 1,
            )
        write_results(args.output, audited)
        return
    crossref = crossref_index(args.timeout * 3)
    print(f"Loaded {len(records)} manifest records and {len(crossref)} Crossref works", flush=True)
    candidates = [record for record in records if record["decision"] == "candidate"]
    pages = sorted({record["article_url"] for record in candidates})
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        doi_results = list(pool.map(lambda record: doi_probe(record, args.timeout), candidates))
    print(f"Probed {len(doi_results)} DOI links", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        pdf_results = list(pool.map(lambda record: pdf_probe(record["pdf_source_url"], args.timeout), candidates))
    print(f"Probed {len(pdf_results)} publisher PDFs", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        page_results = list(pool.map(lambda url: publisher_probe(url, args.timeout), pages))
    page_index = dict(zip(pages, page_results))
    print(f"Probed {len(pages)} publisher pages", flush=True)
    doi_index = dict(zip((r["record_id"] for r in candidates), doi_results))
    pdf_index = dict(zip((r["record_id"] for r in candidates), pdf_results))
    audited = []
    for record in records:
        rid = record["record_id"]
        registry = metadata_check(record, crossref)
        doi = doi_index.get(rid, {"http": None, "location": "", "error": "excluded"})
        pdf = pdf_index.get(rid, {"http": None, "pdf_magic": False, "error": "excluded"})
        page = page_index.get(record["article_url"], {"http": None, "html": "", "error": "excluded"})
        html = page["html"]
        publisher = {
            "http": page["http"],
            "contains_doi": bool(record["doi"] and record["doi"].casefold() in html),
            "contains_pdf_ref": bool(record["pdf_source_url"] and (
                record["pdf_source_url"].casefold() in html
                or urllib.parse.urlsplit(record["pdf_source_url"]).path.split("/")[-1].casefold() in html
            )),
            "error": page["error"],
        }
        status, recommended_url, reason = classify(
            record, registry, doi, pdf, publisher, page_counts[record["article_url"]] == 1,
        )
        audited.append({
            "record_id": rid,
            "doi": record["doi"],
            "status": status,
            "recommended_url": recommended_url,
            "reason": reason,
            "crossref": registry,
            "doi_link": doi,
            "pdf_link": pdf,
            "publisher_page": publisher,
        })
    write_results(args.output, audited)


def write_results(output: Path, audited: list[dict]):
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        for result in audited:
            handle.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
    from collections import Counter

    print("Audit date (UTC):", dt.datetime.now(dt.timezone.utc).isoformat(), flush=True)
    print("Statuses:", dict(Counter(item["status"] for item in audited)), flush=True)
    print("Results:", output, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--timeout", type=float, default=5)
    parser.add_argument("--reclassify", action="store_true", help="Apply current decision rules to an existing audit without HTTP requests")
    parser.add_argument("--retry-doi", action="store_true", help="Recheck DOI redirects with empty Location in an existing audit")
    run(parser.parse_args())
