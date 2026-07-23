from difflib import SequenceMatcher

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.directory.models import PublicationTopic, normalize_catalog_name


RU_TO_LATIN = str.maketrans(
    {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "g",
        "д": "d",
        "е": "e",
        "ё": "e",
        "ж": "zh",
        "з": "z",
        "и": "i",
        "й": "i",
        "к": "k",
        "л": "l",
        "м": "m",
        "н": "n",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "у": "u",
        "ф": "f",
        "х": "h",
        "ц": "c",
        "ч": "ch",
        "ш": "sh",
        "щ": "sch",
        "ъ": "",
        "ы": "y",
        "ь": "",
        "э": "e",
        "ю": "yu",
        "я": "ya",
    }
)


def transliterate(value):
    return "".join(RU_TO_LATIN.get(ord(char), char) for char in normalize_catalog_name(value))


def _acronym(value):
    return "".join(token[0] for token in normalize_catalog_name(value).split() if token)


def _topic_score(topic, query):
    normalized_query = normalize_catalog_name(query)
    if not normalized_query:
        return 0.0

    values = [topic.name, *(topic.aliases or [])]
    best = 0.0
    query_tokens = set(normalized_query.split())
    query_translit = transliterate(normalized_query)
    for value in values:
        normalized_value = normalize_catalog_name(value)
        if not normalized_value:
            continue
        if normalized_value == normalized_query:
            return 1.0
        if normalized_query in normalized_value:
            best = max(best, 0.92)

        value_tokens = set(normalized_value.split())
        if query_tokens and value_tokens:
            overlap = len(query_tokens & value_tokens) / max(len(query_tokens), 1)
            best = max(best, 0.55 + overlap * 0.35)

        best = max(best, SequenceMatcher(None, normalized_query, normalized_value).ratio() * 0.9)
        best = max(best, SequenceMatcher(None, query_translit, transliterate(normalized_value)).ratio() * 0.86)
        if normalized_query == _acronym(normalized_value):
            best = max(best, 0.88)
    return best


def search_publication_topics(query, *, limit=20):
    normalized_query = normalize_catalog_name(query)
    if len(normalized_query) < 2:
        return []

    candidates = list(
        PublicationTopic.objects.filter(is_active=True, merged_into__isnull=True)
        .only(
            "id",
            "name",
            "aliases",
            "last_used_at",
            "updated_at",
        )
        .order_by("-last_used_at", "-updated_at")[:500]
    )
    ranked = [
        (_topic_score(topic, normalized_query), topic)
        for topic in candidates
    ]
    ranked = [item for item in ranked if item[0] >= 0.48]
    ranked.sort(
        key=lambda item: (
            -item[0],
            -(item[1].last_used_at or item[1].updated_at).timestamp(),
            item[1].name.casefold(),
        )
    )
    return [topic for _score, topic in ranked[:limit]]


@transaction.atomic
def resolve_or_create_publication_topic(name, *, created_by=None):
    normalized_name = normalize_catalog_name(name)
    if not normalized_name:
        raise ValueError("Укажите название темы или события.")

    existing = (
        PublicationTopic.objects.select_for_update()
        .filter(normalized_name=normalized_name)
        .first()
    )
    if existing is not None:
        if existing.merged_into_id:
            existing = existing.merged_into
        if not existing.is_active:
            existing.is_active = True
        existing.last_used_at = timezone.now()
        existing.save(update_fields=["is_active", "last_used_at", "updated_at"])
        return existing, False

    try:
        topic = PublicationTopic.objects.create(
            name=name,
            normalized_name=normalized_name,
            created_by=created_by,
            last_used_at=timezone.now(),
        )
    except IntegrityError:
        topic = PublicationTopic.objects.get(normalized_name=normalized_name)
        topic.last_used_at = timezone.now()
        topic.save(update_fields=["last_used_at", "updated_at"])
        return topic, False
    return topic, True


@transaction.atomic
def merge_publication_topics(source, target):
    if source.pk == target.pk:
        return target

    from apps.directory.models import FormattingTemplate
    from apps.submissions.models import Submission

    source = PublicationTopic.objects.select_for_update().get(pk=source.pk)
    target = PublicationTopic.objects.select_for_update().get(pk=target.pk)
    aliases = [*(target.aliases or []), source.name, *(source.aliases or [])]
    target.aliases = list(dict.fromkeys(value for value in aliases if value and value != target.name))
    target.last_used_at = max(
        value for value in (target.last_used_at, source.last_used_at, timezone.now()) if value
    )
    target.save()

    Submission.objects.filter(publication_topic=source).update(publication_topic=target)
    for template in FormattingTemplate.objects.filter(publication_topic=source).order_by("created_at", "pk"):
        latest_version = (
            FormattingTemplate.objects.filter(
                publication_topic=target,
                article_type=template.article_type,
            ).order_by("-version_number").values_list("version_number", flat=True).first()
            or 0
        )
        template.publication_topic = target
        template.version_number = latest_version + 1
        template.save(update_fields=["publication_topic", "version_number"])

    source.is_active = False
    source.merged_into = target
    source.save(update_fields=["is_active", "merged_into", "updated_at"])
    return target
