"""유저간 킬 구도(헤드투헤드) 집계 — 라이벌/천적 기능.

Henrik 매치 상세의 kills[] 는 킬마다 killer/victim puuid 를 담는다. 이를
등록 유저(PlayerRiotAccount) 로 매핑해 경기별 (killer→victim) 킬 수를
head_to_head_kills 에 저장한다. OCR 행 배정과 무관하게 riot 계정으로만
매핑하므로 더 정확하고 결합도가 낮다.

원칙: Henrik 실패(rate limit·네트워크)는 확정을 막지 않는다(best-effort).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app import config
from app.db.models import (
    HeadToHeadKill,
    Match,
    PlayerRiotAccount,
    PlayerTier,
)
from app.henrik.client import HenrikClient

# 랭킹 가드/판정 규칙 (사용자 확정 2026-07-21)
# 티어는 '차이 크기'가 아니라 '방향'만 본다:
#   라이벌 = 상대가 나와 동티어 or 상위(tier_diff ≤ 0). 위를 상대로 대등하게 맞섬.
#   천적   = 상대가 나와 동티어 or 하위(tier_diff ≥ 0)인데 나를 더 잡음.
MIN_ENCOUNTERS = 3       # 함께 이만큼 판 이상 등장해야 랭킹에 올림(표본 가드)
RIVAL_MARGIN = 2         # |A→B − B→A| ≤ 2 면 라이벌(팽팽)
NEMESIS_MARGIN = 3       # (상대→나) − (나→상대) ≥ 3 이면 천적


# --- 집계(쓰기) ---------------------------------------------------------

def _kill_pairs(detail: dict) -> dict[tuple[str, str], int]:
    """상세 kills[] → (killer_puuid, victim_puuid) 별 킬 수. 자살/팀킬 무관, 동일 puuid 제외."""
    out: dict[tuple[str, str], int] = defaultdict(int)
    for k in detail.get("kills") or []:
        killer = (k.get("killer") or {}).get("puuid")
        victim = (k.get("victim") or {}).get("puuid")
        if killer and victim and killer != victim:
            out[(killer, victim)] += 1
    return dict(out)


def _puuid_to_player(session: Session, detail: dict) -> dict[str, int]:
    """상세 로스터의 puuid → 등록 player_id.

    kills[] 는 puuid 만 주므로 로스터의 (puuid, name, tag) 로 브릿지한다.
    PlayerRiotAccount 를 puuid 우선, 없으면 name#tag(소문자) 로 조회.
    """
    accts = session.scalars(select(PlayerRiotAccount)).all()
    by_puuid = {a.puuid: a.player_id for a in accts if a.puuid}
    by_nametag = {
        (a.riot_name.lower(), a.riot_tag.lower()): a.player_id for a in accts
    }
    out: dict[str, int] = {}
    for p in detail.get("players") or []:
        pu = p.get("puuid")
        if not pu:
            continue
        pid = by_puuid.get(pu)
        if pid is None:
            pid = by_nametag.get(
                ((p.get("name") or "").lower(), (p.get("tag") or "").lower())
            )
        if pid is not None:
            out[pu] = pid
    return out


def populate_match(session: Session, match: Match, client: HenrikClient) -> int:
    """확정 경기의 헤드투헤드 킬을 (재)계산해 저장. 저장한 쌍 수 반환.

    henrik_match_id 는 match.extraction_raw 에 남아있다(확정 후에도 유지).
    경기 행을 먼저 지우고 다시 넣으므로 재호출해도 멱등.
    """
    raw = match.extraction_raw or {}
    hid = raw.get("henrik_match_id")
    if not hid:
        return 0
    detail = client.get_match(config.HENRIK_REGION, hid)
    puuid_pid = _puuid_to_player(session, detail)
    agg: dict[tuple[int, int], int] = defaultdict(int)
    for (kp, vp), n in _kill_pairs(detail).items():
        ki, vi = puuid_pid.get(kp), puuid_pid.get(vp)
        if ki is not None and vi is not None and ki != vi:
            agg[(ki, vi)] += n
    session.execute(
        delete(HeadToHeadKill).where(HeadToHeadKill.match_id == match.id)
    )
    for (ki, vi), n in agg.items():
        session.add(
            HeadToHeadKill(match_id=match.id, killer_id=ki, victim_id=vi, kills=n)
        )
    return len(agg)


# --- 판정(읽기) ---------------------------------------------------------

_TIER_NAMES = list(config.TIER_TABLE.keys())  # 아이언0 … 레디언트8


def _tier_band(session: Session, player_id: int) -> int | None:
    """최신 manual 티어 → 밴드 인덱스(아이언0..레디언트8). 미설정이면 None."""
    t = session.scalars(
        select(PlayerTier)
        .where(PlayerTier.player_id == player_id, PlayerTier.source == "manual")
        .order_by(PlayerTier.recorded_at.desc())
    ).first()
    if t is None:
        return None
    return _TIER_NAMES.index(config.tier_name(t.tier_value))


@dataclass
class Encounter:
    opponent_id: int
    my_kills: int       # 내가 상대를 잡은 수
    their_kills: int    # 상대가 나를 잡은 수
    encounters: int     # 함께 등장한(킬 데이터 있는) 경기 수
    tier_diff: int      # 내 티어밴드 − 상대 티어밴드 (양수=내가 상위, 음수=내가 하위)


def _encounters(session: Session, player_id: int) -> list[Encounter]:
    """player_id 와 킬을 주고받은 상대별 집계. 티어 미설정 상대는 제외."""
    my_band = _tier_band(session, player_id)
    if my_band is None:
        return []

    # 내가 잡은 수 / 나를 잡은 수
    my = dict(session.execute(
        select(HeadToHeadKill.victim_id, func.sum(HeadToHeadKill.kills))
        .where(HeadToHeadKill.killer_id == player_id)
        .group_by(HeadToHeadKill.victim_id)
    ).all())
    their = dict(session.execute(
        select(HeadToHeadKill.killer_id, func.sum(HeadToHeadKill.kills))
        .where(HeadToHeadKill.victim_id == player_id)
        .group_by(HeadToHeadKill.killer_id)
    ).all())

    # 함께 등장한(킬 데이터 있는) 경기 수 — 양방향 행을 opp 별로 합쳐 distinct 경기 수를 센다.
    match_ids: dict[int, set] = defaultdict(set)
    enc_pairs = session.execute(
        select(HeadToHeadKill.match_id, HeadToHeadKill.killer_id, HeadToHeadKill.victim_id)
        .where(or_(
            HeadToHeadKill.killer_id == player_id,
            HeadToHeadKill.victim_id == player_id,
        ))
    ).all()
    for mid, ki, vi in enc_pairs:
        opp = vi if ki == player_id else ki
        match_ids[opp].add(mid)

    out: list[Encounter] = []
    for opp in set(my) | set(their):
        opp_band = _tier_band(session, opp)
        if opp_band is None:
            continue
        out.append(Encounter(
            opponent_id=opp,
            my_kills=int(my.get(opp, 0)),
            their_kills=int(their.get(opp, 0)),
            encounters=len(match_ids.get(opp, ())),
            tier_diff=my_band - opp_band,
        ))
    return out


def relationships(session: Session, player_id: int) -> dict[str, list[Encounter]]:
    """player_id 의 라이벌·도전자·천적·사냥감 목록. 모두 함께 3판+.

    한 쌍은 서로의 페이지에 다른 라벨로 뜬다(상호적):
    - 라이벌: 상대가 동티어 or 상위(tier_diff ≤ 0) & |내킬 − 상대킬| ≤ 2.
      위 티어를 상대로 대등하게 맞서는 관계.
    - 도전자: 상대가 하위(tier_diff ≥ 1) & |내킬 − 상대킬| ≤ 2.
      라이벌의 반대편 — 아래 티어인데 감히 나와 대등하게 맞서는 상대.
    - 천적: 상대가 동티어 or 하위(tier_diff ≥ 0) & (상대킬 − 내킬) ≥ 3.
      나보다 낮거나 같은 티어인데 나를 더 잡는 상대.
    - 사냥감: 상대가 상위(tier_diff ≤ -1) & (내킬 − 상대킬) ≥ 3.
      천적의 반대편(업셋) — 상위 티어인데도 내가 더 잡은 상대. 고양감.
    """
    rivals: list[Encounter] = []
    challengers: list[Encounter] = []
    nemeses: list[Encounter] = []
    prey: list[Encounter] = []
    for e in _encounters(session, player_id):
        if e.encounters < MIN_ENCOUNTERS:
            continue
        even = abs(e.my_kills - e.their_kills) <= RIVAL_MARGIN
        if even and e.tier_diff <= 0:
            rivals.append(e)
        elif even and e.tier_diff >= 1:
            challengers.append(e)
        elif e.tier_diff >= 0 and (e.their_kills - e.my_kills) >= NEMESIS_MARGIN:
            nemeses.append(e)
        elif e.tier_diff <= -1 and (e.my_kills - e.their_kills) >= NEMESIS_MARGIN:
            prey.append(e)
    rivals.sort(key=lambda e: (-(e.my_kills + e.their_kills), -e.encounters))
    challengers.sort(key=lambda e: (-(e.my_kills + e.their_kills), -e.encounters))
    nemeses.sort(key=lambda e: -(e.their_kills - e.my_kills))
    prey.sort(key=lambda e: -(e.my_kills - e.their_kills))
    return {
        "rivals": rivals,
        "challengers": challengers,
        "nemeses": nemeses,
        "prey": prey,
    }
