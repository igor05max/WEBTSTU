# Document Template Engine

Независимый от Django модуль для анализа требований, проверки структуры и
создания DOCX/LaTeX по шаблону научного материала.

Основные гарантии:

- элементы титульного блока не считаются заголовками разделов;
- правила основного текста не применяются вслепую к УДК, названию, авторам и
  списку литературы;
- исходный научный текст, таблицы, рисунки и ссылки сохраняются;
- отсутствующие блоки заполняются только данными, явно переданными приложением;
- локальная AI-модель подключается через callback и нужна для интерпретации
  свободного текста требований, а сборка DOCX остаётся детерминированной.
- LaTeX-исходники разбираются без компиляции и выполнения пользовательских
  команд; найденные параметры страницы и текста переводятся в общую схему;
- из общей схемы создаётся самостоятельный UTF-8 шаблон для XeLaTeX/LuaLaTeX.

```python
from document_template_engine import (
    build_docx_from_template,
    build_latex_template,
    check_latex_against_template,
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

latex_source = build_latex_template(
    rules,
    metadata={"title": "...", "authors": "..."},
)

latex_report = check_latex_against_template(latex_source, rules)
```

В модуле нет импортов Django, моделей проекта, сетевых клиентов и настроек
конкретного приложения. Для DOCX нужен `python-docx`; создание и проверка
LaTeX-исходника работают на стандартной библиотеке Python.
