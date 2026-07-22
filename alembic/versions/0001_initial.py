"""initial schema (Phase 1)

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "players",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("puuid", sa.Text, unique=True),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("riot_name", sa.Text),
        sa.Column("riot_tag", sa.Text),
        sa.Column("created_at", sa.Text, nullable=False),
    )
    op.create_table(
        "player_aliases",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("player_id", sa.Integer, sa.ForeignKey("players.id"), nullable=False),
        sa.Column("alias", sa.Text, nullable=False, unique=True),
    )
    op.create_table(
        "player_tiers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("player_id", sa.Integer, sa.ForeignKey("players.id"), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("tier_value", sa.Float, nullable=False),
        sa.Column("ranked_games_in_act", sa.Integer),
        sa.Column("recorded_at", sa.Text, nullable=False),
    )
    op.create_table(
        "matches",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("external_match_id", sa.Text, unique=True),
        sa.Column("played_at", sa.Text, nullable=False),
        sa.Column("map_name", sa.Text),
        sa.Column("team_a_rounds", sa.Integer),
        sa.Column("team_b_rounds", sa.Integer),
        sa.Column("screenshot_path", sa.Text),
        sa.Column("extraction_raw", sa.JSON),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.Text, nullable=False),
    )
    op.create_table(
        "match_players",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("match_id", sa.Integer, sa.ForeignKey("matches.id"), nullable=False),
        sa.Column("player_id", sa.Integer, sa.ForeignKey("players.id"), nullable=False),
        sa.Column("team", sa.String(1), nullable=False),
        sa.Column("agent", sa.Text, nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("acs", sa.Integer, nullable=False),
        sa.Column("kills", sa.Integer, nullable=False),
        sa.Column("deaths", sa.Integer, nullable=False),
        sa.Column("assists", sa.Integer, nullable=False),
        sa.Column("econ_rating", sa.Integer),
        sa.Column("first_kills", sa.Integer),
        sa.Column("plants", sa.Integer),
        sa.Column("defuses", sa.Integer),
        sa.Column("kast", sa.Float),
        sa.Column("adr", sa.Float),
        sa.Column("first_deaths", sa.Integer),
        sa.Column("headshot_pct", sa.Float),
        sa.UniqueConstraint("match_id", "player_id"),
    )
    op.create_table(
        "match_ratings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("match_player_id", sa.Integer, sa.ForeignKey("match_players.id"), nullable=False),
        sa.Column("params_version", sa.Text, nullable=False),
        sa.Column("tier_eff_used", sa.Float, nullable=False),
        sa.Column("expected_acs", sa.Float, nullable=False),
        sa.Column("tacr", sa.Float, nullable=False),
        sa.Column("display_score", sa.Float, nullable=False),
        sa.Column("computed_at", sa.Text, nullable=False),
    )
    op.create_table(
        "skill_ratings",
        sa.Column("player_id", sa.Integer, sa.ForeignKey("players.id"), primary_key=True),
        sa.Column("mu", sa.Float, nullable=False),
        sa.Column("sigma", sa.Float, nullable=False),
        sa.Column("games_counted", sa.Integer, nullable=False, server_default="0"),
        sa.Column("updated_at", sa.Text, nullable=False),
    )


def downgrade() -> None:
    for t in (
        "skill_ratings", "match_ratings", "match_players", "matches",
        "player_tiers", "player_aliases", "players",
    ):
        op.drop_table(t)
