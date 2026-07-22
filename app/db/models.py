"""SQLAlchemy 2.x 모델 (스펙 §3).

원칙: 원시값을 저장하고 파생값은 재계산 가능한 형태로 저장 (파라미터 튜닝 후
과거 경기 전체 재계산 가능해야 함).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)  # 발로란트 인게임 닉 (OCR 매칭 키)
    discord_name: Mapped[str | None] = mapped_column(Text)  # 디스코드 서버 닉 (표시 전용)
    # 서버 이탈 표시(소프트). 기록·랭크는 유지하되 이름만 '[나간 유저]'로 표시, 시즌 초기화 때 정리.
    departed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=_now)

    aliases: Mapped[list["PlayerAlias"]] = relationship(back_populates="player")
    tiers: Mapped[list["PlayerTier"]] = relationship(back_populates="player")
    riot_accounts: Mapped[list["PlayerRiotAccount"]] = relationship(
        back_populates="player", cascade="all, delete-orphan"
    )

    @property
    def label(self) -> str:
        """페이지 표시용 이름: 이탈 시 '[나간 유저]', 디코 닉 설정 시 '디코닉 (발로닉)', 아니면 발로닉."""
        if self.departed:
            return "[나간 유저]"
        if self.discord_name:
            return f"{self.discord_name} ({self.display_name})"
        return self.display_name


class PlayerAlias(Base):
    __tablename__ = "player_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    alias: Mapped[str] = mapped_column(Text, nullable=False, unique=True)

    player: Mapped[Player] = relationship(back_populates="aliases")


class PlayerRiotAccount(Base):
    """플레이어의 Riot 계정(name#tag/puuid). 부계 대비 한 유저에 여러 개 가능."""
    __tablename__ = "player_riot_accounts"
    __table_args__ = (UniqueConstraint("player_id", "riot_name", "riot_tag"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    riot_name: Mapped[str] = mapped_column(Text, nullable=False)
    riot_tag: Mapped[str] = mapped_column(Text, nullable=False)
    puuid: Mapped[str | None] = mapped_column(Text, unique=True)  # Henrik에서 확보 시

    player: Mapped[Player] = relationship(back_populates="riot_accounts")


class PlayerTier(Base):
    __tablename__ = "player_tiers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    # 'manual' | 'henrik_current' | 'henrik_peak' | 'implied'
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    tier_value: Mapped[float] = mapped_column(nullable=False)
    ranked_games_in_act: Mapped[int | None] = mapped_column(Integer)
    recorded_at: Mapped[str] = mapped_column(Text, nullable=False, default=_now)

    player: Mapped[Player] = relationship(back_populates="tiers")


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_match_id: Mapped[str | None] = mapped_column(Text, unique=True)  # Phase 2
    played_at: Mapped[str] = mapped_column(Text, nullable=False, default=_now)
    map_name: Mapped[str | None] = mapped_column(Text)
    team_a_rounds: Mapped[int | None] = mapped_column(Integer)
    team_b_rounds: Mapped[int | None] = mapped_column(Integer)
    screenshot_path: Mapped[str | None] = mapped_column(Text)
    extraction_raw: Mapped[dict | None] = mapped_column(JSON)
    # 검토 플로우 상태: 'pending'(검토 대기) | 'confirmed'(확정 저장) (스펙 §5.1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=_now)

    players: Mapped[list["MatchPlayer"]] = relationship(
        back_populates="match", cascade="all, delete-orphan"
    )


class MatchPlayer(Base):
    __tablename__ = "match_players"
    __table_args__ = (UniqueConstraint("match_id", "player_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    team: Mapped[str] = mapped_column(String(1), nullable=False)  # 'A' | 'B'
    agent: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    acs: Mapped[int] = mapped_column(Integer, nullable=False)
    kills: Mapped[int] = mapped_column(Integer, nullable=False)
    deaths: Mapped[int] = mapped_column(Integer, nullable=False)
    assists: Mapped[int] = mapped_column(Integer, nullable=False)
    econ_rating: Mapped[int | None] = mapped_column(Integer)
    first_kills: Mapped[int | None] = mapped_column(Integer)
    plants: Mapped[int | None] = mapped_column(Integer)
    defuses: Mapped[int | None] = mapped_column(Integer)
    # Phase 2 enrichment (nullable)
    kast: Mapped[float | None] = mapped_column()
    adr: Mapped[float | None] = mapped_column()
    first_deaths: Mapped[int | None] = mapped_column(Integer)
    headshot_pct: Mapped[float | None] = mapped_column()

    match: Mapped[Match] = relationship(back_populates="players")
    player: Mapped[Player] = relationship()
    rating: Mapped["MatchRating | None"] = relationship(
        back_populates="match_player", cascade="all, delete-orphan", uselist=False
    )


class MatchRating(Base):
    __tablename__ = "match_ratings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_player_id: Mapped[int] = mapped_column(
        ForeignKey("match_players.id"), nullable=False
    )
    params_version: Mapped[str] = mapped_column(Text, nullable=False)
    tier_eff_used: Mapped[float] = mapped_column(nullable=False)
    expected_acs: Mapped[float] = mapped_column(nullable=False)
    tacr: Mapped[float] = mapped_column(nullable=False)
    display_score: Mapped[float] = mapped_column(nullable=False)
    computed_at: Mapped[str] = mapped_column(Text, nullable=False, default=_now)

    match_player: Mapped[MatchPlayer] = relationship(back_populates="rating")


class HeadToHeadKill(Base):
    """경기별 유저간 킬 구도 (Henrik 상세 kills[] 에서 집계). 라이벌/천적 표시용.

    경기 단위 저장이라 재확정·재백필해도 그 경기 행만 지우고 다시 넣으면 멱등.
    전체 매트릭스는 (killer_id, victim_id) 로 SUM.
    """
    __tablename__ = "head_to_head_kills"
    __table_args__ = (UniqueConstraint("match_id", "killer_id", "victim_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), nullable=False
    )
    killer_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    victim_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    kills: Mapped[int] = mapped_column(Integer, nullable=False)


class User(Base):
    """로그인 계정. 어드민이 참가자(디스코드 닉 보유) 기준으로 사전 발급한다.

    아이디는 발급 시점의 디스코드 닉으로 고정. 어드민 권한은 저장하지 않고
    config.admin_usernames() (env) 로 매 요청 판정한다 — env 가 유일 출처.
    """
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(Text, nullable=False, unique=True)  # 디스코드 닉(고정)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id"), nullable=False, unique=True
    )
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    # 첫 로그인(또는 어드민 초기화 후) 비밀번호 강제 변경 플래그.
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=_now)

    player: Mapped[Player] = relationship()


class SkillRating(Base):
    __tablename__ = "skill_ratings"

    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), primary_key=True)
    mu: Mapped[float] = mapped_column(nullable=False)
    sigma: Mapped[float] = mapped_column(nullable=False)
    games_counted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default=_now)
