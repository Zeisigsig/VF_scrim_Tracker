"""닉네임 → player 매칭 (스펙 §5.4).

정확 일치는 자동, 불일치는 유사도 상위 후보 + 신규 옵션을 검토 화면에 제시.
DB 접근은 얇은 조회만 하고, 유사도 계산은 순수 함수로 분리한다.
"""
from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Player, PlayerAlias

SIMILARITY_TOP_N = 3


@dataclass
class Candidate:
    player_id: int
    display_name: str
    score: float  # 0–100


@dataclass
class MatchOutcome:
    nickname: str
    exact_player_id: int | None  # 정확 일치 시 자동 매칭된 player id
    candidates: list[Candidate]  # 불일치 시 유사 후보 상위 N


def rank_candidates(nickname: str, known: list[tuple[int, str]]) -> list[Candidate]:
    """(player_id, display_name) 목록에서 유사도 상위 N 후보. 순수 함수."""
    scored = [
        Candidate(pid, name, fuzz.WRatio(nickname, name))
        for pid, name in known
    ]
    scored.sort(key=lambda c: c.score, reverse=True)
    return scored[:SIMILARITY_TOP_N]


def match_nickname(session: Session, nickname: str) -> MatchOutcome:
    # 1) alias 정확 일치 → 자동
    alias = session.scalar(select(PlayerAlias).where(PlayerAlias.alias == nickname))
    if alias is not None:
        return MatchOutcome(nickname, alias.player_id, [])

    # 2) 불일치 → 유사 후보
    known = [
        (p.id, p.display_name)
        for p in session.scalars(select(Player)).all()
    ]
    return MatchOutcome(nickname, None, rank_candidates(nickname, known))


def register_alias(session: Session, player_id: int, alias: str) -> None:
    """확정 시 새 alias 저장 → 다음부터 자동 매칭."""
    existing = session.scalar(select(PlayerAlias).where(PlayerAlias.alias == alias))
    if existing is None:
        session.add(PlayerAlias(player_id=player_id, alias=alias))
