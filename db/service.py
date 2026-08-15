"""Готовые операции над пользователями и подписками.

Методы делают flush(), но не коммитят — транзакцией управляет вызывающий код.
Для ручного запуска есть connect(): открывает сессию и коммитит на выходе.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterator

from dotenv import load_dotenv
from sqlalchemy import Engine, and_, create_engine, func, select
from sqlalchemy.orm import Session, joinedload

from db.config import database_url
from db.models import Subscription, SubscriptionPlan, User

DEFAULT_PLAN = SubscriptionPlan.MONTH


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_datetime(value: date | datetime) -> datetime:
    """date -> конец дня в UTC; naive datetime считается UTC."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.combine(value, time(23, 59, 59), tzinfo=timezone.utc)


class DB:
    """Обёртка над сессией: всё взаимодействие с БД идёт через её методы."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # --- пользователи -------------------------------------------------

    def get_user(
        self,
        *,
        user_id: int | None = None,
        telegram_id: int | None = None,
        username: str | None = None,
    ) -> User | None:
        """Пользователь по внутреннему id, telegram_id или username."""
        if user_id is not None:
            return self.session.get(User, user_id)
        if telegram_id is not None:
            return self.session.scalar(select(User).where(User.telegram_id == telegram_id))
        if username is not None:
            return self.session.scalar(select(User).where(User.username == username))
        raise ValueError("Нужен user_id, telegram_id или username")

    def add_user(
        self,
        telegram_id: int,
        username: str | None = None,
        *,
        parent_id: int | None = None,
    ) -> User:
        """Создать пользователя. Если telegram_id уже есть — обновить username."""
        user = self.get_user(telegram_id=telegram_id)
        if user is not None:
            if username is not None:
                user.username = username
            if parent_id is not None:
                user.parent_id = parent_id
        else:
            user = User(telegram_id=telegram_id, username=username, parent_id=parent_id)
            self.session.add(user)
        self.session.flush()
        return user

    # --- подписки -----------------------------------------------------

    def get_subscription(
        self,
        *,
        user_id: int | None = None,
        username: str | None = None,
        profile_name: str | None = None,
    ) -> Subscription | None:
        """Последняя подписка (по expires_at). Может быть уже истёкшей —
        проверяйте expires_at у результата."""
        stmt = select(Subscription).options(joinedload(Subscription.user))
        if profile_name is not None:
            stmt = stmt.where(Subscription.profile_name == profile_name)
        else:
            user = self._require_user(user_id=user_id, username=username)
            stmt = stmt.where(Subscription.user_id == user.id)
        return self.session.scalar(stmt.order_by(Subscription.expires_at.desc()).limit(1))

    def active_subscription(
        self, *, user_id: int | None = None, username: str | None = None
    ) -> Subscription | None:
        """Действующая подписка пользователя или None."""
        subscription = self.get_subscription(user_id=user_id, username=username)
        if subscription is None or subscription.expires_at <= _utcnow():
            return None
        return subscription

    def extend_subscription(
        self,
        until: date | datetime,
        *,
        user_id: int | None = None,
        username: str | None = None,
        plan: SubscriptionPlan | None = None,
        profile_name: str | None = None,
        payer_id: int | None = None,
    ) -> Subscription:
        """Продлить подписку до until, создав её при отсутствии.

        Продление — новая строка, начинающаяся с конца текущей. Тариф, профиль и
        плательщик по умолчанию наследуются от текущей подписки.
        """
        user = self._require_user(user_id=user_id, username=username)
        until = _as_datetime(until)
        current = self.active_subscription(user_id=user.id)

        if current is not None and until <= current.expires_at:
            raise ValueError(
                f"Подписка уже действует до {current.expires_at.isoformat()}"
            )

        subscription = Subscription(
            user_id=user.id,
            payer_id=payer_id
            or (current.payer_id if current else None)
            or user.parent_id,
            plan=plan or (current.plan if current else DEFAULT_PLAN),
            profile_name=profile_name
            or (current.profile_name if current else None)
            or self._default_profile_name(user),
            started_at=current.expires_at if current else _utcnow(),
            expires_at=until,
        )
        self.session.add(subscription)
        self.session.flush()
        return subscription

    def cancel_subscription(
        self, *, user_id: int | None = None, username: str | None = None
    ) -> int:
        """Закрыть подписку сейчас. Возвращает число затронутых строк.

        Идущий период обрезается текущим временем, ещё не начавшиеся удаляются.
        """
        user = self._require_user(user_id=user_id, username=username)
        now = _utcnow()
        subscriptions = self.session.scalars(
            select(Subscription).where(
                Subscription.user_id == user.id, Subscription.expires_at > now
            )
        ).all()
        for subscription in subscriptions:
            if subscription.started_at >= now:
                self.session.delete(subscription)
            else:
                subscription.expires_at = now
        self.session.flush()
        return len(subscriptions)

    def expiring_in(self, days: int, *, exact_day: bool = False) -> list[Subscription]:
        """Подписки, заканчивающиеся в ближайшие days дней.

        exact_day=True — только те, что кончаются в календарный день
        «сегодня + days» по UTC (для ежедневных напоминаний).
        У каждого пользователя берётся последний период, так что уже
        продлённые в выборку не попадают.
        Пользователь доступен как subscription.user.
        """
        if days < 0:
            raise ValueError("days не может быть отрицательным")
        now = _utcnow()

        latest = (
            select(
                Subscription.user_id.label("user_id"),
                func.max(Subscription.expires_at).label("ends_at"),
            )
            .group_by(Subscription.user_id)
            .subquery()
        )
        stmt = (
            select(Subscription)
            .join(
                latest,
                and_(
                    Subscription.user_id == latest.c.user_id,
                    Subscription.expires_at == latest.c.ends_at,
                ),
            )
            .where(latest.c.ends_at > now, self._deadline(latest.c.ends_at, days, exact_day))
            .options(joinedload(Subscription.user))
            .order_by(Subscription.expires_at)
        )
        return list(self.session.scalars(stmt).all())

    # --- вспомогательное ----------------------------------------------

    @staticmethod
    def _deadline(column, days: int, exact_day: bool):
        if not exact_day:
            return column <= _utcnow() + timedelta(days=days)
        target = (_utcnow() + timedelta(days=days)).date()
        return func.date(func.timezone("UTC", column)) == target

    def _require_user(
        self, *, user_id: int | None = None, username: str | None = None
    ) -> User:
        user = self.get_user(user_id=user_id, username=username)
        if user is None:
            raise LookupError(f"Пользователь не найден: {user_id or username}")
        return user

    @staticmethod
    def _default_profile_name(user: User) -> str:
        return user.username or f"tg{user.telegram_id}"


@contextmanager
def connect(url: str | None = None, *, engine: Engine | None = None) -> Iterator[DB]:
    """Сессия с коммитом на выходе — для скриптов и ручных операций.

    В боте и бэкенде лучше создавать DB(session) поверх своей сессии.
    """
    own_engine = engine is None
    if engine is None and url is None:
        load_dotenv(override=True)
        url = database_url()
    engine = engine or create_engine(url)
    try:
        with Session(engine) as session, session.begin():
            yield DB(session)
    finally:
        if own_engine:
            engine.dispose()
