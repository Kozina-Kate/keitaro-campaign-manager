# Keitaro Campaign Manager

Обёртка над Keitaro на FastAPI и SQLAlchemy. Интерфейс создания кампаний и
редактирования офферов будет добавлен на следующих этапах.

## Текущее состояние: этап 1

- Настройки через переменные окружения и `.env`.
- Клиент Keitaro Admin API с таймаутами и обработкой ошибок.
- Получение доменов, групп кампаний, источников и офферов.
- База SQLite, модель журнала операций и первая миграция Alembic.
- Проверка названия: обязательный префикс `test_kozina_`.
- Автоматические проверки API-клиента, конфигурации и миграций.

Создание кампаний, редактор и запись операций пока не реализованы.
На этом этапе приложение выполняет только GET-запросы к Keitaro.

## Установка

Нужен Python 3.11 или новее. Команды выполняются из корня проекта.

```bash
git clone https://github.com/Kozina-Kate/keitaro-campaign-manager.git
cd keitaro-campaign-manager
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install -e . --no-deps
cp .env.example .env
```

В `.env` укажите корневой HTTPS-адрес трекера **без `/admin`** и существующий
API-ключ из профиля Keitaro → «API ключи»:

```dotenv
KEITARO_BASE_URL=https://your-tracker.example
KEITARO_API_KEY=your-api-key
KEITARO_TIMEOUT_SECONDS=20
DATABASE_URL=sqlite:///./app.db
```

`.env`, база и виртуальное окружение исключены из Git. Ключ передаётся только
сервером в заголовке `Api-Key`; в ответах приложения и браузерном коде его нет.
Не публикуйте `.env` и не вставляйте ключ в README или URL.

## Миграции и запуск

```bash
alembic upgrade head
uvicorn app.main:create_app --factory --reload --host 127.0.0.1
```

- Документация приложения: <http://127.0.0.1:8000/docs>.
- Готовность базы: `GET /health` — `200`, если таблица журнала доступна;
  `503`, если необходимо проверить БД или применить миграции.
- Справочники Keitaro: `GET /api/reference-data` — списки ID и названий.

Проверка подключения без изменения данных (выводится только число записей):

```bash
python -m app.check_connection
```

Ошибки подключения возвращаются с кодами `502`/`504`, отсутствие ключа — `503`.
Необработанные тела ответов Keitaro и секреты не передаются клиенту.
Сервер предназначен для локального запуска; перед размещением в сети потребуется
аутентификация пользователей.

Проверить версию схемы и отсутствие расхождений с моделями:

```bash
alembic current
alembic check
```

Откат первой миграции: `alembic downgrade base`. Он удаляет таблицу журнала
вместе с записями; для рабочей базы сначала сделайте резервную копию.

## Проверки

```bash
pytest -q
ruff check .
```

Тесты не обращаются к реальному Keitaro: ответы API подменяются через HTTPX,
миграции проверяются на временной базе.

Для обновления зависимостей разработчик может использовать
`python -m pip install -e '.[dev]'`. Для повторяемой установки используйте
зафиксированные версии из `requirements.lock`.

## Структура

```text
app/
  config.py            Настройки окружения
  database.py          Подключение SQLAlchemy
  models.py            Модель журнала операций
  schemas.py           Схемы ответов и проверка названия
  keitaro.py           Клиент Admin API
  main.py              FastAPI и маршруты
  check_connection.py  Проверка подключения
migrations/            Миграции Alembic
tests/                 Автоматические проверки
docs/requirements.md   Зафиксированные требования и следующие этапы
```
