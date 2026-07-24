import hashlib
import json
import math
import re
import struct
import urllib.error
import urllib.request
import zlib

from django.conf import settings


TOKEN_RE = re.compile(r"[0-9a-zа-яё][0-9a-zа-яё_-]*", re.IGNORECASE)
HASH_DIMENSIONS = 384
RUSSIAN_SUFFIXES = (
    "иями", "ями", "ами", "ого", "ему", "ому", "ими", "ыми", "иях",
    "ах", "ях", "ов", "ев", "ий", "ый", "ой", "ая", "яя", "ое", "ее", "ые", "ие",
    "ить", "ать", "ять", "ение", "ания", "ения", "ию", "ию", "ия",
    "ам", "ям", "ом", "ем", "ы", "и", "а", "я", "у", "ю", "е",
)


def _stem(token):
    normalized = token.casefold().replace("ё", "е")
    if len(normalized) < 5:
        return normalized
    for suffix in RUSSIAN_SUFFIXES:
        if normalized.endswith(suffix) and len(normalized) - len(suffix) >= 4:
            return normalized[: -len(suffix)]
    return normalized


def _features(text):
    tokens = [_stem(token) for token in TOKEN_RE.findall(text or "")]
    for token in tokens:
        yield f"w:{token}", 1.0
        if len(token) >= 6:
            for index in range(len(token) - 2):
                yield f"g:{token[index:index + 3]}", 0.18
    for left, right in zip(tokens, tokens[1:]):
        yield f"b:{left}:{right}", 0.45


def hashing_embedding(text, dimensions=HASH_DIMENSIONS):
    vector = [0.0] * dimensions
    for feature, weight in _features(text):
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        raw = int.from_bytes(digest, "little")
        index = raw % dimensions
        vector[index] += weight if raw & 1 else -weight
    norm = math.sqrt(sum(value * value for value in vector))
    if norm:
        vector = [value / norm for value in vector]
    return vector


def _remote_embeddings(texts):
    base_url = str(settings.CITATION_EMBEDDING_BASE_URL or "").rstrip("/")
    model = str(settings.CITATION_EMBEDDING_MODEL or "").strip()
    if not base_url or not model:
        raise ValueError("Локальная embedding-модель не настроена.")

    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if settings.CITATION_EMBEDDING_API_KEY:
        headers["Authorization"] = f"Bearer {settings.CITATION_EMBEDDING_API_KEY}"
    request = urllib.request.Request(
        f"{base_url}/embeddings",
        data=json.dumps(
            {"model": model, "input": texts, "encoding_format": "float"},
            ensure_ascii=False,
        ).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=settings.CITATION_LLM_TIMEOUT) as response:
        payload = json.loads(response.read().decode("utf-8"))
    ordered = sorted(payload.get("data") or [], key=lambda item: item.get("index", 0))
    vectors = [item.get("embedding") or [] for item in ordered]
    if len(vectors) != len(texts) or any(not vector for vector in vectors):
        raise ValueError("Embedding API вернул неполный набор векторов.")
    return vectors


def embed_texts(texts, *, prefer_remote=True):
    clean_texts = [str(text or "") for text in texts]
    if prefer_remote and settings.CITATION_EMBEDDING_MODEL:
        batch_size = max(1, settings.CITATION_EMBEDDING_BATCH_SIZE)
        vectors = []
        try:
            for start in range(0, len(clean_texts), batch_size):
                vectors.extend(_remote_embeddings(clean_texts[start : start + batch_size]))
            return vectors, f"local_api:{settings.CITATION_EMBEDDING_MODEL}"
        except (OSError, ValueError, KeyError, json.JSONDecodeError, urllib.error.URLError):
            pass
    return [hashing_embedding(text) for text in clean_texts], "local_hashing_v1"


def encode_vector(vector):
    if not vector:
        return b""
    packed = struct.pack(f"<{len(vector)}f", *vector)
    return zlib.compress(packed, level=6)


def decode_vector(value):
    if not value:
        return []
    unpacked = zlib.decompress(value)
    return list(struct.unpack(f"<{len(unpacked) // 4}f", unpacked))


def cosine_similarity(left, right):
    if not left or not right or len(left) != len(right):
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return max(-1.0, min(1.0, numerator / (left_norm * right_norm)))
