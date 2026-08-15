from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class SubscriptionPlan(str, enum.Enum):
    """Тип подписки. Новый тариф — просто новое значение здесь + миграция CHECK."""

    TRIAL = "trial"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


# native_enum=False -> VARCHAR + CHECK вместо PG-типа: добавить тариф проще, чем ALTER TYPE.
plan_type = Enum(
    SubscriptionPlan,
    name="subscription_plan",
    native_enum=False,
    length=32,
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


def _utcnow_column() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    """Пользователь бота. Создаётся при первом сообщении."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    username: Mapped[str | None] = mapped_column(String(32))
    # Владелец группы, оплачивающий подписку за этого пользователя.
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = _utcnow_column()

    parent: Mapped[User | None] = relationship(
        back_populates="children", remote_side="User.id"
    )
    children: Mapped[list[User]] = relationship(back_populates="parent")
    subscriptions: Mapped[list[Subscription]] = relationship(
        back_populates="user",
        foreign_keys="Subscription.user_id",
        cascade="all, delete-orphan",
        order_by="Subscription.expires_at.desc()",
    )

    __table_args__ = (CheckConstraint("parent_id <> id", name="parent_not_self"),)


class Subscription(Base):
    """Один оплаченный период. История: продление — новая строка, старые не трогаем."""

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE")
    )
    # Кто оплатил. NULL — оплатил сам пользователь.
    payer_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    plan: Mapped[SubscriptionPlan] = mapped_column(plan_type)
    # Имя профиля VPN. Обычно совпадает с username, но задаётся отдельно.
    profile_name: Mapped[str] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _utcnow_column()

    user: Mapped[User] = relationship(back_populates="subscriptions", foreign_keys=[user_id])
    payer: Mapped[User | None] = relationship(foreign_keys=[payer_id])

    __table_args__ = (
        CheckConstraint("expires_at > started_at", name="period_valid"),
        # Основной запрос бота: активная подписка пользователя.
        Index("ix_subscriptions_user_id_expires_at", "user_id", "expires_at"),
    )
