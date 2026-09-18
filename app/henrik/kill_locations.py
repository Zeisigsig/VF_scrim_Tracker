"""킬 좌표 저장 — 개인 페이지 미니맵 히트맵용.

Henrik 매치 상세 kills[] 한 건에서:
  - ``location``           = 희생자가 쓰러진 지점 (희생자는 player_locations 에 없다)
  - ``player_locations[]`` = 킬 순간 생존자 위치 → 여기서 killer 좌표를 집는다
를 뽑아 kill_events 에 넣는다. 경기 행을 먼저 지우므로 재호출해도 멱등.

원시 게임 좌표를 그대로 저장한다 — 미니맵 비율 변환(valorant-api 계수)은 표시
시점에 하므로 계수가 갱신돼도 재백필이 필요 없다.

head_to_head.populate_match 가 이미 받아둔 상세를 넘겨주므로 추가 API 호출 없음.
"""
from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.models import KillEvent, Match


def _xy(loc: dict | None) -> tuple[int | None, int | None]:
    if not isinstance(loc, dict):
        return None, None
    x, y = loc.get("x"), loc.get("y")
    if x is None or y is None:
        return None, None
    return int(x), int(y)


def store_kill_locations(
    session: Session, match: Match, detail: dict, puuid_pid: dict[str, int]
) -> int:
    """상세의 킬 좌표를 kill_events 에 (재)저장. 저장한 건수 반환.

    puuid_pid 는 head_to_head 가 만든 {puuid: player_id} 맵(로스터 기준).
    killer/victim 둘 다 미등록이면 우리 쪽에서 쓸 일이 없으므로 건너뛴다.
    """
    map_id = ((detail.get("metadata") or {}).get("map") or {}).get("id")
    if map_id:
        match.map_uuid = map_id

    session.execute(delete(KillEvent).where(KillEvent.match_id == match.id))

    n = 0
    for k in detail.get("kills") or []:
        killer_puuid = (k.get("killer") or {}).get("puuid")
        victim_puuid = (k.get("victim") or {}).get("puuid")
        killer_id = puuid_pid.get(killer_puuid) if killer_puuid else None
        victim_id = puuid_pid.get(victim_puuid) if victim_puuid else None
        if killer_id is None and victim_id is None:
            continue

        vx, vy = _xy(k.get("location"))
        kx = ky = kview = None
        for pl in k.get("player_locations") or []:
            if ((pl.get("player") or {}).get("puuid")) == killer_puuid:
                kx, ky = _xy(pl.get("location"))
                view = pl.get("view_radians")
                kview = float(view) if view is not None else None
                break
        if (kx is None or ky is None) and (vx is None or vy is None):
            continue

        session.add(KillEvent(
            match_id=match.id, round=k.get("round"),
            killer_id=killer_id, victim_id=victim_id,
            killer_x=kx, killer_y=ky, victim_x=vx, victim_y=vy,
            killer_view=kview,
        ))
        n += 1
    return n
