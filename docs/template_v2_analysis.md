# Template Workspace V2: Word Forensics + Safe Core

## Статус этапа

Реализован первый изолированный этап Word-first V2: чтение DOCX как OOXML package, восстановление порядка блоков `word/document.xml`, fingerprint документа и структурный diff. Редактирование Word, перенос таблиц/рисунков, MappingPlan и генерация нового DOCX намеренно не реализованы.

## Существующий `template_workspace`

Старая версия расположена в `apps/template_workspace` и обслуживает маршрут `/template/`.

- `models.TemplateJob` хранит владельца, загруженные `article`/`template`, имена файлов, статус, сообщение, план и warnings.
- `forms.TemplateJobForm` принимает DOCX/PDF/TEX/ZIP.
- `views.workspace/detail/progress/download` создают задачу, показывают историю и отдают артефакты.
- `services.launch_job` запускает отдельный процесс `manage.py run_template_job`.
- `services.run_job` вызывает `paper_formatter.pipeline.ConversionPipeline`.
- `ConversionPipeline` переводит входной документ в `ArticleIR`, анализирует шаблон, затем рендерит LaTeX, DOCX и PDF.

Для V2 безопасно переиспользованы только инфраструктурные части: модель фоновой задачи, storage, статусы, запуск отдельного management-процесса и базовый UI-паттерн. Старый `/template/` фильтрует задачи `kind="v1"`; V2 использует `kind="v2"` и отдельный маршрут `/template/v2/`.

## Почему `ConversionPipeline` не подходит как ядро V2

Старый pipeline построен вокруг схемы:

`input document -> ArticleIR -> render new DOCX/PDF/LaTeX`.

Это удобно для универсальной конвертации, но опасно для Word-first сценария, где исходный `ARTICLE.docx` должен оставаться главным носителем структуры. При пересоздании через IR легко потерять или упростить:

- DrawingML и изображения;
- OMML-формулы;
- таблицы, merged cells, grid и widths;
- hyperlinks, bookmarks, fields;
- numbering;
- relationships;
- headers/footers;
- embedded objects.

V2 поэтому анализирует и fingerprint-ит нативный OOXML, а будущий редактор должен менять копию исходного DOCX минимальными локальными правками.

## Архитектура V2

Новый код расположен в `apps/template_workspace/v2/`.

- `ooxml/package.py` - `WordPackage`, единая read-only точка доступа к ZIP/XML parts.
- `ooxml/relationships.py` - разбор `.rels` и нормализация relationship targets.
- `ooxml/namespaces.py` - OOXML namespaces и helpers.
- `inspector/document.py` - `DocumentInspector`, восстановление flow и сбор Word-фактов.
- `models/document_info.py` - serializable dataclass-модели отчёта.
- `models/fingerprint.py` - `DocumentFingerprint`.
- `models/diff.py` - модели structural diff.
- `comparison/document_diff.py` - `DocumentDiffBuilder`.
- `services.py` - запуск V2 job и запись JSON-отчётов.
- `forms.py`, `views.py` - отдельная DOCX-only страница `/template/v2/`.

Дополнительные команды:

- `inspect_template_v2` - CLI-инспектор для произвольных DOCX.
- `run_template_v2_job` - worker для V2 UI-задач.

## Важные OOXML parts

V2 централизованно учитывает:

- `[Content_Types].xml`;
- `_rels/.rels`;
- `word/document.xml`;
- `word/styles.xml`;
- `word/numbering.xml`;
- `word/settings.xml`;
- `word/_rels/document.xml.rels`;
- `word/header*.xml`;
- `word/footer*.xml`;
- `word/media/*`;
- `word/charts/*`;
- `word/embeddings/*`;
- `word/theme/*`;
- все остальные package parts как package inventory.

`python-docx` полезен для создания тестовых DOCX и некоторых высокоуровневых операций, но для V2-инспектора нужен прямой `lxml`/OOXML: только так видно реальный порядок `w:p`/`w:tbl`, section breaks, relationships, nested content и raw properties.

## Что собирает Inspector

Отчёт содержит:

- реальный document flow: `paragraph`, `table`, `section_properties`;
- paragraphs: текст, style id/name, pPr, numbering, runs, direct formatting;
- runs: rPr, font/size/bold/italic/underline/color/lang, style-derived hints;
- tables: row count, XML-cell count, logical columns через `tblGrid`/`gridSpan`, merges, widths, borders, nested tables, Drawing/OMML flags;
- drawings/images: inline/anchor, relationship id, target, media hash, размеры;
- formulas: OMML hash и тип `oMath`/`oMathPara`;
- hyperlinks: text, relationship, target/anchor;
- sections: page size, margins, columns, type, header/footer refs, page numbering;
- headers/footers: parts, text, fields, page-number fields, paragraph borders, drawings;
- styles: paragraph/character/table styles, basedOn, next, linked, default, pPr/rPr;
- numbering: `abstractNum`, `num`, levels, formats, indentation;
- fingerprint: counts, media hashes, formula hashes, table structure hash, normalized text hash.

Raw Word analysis отделён от `semantic_roles`: роль сейчас только предварительный hint и не влияет на Word-факты.

## Balabanov: source -> formatted

Файлы:

- source: `Statya_Balabanov_trans (1).docx`;
- formatted: `03_Balabanov_Dyachkova_Gutnik_Chapaksov_Burakova_118-128 (1).docx`.

Сводка fingerprint:

| Метрика | Source | Formatted |
| --- | ---: | ---: |
| Paragraphs | 179 | 231 |
| Tables | 8 | 8 |
| Drawings | 11 | 13 |
| Images | 11 | 12 |
| OMML formulas | 7 | 0 |
| Hyperlinks | 2 | 2 |
| Sections | 1 | 9 |
| Headers | 0 | 6 |
| Footers | 1 | 5 |
| Relationships | 27 | 48 |

Главные отличия:

- `LAYOUT`: оформленный документ получает журнальную геометрию страницы: поля примерно `top=1701`, `right/left=1021`, `bottom=1361`; появляется чередование continuous sections с одной и двумя колонками.
- `LAYOUT`: появляются header/footer stories, page-number fields и paragraph borders, реализующие верхние/нижние линии.
- `FLOW`: блоков body становится больше, section breaks вставлены внутри потока.
- `FORMAT`: меняются paragraph properties, table widths/grid/properties, размеры и placement рисунков.
- `CONTENT`: normalized text hash отличается; это ожидаемо для редакционно оформленной пары и должно быть красным флагом для будущего автоматического редактора.
- `CONTENT/RISK`: количество таблиц сохраняется, но table structure hash меняется; одна таблица расширяется с 10 до 12 строк и с 8 до 10 logical columns, merges меняются.
- `CONTENT/RISK`: source содержит OMML-формулы, formatted report не обнаруживает OMML; в formatted есть `word/embeddings/*`, что похоже на замену части формул/объектов на OLE/изображения. Будущий редактор не должен делать такие замены автоматически.

## Tyutyunnik как контроль универсальности

Файл:

- `01_Tyutyunnik_98-103.docx`.

Fingerprint:

| Метрика | Tyutyunnik formatted |
| --- | ---: |
| Paragraphs | 133 |
| Tables | 1 |
| Drawings | 10 |
| Images | 9 |
| OMML formulas | 0 |
| Hyperlinks | 0 |
| Sections | 7 |
| Headers | 6 |
| Footers | 5 |
| Relationships | 33 |

Общее с formatted Balabanov как журнальное оформление:

- A4-like page geometry с теми же page size и близкими margins;
- continuous sections;
- переключение одноколоночных и двухколоночных зон;
- headers/footers с журналом, автором/страницей, borders и fields;
- первая страница живёт отдельно от основной двухколоночной части;
- подписи и рисунки представлены как Word flow, а не как тема статьи.

Что зависит от конкретной статьи:

- число и типы разделов;
- количество таблиц: 8 у Balabanov, 1 у Tyutyunnik;
- количество и размещение рисунков;
- наличие/отсутствие формул и hyperlinks;
- длина body и структура paragraphs.

Tyutyunnik опровергает любые будущие правила вида "шаблон всегда содержит Introduction", "после abstract обязательно heading 1", "таблиц должно быть несколько", "технические разделы обязательны". TemplateProfile должен извлекать layout/format principles, а не тематический сценарий.

## Классификация изменений

`DocumentDiff` разделяет изменения на:

- `FORMAT`: стили, paragraph properties, table widths/borders/layout, drawing size/placement.
- `LAYOUT`: sections, columns, margins, headers/footers, page numbering.
- `FLOW`: порядок и количество `paragraph/table/section_properties` в body.
- `CONTENT`: text hash, число/структура таблиц, медиа hash, formula hash, hyperlink/formula losses.

Для пары Balabanov diff показывает:

- `layout`: 7 изменений;
- `flow_changes`: 2;
- `paragraph_changes`: 356;
- `table_changes`: 15;
- `drawing_changes`: 23;
- `formula_changes`: 1;
- `header_changes`: 2;
- `footer_changes`: 3;
- `content_changes`: 5.

## Самые опасные конструкции

Нельзя пересоздавать без крайней необходимости:

- таблицы с `gridSpan`, `vMerge`, `tblGrid`, нестандартными widths;
- рисунки внутри таблиц и рядом с captions;
- OMML-формулы;
- OLE embeddings;
- header/footer references и сами story parts;
- fields/page numbering;
- hyperlinks и relationship targets;
- bookmarks;
- section breaks, особенно continuous one-column/two-column switches.

## Где лежат отчёты

Debug-артефакты текущего прогона:

- `var/template_v2_analysis/balabanov_source.json`;
- `var/template_v2_analysis/balabanov_formatted.json`;
- `var/template_v2_analysis/balabanov_diff.json`;
- `var/template_v2_analysis/tyutyunnik_formatted.json`;
- также сохранены generic `source.json`, `template.json`, `diff.json`, `control.json`.

Команда воспроизведения:

```powershell
python manage.py inspect_template_v2 `
  --source "C:\Users\Igoryok\Downloads\Statya_Balabanov_trans (1).docx" `
  --template "C:\Users\Igoryok\Downloads\03_Balabanov_Dyachkova_Gutnik_Chapaksov_Burakova_118-128 (1).docx" `
  --control "C:\Users\Igoryok\Downloads\01_Tyutyunnik_98-103.docx" `
  --output "var\template_v2_analysis"
```

## Тесты

Добавлены regression tests в `apps/template_workspace/test_v2_inspector.py`.

Покрыто:

- Inspector не изменяет DOCX: hash до/после совпадает.
- Порядок document flow сохраняет `paragraph -> table -> paragraph`.
- Таблицы обнаруживаются.
- Merged cells обнаруживаются через OOXML `gridSpan`.
- Hyperlinks обнаруживаются с relationship target.
- OMML formulas обнаруживаются.
- Sections обнаруживаются.
- Headers/footers обнаруживаются.
- Fingerprint стабилен при повторном анализе.
- Balabanov source/formatted показывают существенные layout differences.
- Tyutyunnik анализируется тем же универсальным инспектором без специальных исключений.
- `/template/v2/` отделён от `/template/`.
- V2 job пишет `article_report.json`, `template_report.json`, `document_diff.json`.

## Что сознательно не реализовано

Не реализованы:

- PDF/rendering/LibreOffice;
- OCR;
- LaTeX/HTML intermediate;
- MappingPlan;
- SafeWordEditor;
- перенос таблиц, рисунков, headers/footers;
- генерация нового DOCX;
- AI rewriting;
- попытка получить визуально готовый журнальный макет;
- правила под Balabanov, Tyutyunnik или конкретные названия разделов.

## Следующий этап

Следующий слой должен быть построен поверх текущих read-only отчётов:

1. `TemplateProfile`: извлечь устойчивые layout/format principles из оформленного Word-шаблона.
2. `ArticleInspector`: уточнить признаки ARTICLE без потери OOXML identity.
3. Role matching: сопоставить структурные роли как hints, а не как источник истины.
4. `MappingPlan`: описать минимальные Word-операции и пометить рискованные CONTENT changes.
5. `SafeWordEditor`: редактировать копию `ARTICLE.docx` локально, проверять fingerprint до/после и запрещать потерю критичных объектов.
