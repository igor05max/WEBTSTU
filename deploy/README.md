# Развёртывание на Ubuntu 24.04

Приложение запускается под отдельным пользователем `webtstu` через Gunicorn и
systemd. Nginx принимает HTTP-запросы и раздаёт только собранные статические
файлы. Загруженные материалы не публикуются напрямую из `media/`: доступ к ним
остаётся под контролем Django.

Системные зависимости просмотра документов:

```bash
apt-get install -y --no-install-recommends \
  libreoffice-writer libreoffice-math \
  fonts-dejavu-core fonts-liberation \
  fonts-crosextra-carlito fonts-crosextra-caladea
```

`libreoffice-math` обязателен: без него LibreOffice оставляет номера формул, но
не рисует сами объекты Office Math из DOCX.

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

## HTTPS без домена

Сертификат для IP хранится в `/etc/letsencrypt/live/185.221.154.185/`.
IP-сертификаты Let’s Encrypt короткоживущие, поэтому таймер
`certbot-ip-renew.timer` должен быть постоянно включён. Он дважды в сутки
проверяет продление и перезагружает Nginx только после получения нового
сертификата.
