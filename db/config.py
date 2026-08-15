import os
from urllib.parse import quote_plus

REQUIRED_VARS = (
    "BOT_DB_HOST",
    "BOT_DB_PORT",
    "BOT_DB_USER",
    "BOT_DB_PASSWORD",
    "BOT_DB_NAME",
)


def database_url(driver: str = "psycopg") -> str:
    """DSN из переменных окружения BOT_DB_*.

    driver: 'psycopg' — синхронный, 'asyncpg' — асинхронный.
    """
    values = {name: (os.getenv(name) or "").strip() for name in REQUIRED_VARS}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise RuntimeError(f"Не заданы переменные окружения: {', '.join(missing)}")

    user = quote_plus(values["BOT_DB_USER"])
    password = quote_plus(values["BOT_DB_PASSWORD"])
    return (
        f"postgresql+{driver}://{user}:{password}"
        f"@{values['BOT_DB_HOST']}:{values['BOT_DB_PORT']}/{values['BOT_DB_NAME']}"
    )
