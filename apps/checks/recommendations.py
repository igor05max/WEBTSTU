import csv
import hashlib
import html
import json
import math
import re
from collections import Counter
from pathlib import Path

from django.conf import settings

WORD_RE = re.compile(r"[0-9a-zа-яё]+", re.IGNORECASE)
FILE_PREFIX_RE = re.compile(r"^(?P<article_id>\d+)\s+-\s+")
ABSTRACT_RE = re.compile(
    r'<div id="(?P<kind>abstract1|eabstract1)"[^>]*>(?P<content>.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)
KEYWORDS_RE = re.compile(
    r"(?:Keywords|Ключевые слова):(?P<content>.*?)</p>",
    re.IGNORECASE | re.DOTALL,
)
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "with",
    "без",
    "более",
    "бы",
    "в",
    "во",
    "все",
    "для",
    "до",
    "его",
    "ее",
    "же",
    "за",
    "и",
    "из",
    "или",
    "их",
    "к",
    "как",
    "на",
    "не",
    "нет",
    "но",
    "о",
    "об",
    "от",
    "по",
    "под",
    "при",
    "с",
    "со",
    "так",
    "также",
    "тем",
    "то",
    "у",
    "что",
    "это",
}


def _normalize_text(value):
    normalized = html.unescape(value or "").replace("ё", "е").casefold()
    return SPACE_RE.sub(" ", normalized).strip()


def _strip_html(value):
    return SPACE_RE.sub(" ", html.unescape(TAG_RE.sub(" ", value or ""))).strip()


def _tokenize(value):
    normalized = _normalize_text(value)
    return [
        token
        for token in WORD_RE.findall(normalized)
        if len(token) >= 2 and token not in STOPWORDS
    ]


def _collect_text_features(text, *, weight):
    features = Counter()
    token_counts = Counter()
    for token in _tokenize(text):
        token_counts[token] += 1
        features[f"w:{token}"] += 1.0 * weight
        if len(token) >= 5:
            for index in range(len(token) - 2):
                features[f"g:{token[index:index + 3]}"] += 0.2 * weight
    return features, token_counts


def _merge_feature_bundle(base_features, base_tokens, text, *, weight):
    features, token_counts = _collect_text_features(text, weight=weight)
    base_features.update(features)
    base_tokens.update(token_counts)


def _build_article_vector(article):
    features = Counter()
    token_counts = Counter()
    _merge_feature_bundle(features, token_counts, article["section"], weight=1.0)
    _merge_feature_bundle(features, token_counts, article["title"], weight=3.0)
    _merge_feature_bundle(features, token_counts, article["abstract"], weight=2.0)
    _merge_feature_bundle(features, token_counts, article["english_abstract"], weight=1.5)
    _merge_feature_bundle(features, token_counts, ", ".join(article["keywords"]), weight=2.0)
    return features, token_counts


def _build_query_vector(title, abstract):
    features = Counter()
    token_counts = Counter()
    _merge_feature_bundle(features, token_counts, title, weight=3.0)
    _merge_feature_bundle(features, token_counts, abstract, weight=2.0)
    return features, token_counts


def _build_issue_file_map(issue_root, suffix):
    result = {}
    for path in issue_root.rglob(f"*{suffix}"):
        match = FILE_PREFIX_RE.match(path.stem)
        if match:
            result[match.group("article_id")] = path
    return result


def _parse_article_html(html_path):
    if html_path is None or not html_path.exists():
        return {
            "abstract": "",
            "english_abstract": "",
            "keywords": [],
        }

    content = html_path.read_text(encoding="utf-8", errors="ignore")

    abstract = ""
    english_abstract = ""
    for match in ABSTRACT_RE.finditer(content):
        cleaned = _strip_html(match.group("content"))
        if not cleaned:
            continue
        if match.group("kind").lower() == "abstract1" and not abstract:
            abstract = cleaned
        elif match.group("kind").lower() == "eabstract1" and not english_abstract:
            english_abstract = cleaned

    keywords = []
    seen_keywords = set()
    for match in KEYWORDS_RE.finditer(content):
        cleaned = _strip_html(match.group("content"))
        if not cleaned:
            continue
        for raw_keyword in re.split(r"[,;]", cleaned):
            keyword = SPACE_RE.sub(" ", raw_keyword).strip()
            normalized_keyword = _normalize_text(keyword)
            if not keyword or normalized_keyword in seen_keywords:
                continue
            seen_keywords.add(normalized_keyword)
            keywords.append(keyword)

    return {
        "abstract": abstract,
        "english_abstract": english_abstract,
        "keywords": keywords,
    }


def _build_article_embedding_text(article):
    parts = [
        article["section"],
        article["title"],
        article["abstract"],
        article["english_abstract"],
    ]
    if article["keywords"]:
        parts.append("Ключевые слова: " + ", ".join(article["keywords"]))
    return "\n".join(part for part in parts if part).strip()


def _load_corpus(corpus_root):
    root = Path(corpus_root)
    if not root.exists():
        return []

    corpus = []
    for metadata_path in sorted(root.rglob("articles_metadata.csv")):
        issue_root = metadata_path.parent
        html_map = _build_issue_file_map(issue_root, ".html")
        pdf_map = _build_issue_file_map(issue_root, ".pdf")

        with metadata_path.open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source, delimiter=";")
            for row in reader:
                article_id = (row.get("article_id") or "").strip()
                title = (row.get("title") or "").strip()
                if not article_id or not title:
                    continue

                html_path = html_map.get(article_id)
                parsed = _parse_article_html(html_path)
                article = {
                    "article_id": article_id,
                    "issue": issue_root.name,
                    "section": (row.get("section") or "").strip(),
                    "title": title,
                    "authors": (row.get("authors") or "").strip(),
                    "pages": (row.get("pages") or "").strip(),
                    "url": (row.get("url") or "").strip(),
                    "abstract": parsed["abstract"],
                    "english_abstract": parsed["english_abstract"],
                    "keywords": parsed["keywords"],
                    "pdf_path": str(pdf_map.get(article_id) or ""),
                }
                article["features"], article["token_counts"] = _build_article_vector(article)
                article["embedding_text"] = _build_article_embedding_text(article)
                corpus.append(article)

    return corpus


def _build_document_frequency(corpus):
    document_frequency = Counter()
    for article in corpus:
        for feature_name in article["features"].keys():
            document_frequency[feature_name] += 1
    return document_frequency


def _apply_idf(features, document_frequency, document_count):
    weighted = Counter()
    for feature_name, count in features.items():
        df = document_frequency.get(feature_name, 0)
        idf = math.log((1 + document_count) / (1 + df)) + 1.0
        weighted[feature_name] = count * idf
    return weighted


def _vector_norm(vector):
    return math.sqrt(sum(value * value for value in vector.values()))


def _cosine_similarity_sparse(left, right):
    left_norm = _vector_norm(left)
    right_norm = _vector_norm(right)
    if left_norm == 0 or right_norm == 0:
        return 0.0

    if len(left) > len(right):
        left, right = right, left
    numerator = sum(value * right.get(feature_name, 0.0) for feature_name, value in left.items())
    if numerator <= 0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _cosine_similarity_dense(left, right):
    if not left or not right or len(left) != len(right):
        return 0.0

    numerator = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for left_value, right_value in zip(left, right):
        numerator += left_value * right_value
        left_norm += left_value * left_value
        right_norm += right_value * right_value

    if numerator <= 0 or left_norm <= 0 or right_norm <= 0:
        return 0.0
    return numerator / (math.sqrt(left_norm) * math.sqrt(right_norm))


def _top_shared_terms(query_tokens, article_tokens, limit=5):
    shared = []
    for term in query_tokens.keys() & article_tokens.keys():
        score = min(query_tokens[term], article_tokens[term])
        shared.append((score, term))
    shared.sort(key=lambda item: (-item[0], item[1]))
    return [term for _, term in shared[:limit]]


def _truncate_text(value, max_length=380):
    cleaned = SPACE_RE.sub(" ", (value or "").strip())
    if len(cleaned) <= max_length:
        return cleaned
    return cleaned[: max_length - 1].rstrip() + "…"


def _build_result_item(article, score, matched_terms):
    summary = article["abstract"] or article["english_abstract"]
    return {
        "article_id": article["article_id"],
        "title": article["title"],
        "authors": article["authors"],
        "section": article["section"],
        "issue": article["issue"],
        "pages": article["pages"],
        "url": article["url"],
        "score": round(score, 4),
        "score_percent": max(1, round(score * 100)),
        "matched_terms": matched_terms,
        "summary": _truncate_text(summary),
    }


def _empty_payload(message, source):
    return {
        "message": message,
        "recommendations": [],
        "source": source,
    }


def _load_embedding_cache(cache_path, *, corpus_root, model, dimensions):
    path = Path(cache_path)
    if not path.exists():
        return {"meta": {}, "items": {}}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"meta": {}, "items": {}}

    meta = payload.get("meta") or {}
    if (
        meta.get("corpus_root") != str(Path(corpus_root))
        or meta.get("model") != model
        or meta.get("dimensions") != dimensions
    ):
        return {"meta": {}, "items": {}}
    return {"meta": meta, "items": payload.get("items") or {}}


def _save_embedding_cache(cache_path, payload, *, corpus_root, model, dimensions):
    path = Path(cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = {
        "meta": {
            "corpus_root": str(Path(corpus_root)),
            "model": model,
            "dimensions": dimensions,
        },
        "items": payload.get("items") or {},
    }
    path.write_text(json.dumps(serialized, ensure_ascii=False), encoding="utf-8")


def _hash_text(value):
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _get_openai_client():
    if not settings.ARTICLE_RECOMMENDATION_USE_EMBEDDINGS:
        return None
    if not settings.OPENAI_API_KEY:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None
    return OpenAI(api_key=settings.OPENAI_API_KEY)


def _parse_dimensions_setting():
    raw_value = settings.ARTICLE_RECOMMENDATION_EMBEDDING_DIMENSIONS
    if not raw_value:
        return None
    try:
        return int(raw_value)
    except ValueError:
        return None


def _embed_texts(client, texts):
    if not texts:
        return []

    model = settings.ARTICLE_RECOMMENDATION_EMBEDDING_MODEL
    dimensions = _parse_dimensions_setting()
    batch_size = max(1, settings.ARTICLE_RECOMMENDATION_EMBEDDING_BATCH_SIZE)
    embeddings = []

    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        request_kwargs = {
            "input": batch,
            "model": model,
            "encoding_format": "float",
        }
        if dimensions is not None:
            request_kwargs["dimensions"] = dimensions
        response = client.embeddings.create(**request_kwargs)
        embeddings.extend(item.embedding for item in response.data)

    return embeddings


def _rank_with_embeddings(corpus, *, title, abstract, limit, corpus_root):
    client = _get_openai_client()
    if client is None:
        return None

    model = settings.ARTICLE_RECOMMENDATION_EMBEDDING_MODEL
    dimensions = _parse_dimensions_setting()
    cache_payload = _load_embedding_cache(
        settings.ARTICLE_RECOMMENDATION_EMBEDDING_CACHE_PATH,
        corpus_root=corpus_root,
        model=model,
        dimensions=dimensions,
    )
    cache_items = cache_payload["items"]

    missing_articles = []
    missing_texts = []
    for article in corpus:
        text_hash = _hash_text(article["embedding_text"])
        cached = cache_items.get(article["article_id"])
        if cached and cached.get("text_hash") == text_hash and cached.get("embedding"):
            article["embedding"] = cached["embedding"]
            continue
        missing_articles.append((article, text_hash))
        missing_texts.append(article["embedding_text"])

    if missing_texts:
        generated_embeddings = _embed_texts(client, missing_texts)
        for (article, text_hash), embedding in zip(missing_articles, generated_embeddings):
            article["embedding"] = embedding
            cache_items[article["article_id"]] = {
                "text_hash": text_hash,
                "embedding": embedding,
            }
        _save_embedding_cache(
            settings.ARTICLE_RECOMMENDATION_EMBEDDING_CACHE_PATH,
            cache_payload,
            corpus_root=corpus_root,
            model=model,
            dimensions=dimensions,
        )

    query_title = (title or "").strip()
    query_abstract = (abstract or "").strip()
    query_text = "\n".join(part for part in [query_title, query_abstract] if part).strip()
    query_embedding = _embed_texts(client, [query_text])[0]
    _, query_tokens = _build_query_vector(query_title, query_abstract)

    min_score = settings.ARTICLE_RECOMMENDATION_MIN_SCORE
    scored_recommendations = []
    for article in corpus:
        score = _cosine_similarity_dense(query_embedding, article.get("embedding") or [])
        matched_terms = _top_shared_terms(query_tokens, article["token_counts"])
        if score < min_score:
            continue
        if len(matched_terms) < 2 and score < (min_score + 0.05):
            continue
        scored_recommendations.append(_build_result_item(article, score, matched_terms))

    scored_recommendations.sort(key=lambda item: (-item["score"], item["title"]))
    selected = scored_recommendations[: limit or settings.ARTICLE_RECOMMENDATION_LIMIT]
    if not selected:
        return _empty_payload(
            "Сильные совпадения по эмбеддингам не найдены для текущей заявки.",
            f"openai_embeddings:{model}",
        )

    return {
        "message": f"Подбор выполнен по эмбеддингам OpenAI ({model}).",
        "recommendations": selected,
        "source": f"openai_embeddings:{model}",
    }


def _rank_with_local_semantics(corpus, *, title, abstract, limit):
    document_frequency = _build_document_frequency(corpus)
    query_features, query_tokens = _build_query_vector(title, abstract)
    query_vector = _apply_idf(query_features, document_frequency, len(corpus))
    min_score = settings.ARTICLE_RECOMMENDATION_MIN_SCORE

    scored_recommendations = []
    for article in corpus:
        article_vector = _apply_idf(article["features"], document_frequency, len(corpus))
        score = _cosine_similarity_sparse(query_vector, article_vector)
        matched_terms = _top_shared_terms(query_tokens, article["token_counts"])
        if score <= 0:
            continue
        if score < min_score:
            continue
        if len(matched_terms) < 2 and score < (min_score + 0.05):
            continue
        scored_recommendations.append(_build_result_item(article, score, matched_terms))

    scored_recommendations.sort(key=lambda item: (-item["score"], item["title"]))
    selected = scored_recommendations[: limit or settings.ARTICLE_RECOMMENDATION_LIMIT]
    if not selected:
        return _empty_payload(
            "Сильные совпадения не найдены по текущему названию и аннотации заявки.",
            "local_semantic_v1",
        )

    return {
        "message": "Подбор выполнен локальным текстовым фолбэком по названию, аннотации и ключевым словам корпуса.",
        "recommendations": selected,
        "source": "local_semantic_v1",
    }


def recommend_articles(*, title, abstract="", limit=None, corpus_root=None):
    query_title = (title or "").strip()
    query_abstract = (abstract or "").strip()
    if not query_title and not query_abstract:
        return _empty_payload(
            "Для подбора похожих статей нужно указать название или аннотацию.",
            "none",
        )

    resolved_corpus_root = corpus_root or settings.ARTICLE_RECOMMENDATION_CORPUS_ROOT
    corpus = _load_corpus(resolved_corpus_root)
    if not corpus:
        return _empty_payload(
            "Локальный корпус статей пока не найден. Добавьте материалы в каталог рекомендаций.",
            "none",
        )

    try:
        embedding_payload = _rank_with_embeddings(
            corpus,
            title=query_title,
            abstract=query_abstract,
            limit=limit,
            corpus_root=resolved_corpus_root,
        )
    except Exception:
        embedding_payload = None

    if embedding_payload is not None:
        return embedding_payload

    return _rank_with_local_semantics(
        corpus,
        title=query_title,
        abstract=query_abstract,
        limit=limit,
    )
