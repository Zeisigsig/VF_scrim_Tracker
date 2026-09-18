"""add kill_events table + matches.map_uuid (미니맵 킬/데스 히트맵)

Revision ID: 0006_kill_events
Revises: 0005_app_settings
Create Date: 2026-09-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_kill_events"
down_revision: Union[str, None] = "0005_app_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("matches", sa.Column("map_uuid", sa.Text(), nullable=True))
    op.create_table(
        "kill_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("round", sa.Integer(), nullable=True),
        sa.Column("killer_id", sa.Integer(), nullable=True),
        sa.Column("victim_id", sa.Integer(), nullable=True),
        sa.Column("killer_x", sa.Integer(), nullable=True),
        sa.Column("killer_y", sa.Integer(), nullable=True),
        sa.Column("victim_x", sa.Integer(), nullable=True),
        sa.Column("victim_y", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["killer_id"], ["players.id"]),
        sa.ForeignKeyConstraint(["victim_id"], ["players.id"]),
    )
    op.create_index("ix_kill_events_match_id", "kill_events", ["match_id"])
    op.create_index("ix_kill_events_killer_id", "kill_events", ["killer_id"])
    op.create_index("ix_kill_events_victim_id", "kill_events", ["victim_id"])


def downgrade() -> None:
    op.drop_index("ix_kill_events_victim_id", table_name="kill_events")
    op.drop_index("ix_kill_events_killer_id", table_name="kill_events")
    op.drop_index("ix_kill_events_match_id", table_name="kill_events")
    op.drop_table("kill_events")
    op.drop_column("matches", "map_uuid")
