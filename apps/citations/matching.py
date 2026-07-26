import re
from difflib import SequenceMatcher


DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.I)
WORD_RE = re.compile(r"[0-9A-ZА-ЯЁ]+", re.I)


def _normalize(value):
    return " ".join(
        token.casefold().replace("ё", "е")
        for token in WORD_RE.findall(str(value or ""))
    )


def _metadata_value(snapshot, field):
    metadata = (snapshot or {}).get("metadata") or {}
    value = metadata.get(field)
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value)
    return str(value or "")


def build_source_identity(snapshot=None, *, source_title="", source_authors=""):
    snapshot = snapshot or {}
    titles = {
        normalized
        for normalized in (
            _normalize(source_title),
            _normalize(_metadata_value(snapshot, "title")),
        )
        if len(normalized) >= 18
    }
    authors = _normalize(source_authors or _metadata_value(snapshot, "document_authors"))
    front_matter = "\n".join(
        str(item.get("text") or "")
        for item in (snapshot.get("paragraphs") or [])[:24]
    )
    dois = {
        match.group(0).casefold().rstrip(".,;")
        for match in DOI_RE.finditer(front_matter)
    }
    return {"titles": titles, "authors": authors, "dois": dois}


def is_same_article(article, identity):
    candidate_doi = str(article.get("doi") or "").casefold().rstrip(".,;")
    if candidate_doi and candidate_doi in identity.get("dois", set()):
        return True

    candidate_title = _normalize(article.get("title"))
    if len(candidate_title) < 18:
        return False
    for source_title in identity.get("titles", set()):
        if candidate_title == source_title:
            return True
        shorter, longer = sorted((candidate_title, source_title), key=len)
        if len(shorter) >= 28 and shorter in longer and len(shorter) / len(longer) >= 0.9:
            return True
        if (
            min(len(candidate_title), len(source_title)) >= 32
            and SequenceMatcher(None, candidate_title, source_title).ratio() >= 0.95
        ):
            return True
    return False


def remove_source_article(claims, identity):
    removed = 0
    for claim in claims:
        recommendations = claim.get("recommendations") or []
        filtered = [
            article
            for article in recommendations
            if not is_same_article(article, identity)
        ]
        removed += len(recommendations) - len(filtered)
        claim["recommendations"] = filtered
    return removed


def claims_with_recommendations(claims):
    return [
        claim
        for claim in claims
        if claim.get("recommendations")
    ]
