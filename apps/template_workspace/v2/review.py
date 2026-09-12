"""Explain editorial gaps without inventing or rewriting scientific content."""
import re


def editorial_findings(article_report, template_report, structure, profile):
    warnings = []
    roles = {b.get('detected_role') for b in structure.blocks}
    labels = {'affiliation':'аффилиация', 'email':'контакт для переписки', 'author':'авторы'}
    for role, label in labels.items():
        if role in profile.roles and role not in roles:
            warnings.append(f'В TEMPLATE есть поле «{label}», но в ARTICLE оно не найдено. Чужие данные из образца не подставлялись.')
    requirements = ' '.join(p.normalized_text for p in template_report.paragraphs[:25])
    limits = re.findall(r'(?:about\s+|maximum\s+|up to\s+)?(\d{2,4})\s+words\s+(?:maximum|max)', requirements, re.I)
    if limits:
        maximum = min(int(value) for value in limits)
        by_id = {p.id:p for p in article_report.paragraphs}
        count = sum(len(by_id[b['id']].normalized_text.split()) for b in structure.blocks
                    if b.get('detected_role') == 'abstract' and b['id'] in by_id)
        if count > maximum:
            warnings.append(f'Объём аннотации ARTICLE: около {count} слов; инструкция TEMPLATE указывает максимум {maximum}. Текст сохранён, требуется содержательное сокращение редактором.')
    title_ids = {b['id'] for b in structure.blocks if b.get('detected_role') == 'title'}
    titles = [p.normalized_text for p in article_report.paragraphs if p.id in title_ids]
    template_titles = [p.normalized_text for p in template_report.paragraphs
                       if any(v['block_id'] == p.id for v in getattr(profile.roles.get('title'), 'observed_variants', []))]
    matches = [text for text in titles if text.casefold() in {t.casefold() for t in template_titles}]
    if matches and len(matches) < len(titles):
        warnings.append('Один из разноязычных заголовков ARTICLE совпадает с заголовком образца TEMPLATE, а другой отличается. Проверьте, не осталась ли в исходнике шаблонная заглушка; автоматически текст не заменялся.')
    return warnings
