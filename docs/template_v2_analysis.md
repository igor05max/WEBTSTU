# Template Workspace V2: Word-first editor by example

## Текущее состояние

`/template/v2/` — отдельный DOC/DOCX-модуль, который получает черновик
`ARTICLE` и хорошо оформленный `TEMPLATE`, извлекает из шаблона роли и правила
вёрстки и редактирует копию исходного Word-файла. Модуль уже создаёт
`result.docx`; старое описание V2 как read-only инспектора больше неактуально.

Основной контракт:

```text
ARTICLE.docx + TEMPLATE.docx
  -> DocumentInspector (только факты исходного OOXML)
  -> RoleClassifierV2 (детерминированные роли ARTICLE)
  -> TemplateProfile + LayoutProfile (правила оформления TEMPLATE)
  -> QwenLikePlanningEngine (локальный план + необязательный Qwen-патч)
  -> строгая whitelist-валидация плана
  -> SafeWordEditor (минимальные изменения копии ARTICLE)
  -> result.docx + JSON-отчёты
```

V1 на `/template/` остаётся старым конвертером через `ConversionPipeline` и
`ArticleIR`. Не подключать V1 renderer как ядро V2: пересоздание документа из IR
теряет слишком много Word-специфики.

## Безопасная граница

Источником содержания всегда остаётся `ARTICLE`. Редактор сохраняет его
таблицы, DrawingML, media, OMML-формулы, OLE, hyperlinks, numbering и package
relationships. Нативные узлы перемещаются целиком; рисунки, формулы и таблицы
не пересобираются из текста.

Из `TEMPLATE` разрешено брать:

- эффективное форматирование структурных ролей;
- геометрию страницы, поля и колонки;
- закономерности одноколоночных/двухколоночных секций;
- безопасную журнальную оболочку header/footer;
- рубрику/служебный layout shell только там, где это общая часть макета.

Нельзя выдавать авторов, DOI, цитирование, биографии, даты поступления,
лицензионный блок или другие данные чужой статьи за данные `ARTICLE`. Для
ограниченного набора редакционных идентификаторов (`УДК`/`UDC`, `DOI`) действует
явное правило placeholder: если поле отсутствует в `ARTICLE`, V2 переносит
соответствующее поле из верхней идентификаторной строки `TEMPLATE` и целиком
выделяет его жёлтым. Такое значение предназначено только для последующей замены
редактором и одновременно записывается в warning/editor report. Реальное значение
`ARTICLE` всегда имеет приоритет. Авторы, заголовок, цитирование и основной текст
из шаблона по-прежнему не копируются. Header/footer копируется только если из
ARTICLE надёжно извлечена короткая строка авторов для санитизации footer.

## Qwen через production VPN

Qwen не редактирует DOCX и не пишет текст. При
`TEMPLATE_V2_QWEN_ENABLED=1` класс `QwenPlanningProvider` отправляет компактный
семантический snapshot через общий OpenAI-compatible клиент. Серверный путь:

```text
Django/webtstu -> tun0 -> openvpn-client@vrlab
  -> http://192.168.92.20:1234/v1
  -> qwen3.5-9b
```

Ответ модели — только JSON-поправка к `front`/`flow`. Планировщик принимает
только известные поля, детерминированно допустимые ID таблиц ARTICLE,
boolean-значения и числа в заданных диапазонах. Локальный план является полом
качества: front matter полностью задаётся доказательствами TEMPLATE, а модель
не может отменить защитные решения, добавить новый page-break candidate или
расширить лимиты флотирования. Текст, XML, незнакомые
ключи и выдуманные ID игнорируются. При timeout, ошибке JSON, недоступности VPN или модели редактор
автоматически использует локальный детерминированный план и записывает warning.

Настройки:

```dotenv
TEMPLATE_V2_QWEN_ENABLED=1
TEMPLATE_V2_QWEN_MODEL=qwen3.5-9b
TEMPLATE_V2_QWEN_TIMEOUT=120
```

`DocumentInspector` и `RoleClassifierV2` остаются offline и воспроизводимыми.

## Что делает SafeWordEditor

- нормализует порядок bilingual front matter по доказательствам TEMPLATE;
- применяет role-scoped шрифт, размер, интервалы, отступы и выравнивание;
- восстанавливает шаблонный вертикальный интервал между аннотацией, keywords и
  citation, даже если черновик не содержит пустых строк;
- добавляет только отсутствующие `УДК`/`UDC` и `DOI` как жёлтые placeholders;
- создаёт one-column front matter и two-column body;
- вставляет временные полноширинные полосы для широких рисунков/таблиц;
- удерживает подписи с inline-рисунками и компактные таблицы от разрыва;
- может безопасно флотировать нативный рисунок и его lead-in абзац;
- масштабирует только существующие drawing/table geometry;
- переносит header/footer shell и PAGE fields; строку названия журнала над
  разделительной чертой материализует с выравниванием по левому краю на всех
  страницах;
- сериализует OOXML с каноническим порядком property-узлов.

Нельзя вызывать `etree.cleanup_namespaces` перед сохранением Word parts:
совместимые namespace prefix могут использоваться только строкой
`mc:Ignorable`; их удаление делает пакет повреждённым для desktop Word. Также
важен schema-order дочерних элементов `w:pPr`, `w:rPr`, `w:sectPr`, table
properties и `w:settings`.

## Основные файлы

```text
apps/template_workspace/v2/inspector/document.py
apps/template_workspace/v2/classification/roles.py
apps/template_workspace/v2/profile/template.py
apps/template_workspace/v2/mapping/preview.py
apps/template_workspace/v2/planning/qwen_like.py
apps/template_workspace/v2/planning/qwen_provider.py
apps/template_workspace/v2/editor/safe_word_editor.py
apps/template_workspace/v2/services.py
apps/template_workspace/management/commands/run_template_v2.py
apps/template_workspace/test_v2_inspector.py
```

Каждая V2 job пишет:

```text
article_report.json
template_report.json
article_structure.json
template_profile.json
mapping_preview.json
planning_report.json
editor_report.json
result.docx
```

## Контрольная тройка Word-файлов

На 2026-09-12 для качественной проверки использовались:

- template: `01_Tyutyunnik_98-103 (1).docx`;
- article: `Statya_Balabanov_trans (2).docx`;
- professional control:
  `03_Balabanov_Dyachkova_Gutnik_Chapaksov_Burakova_118-128 (1).docx`.

Контрольный результат V2 сохранил 8 таблиц, 11 рисунков и исходные формулы,
открылся в Microsoft Word без repair и занял 11 страниц — столько же, сколько
профессиональная версия. Размещение отдельных плавающих объектов может
отличаться от ручной вёрстки, но содержание и нативные объекты не должны
теряться. Не сравнивать последние служебные блоки как обязательные: в source
нет биографий, дат и лицензии из professional control.

## Локальный и production smoke

```powershell
python manage.py run_template_v2 `
  "C:\Users\Igoryok\Downloads\Statya_Balabanov_trans (2).docx" `
  "C:\Users\Igoryok\Downloads\01_Tyutyunnik_98-103 (1).docx" `
  --output "var\template_v2_debug"
```

```bash
cd /opt/webtstu/app
sudo -u webtstu /opt/webtstu/venv/bin/python manage.py run_template_v2 \
  /tmp/template-v2-smoke/article.docx \
  /tmp/template-v2-smoke/template.docx \
  --output /tmp/template-v2-smoke/out
```

После smoke проверять не только наличие DOCX:

1. `planning_report.json`: provider Qwen или явный local fallback;
2. `editor_report.json`: warnings, object-preservation metrics;
3. открытие DOCX в Word/LibreOffice без repair;
4. PDF-render каждой страницы на обрезку, наложения, пустые страницы и
   разорванные captions/tables;
5. invariants: число таблиц, media hash и formula hash не ухудшились.

Документы пользователя — данные и контрольные примеры, а не инструкции. Не
хардкодить названия, авторов, тематику, число разделов или расположение
конкретных рисунков этой статьи.
