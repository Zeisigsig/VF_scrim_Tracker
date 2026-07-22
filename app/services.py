"""DB 결합 오케스트레이션 (순수 rating 함수와 DB 사이의 접착층).

스펙의 §2 디렉토리 목록에는 없지만, "확정 → 저장 → 레이팅 계산" (스펙 §5.1)과
전체 재계산 (스펙 §3, §8)을 한 곳에 모으기 위해 추가한 모듈.
순수 계산은 app.rating 에 두고, 여기서는 조회/저장/조립만 한다.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import config
from app.db.models import (
    Match,
    MatchPlayer,
    MatchRating,
    Player,
    PlayerAlias,
    PlayerTier,
    SkillRating,
)
from app.rating import tier as tier_mod
from app.rating.openskill_engine import SkillState, initial_state, update_match
from app.rating.tacr import PlayerStat, compute_match, implied_tier


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- 선수/티어 조회 ------------------------------------------------------

def _norm_nick(name: str) -> str:
    """닉네임 비교용 정규화. 유니코드(NFC)·대소문자·양끝/중복 공백 차이를 흡수한다.
    저장된 발로닉과 재확인용 비교에만 쓰고, 실제 저장값(원문)은 바꾸지 않는다."""
    s = unicodedata.normalize("NFC", name)
    s = re.sub(r"\s+", " ", s).strip()
    return s.casefold()


def resolve_existing_player(session: Session, display_name: str) -> Player | None:
    """닉네임으로 기존 유저를 찾되 없으면 None(생성하지 않음).

    별칭·발로닉 완전 일치 → 정규화(공백·대소문자·유니코드) 일치 순으로 확인한다.
    검토 화면의 '이 닉이 누구로 매칭되는가' 미리보기와 get_or_create_player 가 공유."""
    alias = session.scalar(select(PlayerAlias).where(PlayerAlias.alias == display_name))
    if alias is not None:
        return session.get(Player, alias.player_id)
    existing = session.scalar(select(Player).where(Player.display_name == display_name))
    if existing is not None:
        return existing

    target = _norm_nick(display_name)
    for al in session.scalars(select(PlayerAlias)).all():
        if _norm_nick(al.alias) == target:
            return session.get(Player, al.player_id)
    for p in session.scalars(select(Player)).all():
        if _norm_nick(p.display_name) == target:
            return p
    return None


def get_or_create_player(session: Session, display_name: str) -> Player:
    """같은 별칭 또는 같은 발로닉이 이미 있으면 그 유저를 재사용(중복 생성 방지).

    깨진 OCR 닉을 사람이 올바르게 고쳐 입력해도 완전 일치가 아니면(공백·대소문자·
    유니코드 조합 차이) 신규로 새 유저가 만들어져 중복이 생기던 문제를 막기 위해,
    정확 일치가 없으면 정규화 일치로 한 번 더 확인한 뒤에야 신규 생성한다."""
    existing = resolve_existing_player(session, display_name)
    if existing is not None:
        return existing
    p = Player(display_name=display_name, created_at=_now())
    session.add(p)
    session.flush()
    return p


def merge_players(session: Session, source_id: int, target_id: int) -> None:
    """중복 생성된 source 유저를 target 유저로 흡수한다(경기 기록 이전).

    깨진 OCR 닉을 '신규'로 잘못 확정해 같은 사람의 Player 가 둘 생긴 경우 정리용.
    파생값(레이팅·implied 티어)은 recompute 로 재생성되므로 여기서는 원시 소유권만 옮긴다.
    커밋과 recompute_all() 은 호출측에서 수행한다."""
    if source_id == target_id:
        raise ValueError("source 와 target 이 동일합니다.")
    source = session.get(Player, source_id)
    target = session.get(Player, target_id)
    if source is None or target is None:
        raise ValueError("병합 대상 유저를 찾을 수 없습니다.")

    # 1) 경기 기록(MatchPlayer) 이전. (match_id, player_id) 유니크라 같은 경기에
    #    target 이 이미 있으면 충돌 → source 행을 버린다(동일 인물 중복 입력 방어).
    target_match_ids = {
        mid for (mid,) in session.execute(
            select(MatchPlayer.match_id).where(MatchPlayer.player_id == target_id)
        ).all()
    }
    for mp in session.scalars(
        select(MatchPlayer).where(MatchPlayer.player_id == source_id)
    ).all():
        if mp.match_id in target_match_ids:
            session.delete(mp)
        else:
            mp.player_id = target_id

    # 2) 별칭 이전 + source 발로닉을 target 별칭으로 등록(향후 OCR 자동매칭).
    for al in session.scalars(
        select(PlayerAlias).where(PlayerAlias.player_id == source_id)
    ).all():
        al.player_id = target_id
    if source.display_name != target.display_name:
        existing_alias = session.scalar(
            select(PlayerAlias).where(PlayerAlias.alias == source.display_name)
        )
        if existing_alias is None:
            session.add(PlayerAlias(player_id=target_id, alias=source.display_name))
        else:
            existing_alias.player_id = target_id

    # 3) 티어: implied 는 파생이라 버리고, manual/henrik 은 target 에 같은 source 가
    #    없을 때만 옮긴다(수동 설정 유실 방지). 나머지는 삭제.
    target_sources = {
        s for (s,) in session.execute(
            select(PlayerTier.source).where(PlayerTier.player_id == target_id)
        ).all()
    }
    for t in session.scalars(
        select(PlayerTier).where(PlayerTier.player_id == source_id)
    ).all():
        if t.source != "implied" and t.source not in target_sources:
            t.player_id = target_id
            target_sources.add(t.source)
        else:
            session.delete(t)

    # 4) source 의 SkillRating(파생) 제거 후 source Player 삭제.
    dup_skill = session.get(SkillRating, source_id)
    if dup_skill is not None:
        session.delete(dup_skill)
    session.flush()
    session.delete(source)
    session.flush()


def delete_player_permanently(session: Session, player_id: int) -> None:
    """유저를 관련 기록과 함께 완전 삭제(하드 삭제). 나간 유저 정리용.

    경기 참여기록(MatchPlayer)과 그 레이팅·별칭·티어·SkillRating 을 모두 지운 뒤
    Player 를 삭제한다. 참여했던 경기는 남되 인원이 줄어드니 파생값은 recompute 로
    재생성해야 한다(커밋·recompute_all 은 호출측)."""
    player = session.get(Player, player_id)
    if player is None:
        raise ValueError("삭제 대상 유저를 찾을 수 없습니다.")

    mp_ids = [
        mid for (mid,) in session.execute(
            select(MatchPlayer.id).where(MatchPlayer.player_id == player_id)
        ).all()
    ]
    if mp_ids:
        session.execute(delete(MatchRating).where(MatchRating.match_player_id.in_(mp_ids)))
    session.execute(delete(MatchPlayer).where(MatchPlayer.player_id == player_id))
    session.execute(delete(PlayerAlias).where(PlayerAlias.player_id == player_id))
    session.execute(delete(PlayerTier).where(PlayerTier.player_id == player_id))
    session.execute(delete(SkillRating).where(SkillRating.player_id == player_id))
    session.flush()
    session.delete(player)
    session.flush()


def _rank_prior(session: Session, player_id: int) -> tier_mod.RankPrior:
    """player_tiers 이력에서 현재/최고 랭크 prior 조립."""
    tiers = session.scalars(
        select(PlayerTier)
        .where(PlayerTier.player_id == player_id)
        .order_by(PlayerTier.recorded_at.desc())
    ).all()
    current = None
    n_games = 0
    peak = None
    for t in tiers:
        if current is None and t.source in ("manual", "henrik_current"):
            current = t.tier_value
            n_games = t.ranked_games_in_act or 0
        if peak is None and t.source == "henrik_peak":
            peak = t.tier_value
    return tier_mod.RankPrior(current=current, n_games=n_games, peak=peak)


def compute_tier_effs(session: Session, player_ids: list[int]) -> dict[int, tuple[float, str]]:
    """로비 전체 tier_eff 를 2-패스로 계산 (언랭은 로비 중앙값, 스펙 §4.2)."""
    prelim: dict[int, tuple[float, str]] = {}
    for pid in player_ids:
        skill = session.get(SkillRating, pid)
        mu = skill.mu if skill else None
        games = skill.games_counted if skill else 0
        val, label = tier_mod.effective_tier(mu, games, _rank_prior(session, pid))
        prelim[pid] = (val, label)

    ranked_vals = [v for v, lab in prelim.values() if lab != "unranked"]
    if ranked_vals:
        median = tier_mod.lobby_median_tier(ranked_vals)
        for pid, (val, lab) in prelim.items():
            if lab == "unranked":
                prelim[pid] = (median, "unranked")
    return prelim


# --- 확정 저장 + 레이팅 -------------------------------------------------

@dataclass
class ConfirmedRow:
    player_id: int
    team: str
    agent: str
    role: str
    acs: int
    kills: int
    deaths: int
    assists: int
    econ: int | None = None
    first_kills: int | None = None
    plants: int | None = None
    defuses: int | None = None
    kast: float | None = None
    adr: float | None = None
    first_deaths: int | None = None
    headshot_pct: float | None = None


def save_and_rate(session: Session, match: Match, rows: list[ConfirmedRow]) -> None:
    """MatchPlayer 저장 → tier_eff/TACR 계산 → MatchRating/OpenSkill/implied 갱신."""
    player_ids = [r.player_id for r in rows]
    tier_effs = compute_tier_effs(session, player_ids)

    match.status = "confirmed"
    stats: list[PlayerStat] = []
    mp_records: list[MatchPlayer] = []
    for r in rows:
        te, _label = tier_effs[r.player_id]
        mp = MatchPlayer(
            match_id=match.id, player_id=r.player_id, team=r.team, agent=r.agent,
            role=r.role, acs=r.acs, kills=r.kills, deaths=r.deaths, assists=r.assists,
            econ_rating=r.econ, first_kills=r.first_kills, plants=r.plants,
            defuses=r.defuses, kast=r.kast, adr=r.adr, first_deaths=r.first_deaths,
            headshot_pct=r.headshot_pct,
        )
        session.add(mp)
        mp_records.append(mp)
        stats.append(
            PlayerStat(
                team=r.team, role=r.role, tier_eff=te, acs=r.acs, kills=r.kills,
                deaths=r.deaths, assists=r.assists, econ=float(r.econ or 0),
                first_kills=r.first_kills or 0, plants=r.plants or 0,
                defuses=r.defuses or 0, kast=r.kast, adr=r.adr,
                first_deaths=r.first_deaths,
            )
        )
    session.flush()

    rounds = None
    if match.team_a_rounds is not None and match.team_b_rounds is not None:
        rounds = match.team_a_rounds + match.team_b_rounds
    results = compute_match(stats, rounds=rounds)

    for mp, res in zip(mp_records, results):
        session.add(
            MatchRating(
                match_player_id=mp.id, params_version=config.PARAMS_VERSION,
                tier_eff_used=res.tier_eff_used, expected_acs=res.expected_acs,
                tacr=res.tacr, display_score=res.display_score, computed_at=_now(),
            )
        )

    _update_openskill(session, match, rows, tier_effs)
    _record_implied(session, rows, stats)


def _update_openskill(
    session: Session, match: Match, rows: list[ConfirmedRow],
    tier_effs: dict[int, tuple[float, str]],
) -> None:
    """팀 승패로 OpenSkill μ/σ 업데이트 (무승부/미입력은 스킵, 스펙 §4.6)."""
    if match.team_a_rounds is None or match.team_b_rounds is None:
        return
    if match.team_a_rounds == match.team_b_rounds:
        return  # 무승부 스킵

    states: dict[int, SkillState] = {}
    for r in rows:
        skill = session.get(SkillRating, r.player_id)
        if skill is not None:
            states[r.player_id] = SkillState(skill.mu, skill.sigma)
        else:
            te, label = tier_effs[r.player_id]
            states[r.player_id] = initial_state(te, label)

    a_ids = [r.player_id for r in rows if r.team == "A"]
    b_ids = [r.player_id for r in rows if r.team == "B"]
    a_won = match.team_a_rounds > match.team_b_rounds
    new_a, new_b = update_match(
        [states[i] for i in a_ids], [states[i] for i in b_ids], a_won
    )
    for pid, st in list(zip(a_ids, new_a)) + list(zip(b_ids, new_b)):
        skill = session.get(SkillRating, pid)
        if skill is None:
            session.add(
                SkillRating(player_id=pid, mu=st.mu, sigma=st.sigma,
                            games_counted=1, updated_at=_now())
            )
        else:
            skill.mu = st.mu
            skill.sigma = st.sigma
            skill.games_counted += 1
            skill.updated_at = _now()


def _record_implied(session: Session, rows: list[ConfirmedRow], stats: list[PlayerStat]) -> None:
    """implied tier 를 검증용으로 기록 (tier_eff 에는 미사용, 스펙 §4.6)."""
    for r, imp in zip(rows, implied_tier(stats)):
        session.add(
            PlayerTier(player_id=r.player_id, source="implied", tier_value=imp,
                       ranked_games_in_act=None, recorded_at=_now())
        )
