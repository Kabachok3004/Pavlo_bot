"""initial schema

Revision ID: e9b7170bd6f6
Revises: 
Create Date: 2026-08-15 20:22:04.111244+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e9b7170bd6f6'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('users',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('telegram_id', sa.BigInteger(), nullable=False),
    sa.Column('username', sa.String(length=32), nullable=True),
    sa.Column('parent_id', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('parent_id <> id', name=op.f('ck_users_parent_not_self')),
    sa.ForeignKeyConstraint(['parent_id'], ['users.id'], name=op.f('fk_users_parent_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('telegram_id', name=op.f('uq_users_telegram_id'))
    )
    op.create_index(op.f('ix_users_parent_id'), 'users', ['parent_id'], unique=False)
    op.create_table('subscriptions',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('payer_id', sa.BigInteger(), nullable=True),
    sa.Column('plan', sa.Enum('trial', 'month', 'quarter', 'year', name='subscription_plan', native_enum=False, length=32), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('expires_at > started_at', name=op.f('ck_subscriptions_period_valid')),
    sa.ForeignKeyConstraint(['payer_id'], ['users.id'], name=op.f('fk_subscriptions_payer_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_subscriptions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_subscriptions'))
    )
    op.create_index(op.f('ix_subscriptions_payer_id'), 'subscriptions', ['payer_id'], unique=False)
    op.create_index('ix_subscriptions_user_id_expires_at', 'subscriptions', ['user_id', 'expires_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_subscriptions_user_id_expires_at', table_name='subscriptions')
    op.drop_index(op.f('ix_subscriptions_payer_id'), table_name='subscriptions')
    op.drop_table('subscriptions')
    op.drop_index(op.f('ix_users_parent_id'), table_name='users')
    op.drop_table('users')
