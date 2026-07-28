# Развёртывание на Ubuntu 24.04

Приложение запускается под отдельным пользователем `webtstu` через Gunicorn и
systemd. Nginx принимает HTTP-запросы и раздаёт только собранные статические
файлы. Загруженные материалы не публикуются напрямую из `media/`: доступ к ним
остаётся под контролем Django.

Системные зависимости просмотра документов:

```bash
apt-get install -y --no-install-recommends \
  libreoffice-writer libreoffice-math \
  latexmk texlive-latex-extra texlive-fonts-recommended texlive-lang-cyrillic \
  texlive-plain-generic \
  fonts-dejavu-core fonts-liberation \
  fonts-crosextra-carlito fonts-crosextra-caladea
```

`libreoffice-math` обязателен: без него LibreOffice оставляет номера формул, но
не рисует сами объекты Office Math из DOCX.

`latexmk` и пакеты `texlive-*` используются для безопасного просмотра
загруженных TEX-файлов и полных ZIP-проектов. Компиляция выполняется без
shell-escape, с ограничением времени и доступа к файлам вне проекта.

Каталоги на сервере:

- `/opt/webtstu/app` — Git-репозиторий приложения;
- `/opt/webtstu/venv` — виртуальное окружение Python;
- `/opt/webtstu/shared/.env` — production-настройки и секреты;
- `/opt/webtstu/app/db.sqlite3` — демонстрационная база данных;
- `/opt/webtstu/app/media` — загруженные пользователями файлы.

Файл `/opt/webtstu/app/.env` должен быть символической ссылкой на
`/opt/webtstu/shared/.env`. Благодаря этому одинаковые production-настройки
используют и systemd, и ручные management-команды Django.

Для точного просмотра DOC и DOCX на сервере используется LibreOffice в
headless-режиме. Каждый запрос конвертируется в отдельном временном каталоге,
поэтому несколько пользователей могут открывать документы одновременно.

После обновления кода:

```bash
cd /opt/webtstu/app
sudo -u webtstu git pull --ff-only
sudo -u webtstu /opt/webtstu/venv/bin/pip install -r requirements.txt
sudo -u webtstu /opt/webtstu/venv/bin/python manage.py migrate --noinput
sudo -u webtstu /opt/webtstu/venv/bin/python manage.py collectstatic --noinput
systemctl restart webtstu
```

Для извлечения данных из старых `.doc` и семантического уточнения структуры
проверьте production-настройки:

```bash
sudo -u webtstu /usr/bin/libreoffice --version
grep -E '^(LIBREOFFICE_BINARY|AI_PROVIDER|AI_BASE_URL|AI_MODEL|SUBMISSION_DOCUMENT_EXTRACTION_AI_ENABLED)=' \
  /opt/webtstu/shared/.env
```

Ожидаемые значения:

```dotenv
LIBREOFFICE_BINARY=/usr/bin/libreoffice
AI_PROVIDER=openai_compatible
AI_BASE_URL=http://192.168.92.20:8088/v1
AI_MODEL=Qwen3.6-27B-IQ4_XS.gguf
SUBMISSION_DOCUMENT_EXTRACTION_AI_ENABLED=1
```

## HTTPS без домена

Сертификат для IP хранится в `/etc/letsencrypt/live/185.221.154.185/`.
IP-сертификаты Let’s Encrypt короткоживущие, поэтому таймер
`certbot-ip-renew.timer` должен быть постоянно включён. Он дважды в сутки
проверяет продление и перезагружает Nginx только после получения нового
сертификата.
