"""add players.departed (서버 이탈 소프트 표시)

Revision ID: 0003_player_departed
Revises: 0002_player_discord_name
Create Date: 2026-07-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_player_departed"
down_revision: Union[str, None] = "0002_player_discord_name"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "players",
        sa.Column("departed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("players", "departed")
