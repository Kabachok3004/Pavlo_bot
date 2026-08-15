"""subscription profile name

Revision ID: 355a3f9e3a2d
Revises: e9b7170bd6f6
Create Date: 2026-08-15 21:31:11.576580+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '355a3f9e3a2d'
down_revision: Union[str, None] = 'e9b7170bd6f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('subscriptions', sa.Column('profile_name', sa.String(length=64), nullable=True))
    # Существующим подпискам ставим username владельца.
    op.execute(
        """
        UPDATE subscriptions s
        SET profile_name = COALESCE(u.username, 'tg' || u.telegram_id)
        FROM users u
        WHERE u.id = s.user_id AND s.profile_name IS NULL
        """
    )
    op.alter_column('subscriptions', 'profile_name', nullable=False)
    op.create_index(op.f('ix_subscriptions_profile_name'), 'subscriptions', ['profile_name'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_subscriptions_profile_name'), table_name='subscriptions')
    op.drop_column('subscriptions', 'profile_name')
