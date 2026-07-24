import csv
import html
import json
import os
import re
import sqlite3
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from pypdf import PdfReader

from apps.citations.embeddings import (
    cosine_similarity,
    decode_vector,
    embed_texts,
    encode_vector,
)


SPACE_RE = re.compile(r"\s+")
TAG_RE = re.compile(r"<[^>]+>")
TOKEN_RE = re.compile(r"[0-9a-zа-яё]{3,}", re.IGNORECASE)
FILE_PREFIX_RE = re.compile(r"^(?P<article_id>\d+)\s+-\s+")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z0-9])")
REFERENCE_HEADING_RE = re.compile(
    r"\b(?:СПИСОК\s+(?:ИСПОЛЬЗОВАННОЙ\s+)?ЛИТЕРАТУРЫ|"
    r"БИБЛИОГРАФИЧЕСКИЙ\s+СПИСОК|REFERENCES)\b",
    re.IGNORECASE,
)
ABSTRACT_RE = re.compile(
    r'<div[^>]+id=["\'](?P<kind>abstract1|eabstract1)["\'][^>]*>(?P<text>.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)
KEYWORDS_RE = re.compile(
    r"(?:Ключевые\s+слова|Keywords)\s*:?\s*(?P<text>.*?)(?:</p>|</div>)",
    re.IGNORECASE | re.DOTALL,
)


def _clean_html(value):
    return SPACE_RE.sub(" ", html.unescape(TAG_RE.sub(" ", value or ""))).strip()


def _normalize(value):
    return SPACE_RE.sub(" ", (value or "").replace("\x00", " ")).strip()


def _tokens(value):
    seen = set()
    result = []
    for token in TOKEN_RE.findall((value or "").casefold().replace("ё", "е")):
        if token not in seen:
            seen.add(token)
            result.append(token)
    return result


def _build_local_file_maps(root):
    html_map = {}
    pdf_map = {}
    for path in Path(root).rglob("*"):
        if not path.is_file() or path.suffix.casefold() not in {".html", ".pdf"}:
            continue
        match = FILE_PREFIX_RE.match(path.stem)
        if not match:
            continue
        target = html_map if path.suffix.casefold() == ".html" else pdf_map
        target.setdefault(match.group("article_id"), path)
    return html_map, pdf_map


def _read_html_metadata(path):
    if not path or not path.exists():
        return {"abstract": "", "english_abstract": "", "keywords": ""}
    content = path.read_text(encoding="utf-8", errors="ignore")
    values = {"abstract": "", "english_abstract": "", "keywords": ""}
    for match in ABSTRACT_RE.finditer(content):
        text = _clean_html(match.group("text"))
        if match.group("kind").casefold() == "eabstract1":
            values["english_abstract"] = values["english_abstract"] or text
        else:
            values["abstract"] = values["abstract"] or text
    keyword_match = KEYWORDS_RE.search(content)
    if keyword_match:
        values["keywords"] = _clean_html(keyword_match.group("text"))
    return values


def _read_pdf_text(path):
    if not path or not path.exists():
        return ""
    try:
        reader = PdfReader(str(path))
        pages = []
        for page in reader.pages:
            text = _normalize(page.extract_text() or "")
            if text:
                pages.append(text)
        return "\n".join(pages)
    except Exception:
        return ""


def _trim_reference_section(text):
    candidates = [
        match.start()
        for match in REFERENCE_HEADING_RE.finditer(text or "")
        if match.start() >= len(text) * 0.35
    ]
    if not candidates:
        return text
    return text[: min(candidates)].rstrip()


def _split_chunks(text, *, max_chars=1250, overlap_sentences=1):
    paragraphs = [_normalize(item) for item in re.split(r"[\r\n]+", text or "")]
    sentences = []
    for paragraph in paragraphs:
        if len(paragraph) < 40:
            continue
        sentences.extend(item.strip() for item in SENTENCE_RE.split(paragraph) if item.strip())
    if not sentences and text:
        sentences = [_normalize(text)]

    chunks = []
    current = []
    current_length = 0
    for sentence in sentences:
        if current and current_length + len(sentence) + 1 > max_chars:
            chunks.append(" ".join(current))
            current = current[-overlap_sentences:] if overlap_sentences else []
            current_length = sum(len(item) + 1 for item in current)
        current.append(sentence)
        current_length += len(sentence) + 1
    if current:
        chunks.append(" ".join(current))
    return [chunk for chunk in chunks if len(chunk) >= 60]


def _metadata_csv(root):
    master = Path(root) / "journal_articles_full_metadata.csv"
    if master.exists():
        return master
    candidates = sorted(Path(root).rglob("articles_metadata.csv"))
    return candidates[0] if candidates else None


def _iter_articles(root):
    metadata_path = _metadata_csv(root)
    if metadata_path is None:
        return
    html_map, pdf_map = _build_local_file_maps(root)
    with metadata_path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source, delimiter=";")
        for row in reader:
            article_id = (row.get("article_id") or "").strip()
            title = _normalize(row.get("title") or "")
            if not article_id or not title:
                continue
            html_path = html_map.get(article_id)
            pdf_path = pdf_map.get(article_id)
            parsed = _read_html_metadata(html_path)
            abstract = parsed["abstract"]
            english_abstract = parsed["english_abstract"]
            keywords = parsed["keywords"]
            full_text = _trim_reference_section(_read_pdf_text(pdf_path))
            chunks = []
            if abstract:
                chunks.append(("abstract", abstract))
            if english_abstract and english_abstract.casefold() != abstract.casefold():
                chunks.append(("english_abstract", english_abstract))
            chunks.extend(("body", value) for value in _split_chunks(full_text))
            if not chunks:
                chunks.append(("metadata", " ".join(filter(None, [title, keywords, row.get("section")]))))
            yield {
                "article_id": article_id,
                "title": title,
                "authors": _normalize(row.get("authors") or ""),
                "year": _normalize(row.get("article_year") or row.get("year") or ""),
                "doi": _normalize(row.get("doi") or ""),
                "edn": _normalize(row.get("edn") or ""),
                "journal": _normalize(row.get("journal") or ""),
                "issue": _normalize(
                    row.get("issue_display_name") or row.get("issue") or row.get("issue_name") or ""
                ),
                "pages": _normalize(row.get("pages") or ""),
                "section": _normalize(row.get("section") or ""),
                "language": _normalize(row.get("language") or ""),
                "url": _normalize(row.get("article_url") or row.get("url") or ""),
                "citation": _normalize(row.get("citation_elibrary") or ""),
                "keywords": keywords,
                "abstract": abstract or english_abstract,
                "pdf_path": str(pdf_path or ""),
                "chunks": chunks,
            }


def _schema(connection):
    connection.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE articles (
            article_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            authors TEXT NOT NULL,
            year TEXT NOT NULL,
            doi TEXT NOT NULL,
            edn TEXT NOT NULL,
            journal TEXT NOT NULL,
            issue TEXT NOT NULL,
            pages TEXT NOT NULL,
            section TEXT NOT NULL,
            language TEXT NOT NULL,
            url TEXT NOT NULL,
            citation TEXT NOT NULL,
            keywords TEXT NOT NULL,
            abstract TEXT NOT NULL,
            pdf_path TEXT NOT NULL
        );
        CREATE TABLE chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id TEXT NOT NULL REFERENCES articles(article_id),
            position INTEGER NOT NULL,
            kind TEXT NOT NULL,
            text TEXT NOT NULL,
            embedding BLOB NOT NULL
        );
        CREATE INDEX chunks_article_idx ON chunks(article_id);
        CREATE VIRTUAL TABLE chunk_fts USING fts5(
            chunk_id UNINDEXED,
            text,
            title,
            keywords,
            section,
            tokenize='unicode61 remove_diacritics 2'
        );
        """
    )


def build_index(*, corpus_root=None, index_path=None, progress=None):
    corpus_root = Path(corpus_root or settings.CITATION_CORPUS_ROOT)
    index_path = Path(index_path or settings.CITATION_INDEX_PATH)
    if not corpus_root.exists():
        raise FileNotFoundError(f"Корпус не найден: {corpus_root}")
    index_path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        prefix=f"{index_path.stem}-",
        suffix=".sqlite3",
        dir=index_path.parent,
    )
    os.close(handle)
    temp_path = Path(temp_name)
    article_count = 0
    chunk_count = 0
    embedding_backend = ""
    try:
        connection = sqlite3.connect(temp_path)
        _schema(connection)
        for article in _iter_articles(corpus_root):
            article_count += 1
            fields = (
                "article_id", "title", "authors", "year", "doi", "edn", "journal",
                "issue", "pages", "section", "language", "url", "citation", "keywords",
                "abstract", "pdf_path",
            )
            connection.execute(
                f"INSERT INTO articles ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",
                tuple(article[field] for field in fields),
            )
            embedding_inputs = [
                "\n".join(
                    filter(
                        None,
                        [
                            article["title"],
                            article["section"],
                            article["keywords"],
                            chunk_text,
                        ],
                    )
                )
                for _kind, chunk_text in article["chunks"]
            ]
            vectors, embedding_backend = embed_texts(embedding_inputs)
            for position, ((kind, chunk_text), vector) in enumerate(
                zip(article["chunks"], vectors)
            ):
                cursor = connection.execute(
                    "INSERT INTO chunks(article_id, position, kind, text, embedding) VALUES(?,?,?,?,?)",
                    (
                        article["article_id"],
                        position,
                        kind,
                        chunk_text,
                        encode_vector(vector),
                    ),
                )
                connection.execute(
                    "INSERT INTO chunk_fts(chunk_id, text, title, keywords, section) VALUES(?,?,?,?,?)",
                    (
                        cursor.lastrowid,
                        chunk_text,
                        article["title"],
                        article["keywords"],
                        article["section"],
                    ),
                )
                chunk_count += 1
            if article_count % 10 == 0:
                connection.commit()
                if progress:
                    progress(article_count, chunk_count)
        metadata = {
            "schema_version": "2",
            "built_at": datetime.now(timezone.utc).isoformat(),
            "corpus_root": str(corpus_root.resolve()),
            "article_count": str(article_count),
            "chunk_count": str(chunk_count),
            "embedding_backend": embedding_backend or "local_hashing_v1",
        }
        connection.executemany("INSERT INTO meta(key, value) VALUES(?, ?)", metadata.items())
        connection.commit()
        connection.close()
        os.replace(temp_path, index_path)
        return {**metadata, "index_path": str(index_path)}
    except Exception:
        try:
            connection.close()
        except Exception:
            pass
        temp_path.unlink(missing_ok=True)
        raise


def get_index_status(index_path=None):
    path = Path(index_path or settings.CITATION_INDEX_PATH)
    if not path.exists():
        return {"ready": False, "index_path": str(path), "message": "Индекс ещё не создан."}
    try:
        connection = sqlite3.connect(path)
        meta = dict(connection.execute("SELECT key, value FROM meta"))
        connection.close()
        return {
            "ready": True,
            "index_path": str(path),
            "message": "Индекс готов.",
            **meta,
        }
    except sqlite3.Error as exc:
        return {
            "ready": False,
            "index_path": str(path),
            "message": f"Индекс повреждён: {exc}",
        }


def ensure_index():
    status = get_index_status()
    if status["ready"]:
        return status
    if not settings.CITATION_INDEX_AUTO_BUILD:
        return status
    index_path = Path(settings.CITATION_INDEX_PATH)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = index_path.with_suffix(index_path.suffix + ".lock")
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(descriptor)
    except FileExistsError:
        try:
            age_seconds = time.time() - lock_path.stat().st_mtime
        except OSError:
            age_seconds = 0
        if age_seconds > 60 * 60:
            lock_path.unlink(missing_ok=True)
            return ensure_index()
        return {
            **status,
            "message": "Индекс корпуса сейчас создаётся другим процессом. Повторите поиск позже.",
        }
    try:
        return {"ready": True, **build_index()}
    finally:
        lock_path.unlink(missing_ok=True)


def _fts_candidates(connection, query, limit):
    terms = _tokens(query)[:18]
    if not terms:
        return {}
    expression = " OR ".join(f'"{term}"' for term in terms)
    rows = connection.execute(
        """
        SELECT CAST(chunk_id AS INTEGER) AS chunk_id, bm25(chunk_fts, 1.0, 4.0, 2.5, 1.7) AS rank
        FROM chunk_fts
        WHERE chunk_fts MATCH ?
        ORDER BY rank
        LIMIT ?
        """,
        (expression, limit),
    ).fetchall()
    return {
        row["chunk_id"]: 1.0 / (1.0 + max(0.0, abs(float(row["rank"]))))
        for row in rows
    }


def _evidence_reason(claim_type, matched_terms, semantic_score):
    type_labels = {
        "topic": "контекст и предмет исследования",
        "method": "используемый метод",
        "data": "данные или экспериментальную базу",
        "task": "постановку научной задачи",
        "result": "заявленный результат или вывод",
    }
    target = type_labels.get(claim_type, "содержание утверждения")
    if matched_terms:
        return (
            f"Источник связывает {target} с терминами: "
            + ", ".join(matched_terms[:5])
            + "."
        )
    if semantic_score >= 0.55:
        return f"Смысловой фрагмент источника близок и может подтвердить {target}."
    return f"Источник тематически связан и требует ручной проверки для подтверждения: {target}."


def search_claim(claim, *, limit=None, candidate_limit=None, index_path=None):
    status = ensure_index()
    if not status.get("ready"):
        return []
    path = Path(index_path or settings.CITATION_INDEX_PATH)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    query = " ".join(
        filter(
            None,
            [
                claim.get("query_ru"),
                claim.get("query_en"),
                claim.get("text"),
            ],
        )
    )
    candidate_limit = candidate_limit or settings.CITATION_CANDIDATE_LIMIT
    lexical = _fts_candidates(connection, query, candidate_limit * 2)
    query_vectors, query_backend = embed_texts([query])
    query_vector = query_vectors[0]
    index_backend = status.get("embedding_backend", "")

    rows = connection.execute(
        """
        SELECT c.id AS chunk_id, c.article_id, c.kind, c.text, c.embedding,
               a.title, a.authors, a.year, a.doi, a.edn, a.journal, a.issue,
               a.pages, a.section, a.language, a.url, a.citation, a.keywords, a.abstract
        FROM chunks c
        JOIN articles a ON a.article_id = c.article_id
        """
    ).fetchall()
    query_terms = set(_tokens(query))
    candidates = defaultdict(list)
    same_embedding_space = query_backend == index_backend
    for row in rows:
        semantic = (
            cosine_similarity(query_vector, decode_vector(row["embedding"]))
            if same_embedding_space
            else 0.0
        )
        lexical_score = lexical.get(row["chunk_id"], 0.0)
        if lexical_score <= 0 and semantic < 0.18:
            continue
        evidence_terms = set(_tokens(row["text"]))
        title_terms = set(_tokens(row["title"] + " " + row["keywords"] + " " + row["section"]))
        overlap = len(query_terms & evidence_terms) / max(1, min(len(query_terms), 14))
        field_overlap = len(query_terms & title_terms) / max(1, min(len(query_terms), 10))
        score = (
            0.42 * max(semantic, 0.0)
            + 0.28 * lexical_score
            + 0.18 * min(1.0, overlap * 2.3)
            + 0.12 * min(1.0, field_overlap * 2.0)
        )
        candidates[row["article_id"]].append((score, semantic, lexical_score, overlap, row))

    results = []
    for article_rows in candidates.values():
        score, semantic, lexical_score, overlap, row = max(
            article_rows,
            key=lambda item: item[0],
        )
        matched_terms = sorted(
            query_terms & set(_tokens(row["text"] + " " + row["title"])),
            key=lambda item: (-len(item), item),
        )[:7]
        evidence = _normalize(row["text"])
        if len(evidence) > 720:
            evidence = evidence[:719].rstrip() + "…"
        citation = row["citation"] or (
            f"{row['authors']} {row['title']} // {row['journal']}. "
            f"— {row['year']}. — {row['issue']}. — С. {row['pages']}."
        ).strip()
        results.append(
            {
                "article_id": row["article_id"],
                "title": row["title"],
                "authors": row["authors"],
                "year": row["year"],
                "doi": row["doi"],
                "edn": row["edn"],
                "journal": row["journal"],
                "issue": row["issue"],
                "pages": row["pages"],
                "section": row["section"],
                "language": row["language"],
                "url": row["url"],
                "citation": citation,
                "evidence": evidence,
                "chunk_kind": row["kind"],
                "matched_terms": matched_terms,
                "hybrid_score": round(score, 4),
                "semantic_score": round(max(semantic, 0.0), 4),
                "lexical_score": round(lexical_score, 4),
                "score": round(score, 4),
                "score_percent": max(1, min(99, round(score * 100))),
                "verdict": "possible",
                "reason": _evidence_reason(claim.get("type"), matched_terms, semantic),
            }
        )
    connection.close()
    results.sort(key=lambda item: (-item["score"], item["title"]))
    return results[: (limit or settings.CITATION_SEARCH_LIMIT)]
