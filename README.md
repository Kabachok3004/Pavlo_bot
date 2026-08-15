# Pavlo bot — схема БД

Схема и миграции для VPN-бота: пользователи Telegram и их подписки.
Репозиторий отдаёт только модели SQLAlchemy и миграции Alembic — engine и сессии
создают бот и бэкенд у себя.

## Структура

```
db/
  base.py      Declarative Base + naming convention для ограничений
  models.py    User, Subscription, SubscriptionPlan
  service.py   класс DB — готовые операции над пользователями и подписками
  config.py    сборка DSN из переменных BOT_DB_*
migrations/    Alembic (env.py читает .env)
alembic.ini
```

## Схема

**users** — заводится при первом сообщении боту.

| поле | тип | комментарий |
|---|---|---|
| `id` | bigint PK | внутренний id, на него ссылаются связи |
| `telegram_id` | bigint UNIQUE | id аккаунта в Telegram |
| `username` | varchar(32) NULL | |
| `parent_id` | bigint NULL → users.id | владелец группы, платящий за пользователя; `ON DELETE SET NULL` |
| `created_at` | timestamptz | `now()` |

**subscriptions** — история: продление это новая строка, старые не изменяются.

| поле | тип | комментарий |
|---|---|---|
| `id` | bigint PK | |
| `user_id` | bigint → users.id | чей доступ; `ON DELETE CASCADE` |
| `payer_id` | bigint NULL → users.id | кто оплатил; NULL — оплатил сам |
| `plan` | varchar(32) | `trial` / `month` / `quarter` / `year` |
| `profile_name` | varchar(64) | имя профиля VPN, обычно = username |
| `started_at`, `expires_at` | timestamptz | `CHECK (expires_at > started_at)` |
| `created_at` | timestamptz | `now()` |

Индексы: `users(parent_id)`, `subscriptions(user_id, expires_at)`, `subscriptions(payer_id)`,
`subscriptions(profile_name)`.

Принятые решения:

- Строка подписки есть у каждого участника группы, даже если платил `parent`.
  Проверка доступа всегда одна и та же и не требует обхода дерева.
- `parent_id` в `users` описывает только структуру группы, на доступ не влияет.
- Тариф хранится строкой с `CHECK`, а не PG-типом: новый тариф добавляется
  миграцией `ALTER ... CHECK`, без `ALTER TYPE`.
- Все времена — `timestamptz`, писать только aware-datetime в UTC.

## Установка

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

Скопировать `.env.example` в `.env` и заполнить `BOT_DB_HOST/BOT_DB_PORT/BOT_DB_USER/BOT_DB_PASSWORD/BOT_DB_NAME`.

## Миграции

```bash
.venv/bin/alembic upgrade head
```

Прочие команды:

```bash
.venv/bin/alembic revision --autogenerate -m "описание"
```

```bash
.venv/bin/alembic downgrade -1
```

```bash
.venv/bin/alembic check
```

`alembic check` показывает расхождение моделей и БД, `alembic current` — текущую ревизию.
Разовое переопределение подключения: `alembic -x db_url=postgresql+psycopg://... upgrade head`.

Автогенерацию всегда просматривать глазами: удаление и переименование колонок Alembic
угадывает как drop + create.

`.env` загружается с `override=True` и перебивает переменные, уже висящие в оболочке.
Если подключение уходит не туда, посмотреть эффективные значения:

```bash
.venv/bin/python -c "from dotenv import load_dotenv; load_dotenv(override=True); import os; print({k: os.getenv(k) for k in ('BOT_DB_HOST','BOT_DB_PORT','BOT_DB_USER','BOT_DB_NAME')})"
```

## Использование в боте и бэкенде

Синхронно:

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from db import database_url, User, Subscription

engine = create_engine(database_url())
Session = sessionmaker(engine)
```

Асинхронно (нужен `asyncpg`):

```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from db import database_url

engine = create_async_engine(database_url("asyncpg"))
Session = async_sessionmaker(engine, expire_on_commit=False)
```

## Класс DB

`db/service.py` — готовые операции. Методы делают `flush()` и не коммитят:
транзакцией управляет вызывающий код. Для скриптов и ручных правок есть
`connect()` — сам открывает сессию и коммитит на выходе.

```python
from datetime import date
from db import connect, SubscriptionPlan

with connect() as db:
    user = db.add_user(telegram_id=123456, username="vasya")
    db.extend_subscription(date(2026, 12, 31), username="vasya",
                           plan=SubscriptionPlan.YEAR, profile_name="vasya-mac")
```

В боте — поверх своей сессии:

```python
from db import DB

with Session() as session, session.begin():
    DB(session).cancel_subscription(username="vasya")
```

| метод | что делает |
|---|---|
| `add_user(telegram_id, username, parent_id=None)` | создаёт пользователя; если `telegram_id` уже есть — обновляет username |
| `get_user(user_id \| telegram_id \| username)` | пользователь или `None` |
| `extend_subscription(until, user_id \| username, plan=None, profile_name=None, payer_id=None)` | продлевает до `until`, создаёт подписку при отсутствии |
| `cancel_subscription(user_id \| username)` | закрывает подписку сейчас, возвращает число строк |
| `get_subscription(user_id \| username \| profile_name)` | последняя подписка (может быть истёкшей) |
| `active_subscription(user_id \| username)` | действующая подписка или `None` |
| `expiring_in(days, exact_day=False)` | подписки, кончающиеся в ближайшие `days` дней; `exact_day=True` — ровно в день «сегодня + days» по UTC |

Поведение, о котором стоит знать:

- `extend_subscription` пишет **новую строку**, начинающуюся с конца текущей;
  тариф, профиль и плательщик наследуются от неё, `payer_id` по умолчанию —
  `parent_id` пользователя. Дата раньше текущего конца — `ValueError`.
- `until` типа `date` разворачивается в `23:59:59` UTC этого дня, naive
  `datetime` считается UTC.
- `cancel_subscription` обрезает идущий период текущим временем, а ещё не
  начавшиеся периоды удаляет; история не переписывается.
- Неизвестный пользователь — `LookupError`.
- Модуль синхронный (psycopg). Из async-бота вызывать через
  `asyncio.to_thread(...)` либо писать запросы на async-сессии напрямую.

Тот же запрос без класса:

```python
from datetime import datetime, timezone
from sqlalchemy import select
from db import Subscription

stmt = (
    select(Subscription)
    .where(Subscription.user_id == user_id,
           Subscription.expires_at > datetime.now(timezone.utc))
    .order_by(Subscription.expires_at.desc())
    .limit(1)
)
```

## Возможные расширения

- Платежи (сумма, провайдер, transaction_id) — отдельная таблица со ссылкой на `subscriptions`.
- Ключи/конфиги VPN — отдельная таблица со ссылкой на `users`.
- Запрет пересекающихся периодов у одного пользователя — `EXCLUDE USING gist`
  по `(user_id WITH =, tstzrange(started_at, expires_at) WITH &&)` (нужно расширение `btree_gist`).
