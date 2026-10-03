import csv
import hashlib
import html
import json
import math
import os
import re
import sqlite3
import tempfile
import time
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

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


def _read_pdf_bytes(data):
    reader = PdfReader(BytesIO(data))
    pages = [_normalize(page.extract_text() or "") for page in reader.pages]
    return "\n".join(page for page in pages if page), len(reader.pages)


def _abstract_from_text(text):
    match = re.search(
        r"(?:Аннотация|Abstract)\s*[:.]?\s*(.{80,2500}?)"
        r"(?=\b(?:Ключевые\s+слова|Keywords|Введение|Introduction)\b)",
        text[:8000],
        flags=re.IGNORECASE | re.DOTALL,
    )
    return _normalize(match.group(1)) if match else ""


def _archive_pdf_members(archive_root):
    members = {}
    for archive_path in sorted(Path(archive_root).glob("*.zip")):
        with ZipFile(archive_path) as archive:
            for name in archive.namelist():
                if not name.casefold().endswith(".pdf") or "/" not in name:
                    continue
                relative = name.split("/", 1)[1]
                if relative in members:
                    raise ValueError(f"PDF встречается в нескольких архивах: {relative}")
                members[relative] = (archive_path, name)
    return members


def _iter_tgtu_articles(manifest_path, archive_root):
    manifest_path = Path(manifest_path)
    archive_root = Path(archive_root)
    if not manifest_path.exists():
        raise FileNotFoundError(f"Манифест корпуса не найден: {manifest_path}")
    if not archive_root.exists():
        raise FileNotFoundError(f"Архивы корпуса не найдены: {archive_root}")
    members = _archive_pdf_members(archive_root)
    legacy_abstracts = {}
    abstracts_path = manifest_path.parent / "legacy_abstracts.jsonl"
    if abstracts_path.exists():
        with abstracts_path.open(encoding="utf-8") as source:
            for line in source:
                if line.strip():
                    item = json.loads(line)
                    if item["record_id"] in legacy_abstracts:
                        raise ValueError(f"Повторяющиеся аннотации: {item['record_id']}")
                    legacy_abstracts[item["record_id"]] = item["abstracts"]
    seen_dois = set()
    with manifest_path.open(encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            record = json.loads(line)
            if record["decision"] != "candidate":
                continue
            doi = record["doi"].casefold()
            if doi in seen_dois:
                raise ValueError(f"Повторяющийся DOI в корпусе: {doi}")
            seen_dois.add(doi)
            pdf_path = record["pdf_path"]
            if pdf_path not in members:
                raise FileNotFoundError(f"PDF отсутствует в архивах: {pdf_path}")
            archive_path, member_name = members[pdf_path]
            with ZipFile(archive_path) as archive:
                data = archive.read(member_name)
            if hashlib.sha256(data).hexdigest() != record["pdf_sha256"]:
                raise ValueError(f"SHA-256 PDF не совпал: {pdf_path}")
            if len(data) != record["pdf_bytes"]:
                raise ValueError(f"Размер PDF не совпал: {pdf_path}")
            full_text, page_count = _read_pdf_bytes(data)
            if page_count != record["pdf_pages"]:
                raise ValueError(f"Число страниц PDF не совпало: {pdf_path}")
            if len(full_text) < 200:
                raise ValueError(f"Из PDF извлечено слишком мало текста: {pdf_path}")
            full_text = _trim_reference_section(full_text)
            abstract = _abstract_from_text(full_text)
            chunks = [
                ("catalog_abstract", _normalize(item["text"]))
                for item in legacy_abstracts.get(record["record_id"], [])
                if _normalize(item.get("text"))
            ]
            if abstract:
                chunks.append(("abstract", abstract))
            chunks.extend(("body", value) for value in _split_chunks(full_text))
            journal = record["journal"].replace("_", " ")
            citation = (
                f"{record['authors']} {record['title']} // {journal}. "
                f"— {record['year']}. — № {record['issue']}. — С. {record['pages']}."
            ).strip()
            article_id = record["record_id"]
            yield {
                "article_id": article_id,
                "title": record["title"],
                "authors": record["authors"],
                "year": str(record["year"]),
                "doi": record["doi"],
                "edn": "",
                "journal": journal,
                "issue": f"№ {record['issue']}",
                "pages": record["pages"],
                "section": "",
                "language": "ru",
                "url": f"/citations/sources/{article_id}/pdf/",
                "citation": citation,
                "keywords": "",
                "abstract": abstract,
                "pdf_path": "",
                "chunks": chunks,
                "archive_source": {
                    "archive_name": archive_path.name,
                    "member_name": member_name,
                    "sha256": record["pdf_sha256"],
                    "external_url": record.get("verified_source_url") or "",
                    "legacy_url": record.get("legacy_fallback_url") or "",
                    "link_status": record["link_verification"],
                },
            }
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
        CREATE TABLE article_vectors (
            article_id TEXT PRIMARY KEY REFERENCES articles(article_id),
            embedding BLOB NOT NULL
        );
        CREATE TABLE source_manifest (
            article_id TEXT PRIMARY KEY REFERENCES articles(article_id),
            archive_name TEXT NOT NULL,
            member_name TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            external_url TEXT NOT NULL,
            legacy_url TEXT NOT NULL,
            link_status TEXT NOT NULL
        );
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


def build_index(*, corpus_root=None, index_path=None, manifest_path=None, archive_root=None, progress=None):
    corpus_root = Path(corpus_root or settings.CITATION_CORPUS_ROOT)
    index_path = Path(index_path or settings.CITATION_INDEX_PATH)
    mode = "tgtu_archives" if manifest_path or archive_root or settings.CITATION_CORPUS_MODE == "tgtu_archives" else "legacy"
    if mode == "legacy" and not corpus_root.exists():
        raise FileNotFoundError(f"Корпус не найден: {corpus_root}")
    if mode == "tgtu_archives":
        manifest_path = Path(manifest_path or settings.CITATION_TGTU_MANIFEST)
        archive_root = Path(archive_root or settings.CITATION_ARCHIVE_ROOT)
        articles = _iter_tgtu_articles(manifest_path, archive_root)
    else:
        articles = _iter_articles(corpus_root)
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
        for article in articles:
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
            vectors, article_backend = embed_texts(embedding_inputs)
            if embedding_backend and article_backend != embedding_backend:
                raise ValueError("Embedding-модель изменилась во время построения индекса")
            embedding_backend = article_backend
            if vectors:
                dimensions = len(vectors[0])
                centroid = [0.0] * dimensions
                for vector in vectors[: min(len(vectors), 4)]:
                    if len(vector) != dimensions:
                        raise ValueError("Размерность векторов статьи не совпадает")
                    for index, value in enumerate(vector):
                        centroid[index] += value
                norm = math.sqrt(sum(value * value for value in centroid))
                if norm:
                    centroid = [value / norm for value in centroid]
                connection.execute(
                    "INSERT INTO article_vectors(article_id, embedding) VALUES(?, ?)",
                    (article["article_id"], encode_vector(centroid)),
                )
            if article.get("archive_source"):
                source = article["archive_source"]
                connection.execute(
                    "INSERT INTO source_manifest(article_id, archive_name, member_name, sha256, external_url, legacy_url, link_status) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (article["article_id"], source["archive_name"], source["member_name"],
                     source["sha256"], source["external_url"], source["legacy_url"], source["link_status"]),
                )
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
            "schema_version": "3",
            "built_at": datetime.now(timezone.utc).isoformat(),
            "corpus_root": str((archive_root if mode == "tgtu_archives" else corpus_root).resolve()),
            "corpus_mode": mode,
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
        with closing(sqlite3.connect(path)) as connection:
            meta = dict(connection.execute("SELECT key, value FROM meta"))
            if meta.get("corpus_mode") == "tgtu_archives":
                archive_root = Path(settings.CITATION_ARCHIVE_ROOT)
                required = [row[0] for row in connection.execute(
                    "SELECT DISTINCT archive_name FROM source_manifest"
                )]
                missing = [name for name in required if not (archive_root / name).is_file()]
                if missing:
                    return {
                        "ready": False,
                        "index_path": str(path),
                        "message": f"Архивы источников недоступны ({len(missing)}).",
                        **meta,
                    }
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
    if settings.CITATION_CORPUS_MODE == "tgtu_archives" or status.get("corpus_mode") == "tgtu_archives":
        return {
            **status,
            "message": status.get("message") or "Расширенный индекс нужно собрать отдельной командой.",
        }
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
    # FTS5 bm25() returns better matches as *more negative* values. The old
    # inverse-absolute formula reversed that order and favored weaker hits.
    strengths = [max(0.0, -float(row["rank"])) for row in rows]
    strongest = max(strengths, default=0.0)
    return {
        row["chunk_id"]: strength / strongest if strongest else 0.0
        for row, strength in zip(rows, strengths)
    }


def _semantic_article_candidates(connection, query_vector, limit):
    ranked = []
    for article_id, encoded in connection.execute(
        "SELECT article_id, embedding FROM article_vectors"
    ):
        similarity = cosine_similarity(query_vector, decode_vector(encoded))
        ranked.append((similarity, article_id))
    ranked.sort(reverse=True)
    return {article_id: score for score, article_id in ranked[:limit] if score > 0}


def _candidate_rows(connection, lexical_ids, article_ids):
    chunk_ids = list(lexical_ids)
    article_ids = list(article_ids)
    if not chunk_ids and not article_ids:
        return []
    terms = []
    values = []
    if chunk_ids:
        terms.append(f"c.id IN ({','.join('?' for _ in chunk_ids)})")
        values.extend(chunk_ids)
    if article_ids:
        terms.append(f"c.article_id IN ({','.join('?' for _ in article_ids)})")
        values.extend(article_ids)
    return connection.execute(
        """
        SELECT c.id AS chunk_id, c.article_id, c.kind, c.text, c.embedding,
               a.title, a.authors, a.year, a.doi, a.edn, a.journal, a.issue,
               a.pages, a.section, a.language, a.url, a.citation, a.keywords, a.abstract
        FROM chunks c
        JOIN articles a ON a.article_id = c.article_id
        WHERE """ + " OR ".join(terms),
        values,
    ).fetchall()


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
            f"Источник подходит для подтверждения фрагмента про {target}. "
            "Содержательная связь основана на общих ключевых понятиях: "
            + ", ".join(matched_terms[:5])
            + "; они встречаются и в утверждении вашей статьи, и в найденном "
            "фрагменте источника."
        )
    if semantic_score >= 0.55:
        return (
            f"Источник подходит для подтверждения фрагмента про {target}: "
            "найденный текст семантически близок утверждению вашей статьи. "
            "Ниже показан конкретный фрагмент источника, по которому можно "
            "проверить эту связь перед добавлением ссылки."
        )
    return (
        f"Источник тематически связан с фрагментом про {target}, но прямота "
        "подтверждения ограничена. Сравните утверждение вашей статьи с "
        "приведённым ниже фрагментом источника перед добавлением ссылки."
    )


def search_claim(claim, *, limit=None, candidate_limit=None, index_path=None):
    status = get_index_status(index_path) if index_path else ensure_index()
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
    lexical = _fts_candidates(connection, query, candidate_limit * 3)
    query_vectors, query_backend = embed_texts([query])
    query_vector = query_vectors[0]
    index_backend = status.get("embedding_backend", "")

    same_embedding_space = query_backend == index_backend
    if status.get("schema_version") == "3":
        article_semantic = (
            _semantic_article_candidates(connection, query_vector, candidate_limit)
            if same_embedding_space else {}
        )
        rows = _candidate_rows(connection, lexical, article_semantic)
    else:
        article_semantic = {}
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
            0.38 * max(semantic, 0.0)
            + 0.26 * lexical_score
            + 0.18 * min(1.0, overlap * 2.3)
            + 0.10 * min(1.0, field_overlap * 2.0)
            + 0.08 * max(0.0, article_semantic.get(row["article_id"], 0.0))
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
