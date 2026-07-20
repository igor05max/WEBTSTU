import re

from django.db.models import Q

from apps.directory.models import Journal


ISSN_TOKEN_RE = re.compile(r"[^0-9Xx]+")
ISSN_CANDIDATE_RE = re.compile(r"(?<!\d)(\d{4})[-\s]?(\d{3}[0-9Xx])(?!\d)")
SPACE_RE = re.compile(r"\s+")


def normalize_space(value):
    return SPACE_RE.sub(" ", str(value or "").strip())


def normalize_issn(value):
    return ISSN_TOKEN_RE.sub("", str(value or "")).upper()


def extract_issn_candidates(value):
    candidates = []
    seen = set()
    for match in ISSN_CANDIDATE_RE.finditer(str(value or "")):
        candidate = normalize_issn("".join(match.groups()))
        if candidate and candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)
    return candidates


def normalize_titles(raw_titles):
    titles = []
    seen = set()
    for raw_title in raw_titles or []:
        title = normalize_space(raw_title)
        title_key = title.casefold()
        if not title or title_key in seen:
            continue
        seen.add(title_key)
        titles.append(title)
    return titles


def normalize_issns(raw_issns):
    issns = []
    seen = set()
    for raw_issn in raw_issns or []:
        issn = normalize_space(raw_issn).upper()
        issn_key = normalize_issn(issn)
        if not issn or not issn_key or issn_key in seen:
            continue
        seen.add(issn_key)
        issns.append(issn)
    return issns


def build_journal_search_index(titles, issns):
    tokens = []
    for title in normalize_titles(titles):
        tokens.append(title)
        tokens.append(title.casefold())
    for issn in normalize_issns(issns):
        tokens.append(issn)
        tokens.append(normalize_issn(issn))
    return "\n" + "\n".join(token for token in tokens if token) + "\n"


def search_journals(query, *, limit=20, active_only=True):
    query = normalize_space(query)
    if len(query) < 2:
        return Journal.objects.none()

    compact_issn = normalize_issn(query)
    filters = Q(name__icontains=query) | Q(issn__icontains=query) | Q(search_index__icontains=query)
    if compact_issn and len(compact_issn) >= 4 and compact_issn != query:
        filters |= Q(search_index__icontains=compact_issn)
    for candidate in extract_issn_candidates(query):
        filters |= Q(search_index__icontains=candidate)

    queryset = Journal.objects.filter(filters)
    if active_only:
        queryset = queryset.filter(is_active=True)
    return queryset.order_by("name")[:limit]


def resolve_journal_query(query):
    query = normalize_space(query)
    if not query:
        return None

    queryset = Journal.objects.filter(is_active=True)
    issn_candidates = extract_issn_candidates(query)
    compact_issn = normalize_issn(query)
    if len(compact_issn) >= 8:
        issn_candidates.append(compact_issn)
    for compact_issn in issn_candidates:
        journal = queryset.filter(search_index__icontains=f"\n{compact_issn}\n").order_by("name").first()
        if journal is not None:
            return journal

    journal = queryset.filter(name__iexact=query).first()
    if journal is not None:
        return journal

    matches = list(search_journals(query, limit=2))
    if len(matches) == 1:
        return matches[0]
    return None
