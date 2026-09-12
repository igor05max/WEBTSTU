# Template Workspace V2: Word-first editor by example

## Текущее состояние

`/template/v2/` — отдельный Word-модуль (статья DOC/DOCX, шаблон DOC/DOCX/DOTX), который получает черновик
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
  -> TEMPLATE_V2_QWEN_BASE_URL или общий AI_BASE_URL
  -> POST /v1/chat/completions
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
TEMPLATE_V2_QWEN_BASE_URL=http://192.168.92.20:8088/v1
TEMPLATE_V2_QWEN_MODEL=qwen3.8-27b-vision
TEMPLATE_V2_QWEN_TIMEOUT=120
```

`DocumentInspector` и `RoleClassifierV2` остаются offline и воспроизводимыми.
На 2026-09-12 оба endpoint работают: 1234 (`qwen3.5-9b`) и 8088
(`qwen3.8-27b-vision`). Каждый прошёл реальные запросы на четырёх новых парах.
Общий `AI_BASE_URL` сайта не переключается. Snapshot ограничен 22000 символами;
переполнение старого контекста 20k токенов на длинной статье исправлено.
Vision новой модели отдельно проверен на изображениях страниц при разработке;
автоматический production-планировщик пока получает только структурный JSON,
а не картинки. Не выдавать ручной vision-QA за часть каждой пользовательской job.

## Что делает SafeWordEditor

- нормализует порядок bilingual front matter по доказательствам TEMPLATE;
- применяет role-scoped шрифт, размер, интервалы, отступы и выравнивание;
- восстанавливает шаблонный вертикальный интервал между аннотацией, keywords и
  citation, даже если черновик не содержит пустых строк;
- добавляет только отсутствующие `УДК`/`UDC` и `DOI` как жёлтые placeholders;
- берёт число колонок и поля из TEMPLATE, включая одноколоночный MDPI и A5;
- вставляет временные полноширинные полосы для широких рисунков/таблиц;
- удерживает подписи с inline-рисунками и компактные таблицы от разрыва;
- может безопасно флотировать нативный рисунок и его lead-in абзац;
- масштабирует только существующие drawing/table geometry;
- переносит header/footer shell, логотипы и PAGE fields с уникальными именами
  package parts; для JAMT сохраняет явно запрошенную левую строку над чертой;
- переносит свойства таблиц из TEMPLATE; согласует grid/tcW, отменяет старые
  абзацные отступы внутри ячеек и учитывает ширину чисел;
- материализует скрытые свойства старых Equation-полей; не показывает их
  служебные строки и не превращает OLE-формулы в новый текст;
- проверяет сохранность научных токенов, OMML, объектов, ссылок и исходных media
  до записи результата; ошибка проверки блокирует выдачу повреждённого DOCX;
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
apps/template_workspace/v2/editor/template_evidence.py
apps/template_workspace/v2/editor/integrity.py
apps/template_workspace/v2/word_inputs.py
apps/template_workspace/v2/review.py
apps/template_workspace/v2/services.py
apps/template_workspace/management/commands/run_template_v2.py
apps/template_workspace/test_v2_inspector.py
apps/template_workspace/test_v2_cross_template.py
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

Дополнительный корпус: `Novaya_papka_19.zip` пользователя, четыре папки.

| Пара | Исходник | Шаблон | Назначение контроля |
| --- | --- | --- | --- |
| MDPI LLM | `1/статья.docx` | `1/Type of the Paper.docx` | Одноколоночный макет, длинные таблицы, OLE-формулы |
| MDPI gait | `2/Влияние скорости на симметрию V8.docx` | тот же Word-шаблон MDPI | Много авторов/ORCID, OMML, графики |
| Legacy | `3/Статья.doc` | `3/ОБРАЗЕЦ_оформления-статьи-новый.doc` | Образец после страницы инструкций, повторная обработка заполненной статьи |
| RUS A5 | `4/Обухов.docx` | `4/template_rus.dotx` | A5, авторы перед заголовком, двуязычный хвост, две библиографии |
| JAMT | Балабанов | Тютюнник | Регрессия прежнего двухколоночного оформления |

QA-файлы находятся локально в `.codex_cross_template_20260912`, серверные
прогоны — `/opt/webtstu/var/cross-template-20260912`. Они не входят в Git.
Для проверки кандидата использовался отдельный server worktree `candidate`,
не подмена рабочего приложения во время пользовательской job.

Нельзя сравнивать русский Word с английским `ARTICLE.docx`/TEX только по числу
страниц: перевод и содержательное сокращение в эту функцию не входят.
Отсутствующие аффилиации/контакты и превышение лимита аннотации отражаются в
warnings. В legacy-исходнике обнаружен английский заголовок, оставшийся от
образца: он отмечается для проверки, а не переписывается без согласования.
Размещение всех плавающих объектов и произвольные сложные макеты пока не
гарантируют совпадение с ручной издательской вёрсткой.

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
