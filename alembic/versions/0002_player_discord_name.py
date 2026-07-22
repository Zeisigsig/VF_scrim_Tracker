"""add players.discord_name (표시용 디스코드 서버 닉)

Revision ID: 0002_player_discord_name
Revises: 0001_initial
Create Date: 2026-07-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_player_discord_name"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("players", sa.Column("discord_name", sa.Text))


def downgrade() -> None:
    op.drop_column("players", "discord_name")
