# Document Template Engine

Независимый от Django модуль для анализа требований, проверки структуры и
создания DOCX по шаблону научного материала.

Основные гарантии:

- элементы титульного блока не считаются заголовками разделов;
- правила основного текста не применяются вслепую к УДК, названию, авторам и
  списку литературы;
- исходный научный текст, таблицы, рисунки и ссылки сохраняются;
- отсутствующие блоки заполняются только данными, явно переданными приложением;
- локальная AI-модель подключается через callback и нужна для интерпретации
  свободного текста требований, а сборка DOCX остаётся детерминированной.

```python
from document_template_engine import (
    build_docx_from_template,
    interpret_template_text,
)

rules = interpret_template_text(
    document_type="Тезисы",
    target_name="Название конференции",
    text=requirements_text,
    complete_json=local_model_completion,
)

result, changes, plan = build_docx_from_template(
    source_docx,
    rules,
    metadata={"title": "...", "authors": "..."},
)
```

В модуле нет импортов Django, моделей проекта, сетевых клиентов и настроек
конкретного приложения. Для переноса нужен только `python-docx`.
