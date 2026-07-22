"""add users table (로그인 계정)

Revision ID: 0004_users
Revises: 0003_player_departed
Create Date: 2026-07-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_users"
down_revision: Union[str, None] = "0003_player_departed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "must_change_password", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("username"),
        sa.UniqueConstraint("player_id"),
    )


def downgrade() -> None:
    op.drop_table("users")
