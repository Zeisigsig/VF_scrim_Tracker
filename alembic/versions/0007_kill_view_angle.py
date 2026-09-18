"""add kill_events.killer_view (킬 순간 조준 각도)

Revision ID: 0007_kill_view_angle
Revises: 0006_kill_events
Create Date: 2026-09-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_kill_view_angle"
down_revision: Union[str, None] = "0006_kill_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("kill_events", sa.Column("killer_view", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("kill_events", "killer_view")
