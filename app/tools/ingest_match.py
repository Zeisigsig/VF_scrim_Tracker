"""Henrik 매치 ID로 커스텀게임을 트래커에 직접 적재 (스크린샷 누락 경기 복구용).

내전 데이터를 못 받아 트래커에 빠진 경기를, Henrik 매치 상세(teams[].won =
승패 절대정답)에서 그대로 만들어 확정 저장한다. 웹 확정과 동일한
save_and_rate() 파이프라인을 재사용하므로 TACR/OpenSkill/헤드투헤드까지 일관.

사용:
  uv run python -m app.tools.ingest_match <match_id> [<match_id> ...] \
      [--link "Riot Name#Tag:player_id"] [--dry-run]

미등록 Riot 계정이 하나라도 있으면 저장하지 않고 목록만 출력한다.
--link 로 기존 player 에 계정(puuid 포함)을 연결한 뒤 다시 실행하면 된다.
external_match_id 로 중복을 막으므로 재실행해도 안전(멱등).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app import config
from app.db.models import Match, PlayerRiotAccount
from app.db.session import SessionLocal
from app.henrik.client import HenrikClient
from app.henrik.enrich import _parse_roster, _team_rounds
from app.henrik.head_to_head import populate_match
from app.services import ConfirmedRow, save_and_rate

_KST = timezone(timedelta(hours=9))

_MAP_EN_TO_KR = {
    "bind": "바인드", "haven": "헤이븐", "split": "스플릿", "ascent": "어센트",
    "icebox": "아이스박스", "breeze": "브리즈", "fracture": "프랙처", "pearl": "펄",
    "lotus": "로터스", "sunset": "선셋", "summit": "서밋", "abyss": "어비스",
    "corrode": "코로드",
}


def _kst_played_at(started_at: str | None) -> str:
    if not started_at:
        return datetime.now(_KST).replace(tzinfo=None).isoformat(timespec="seconds")
    dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    return dt.astimezone(_KST).replace(tzinfo=None).isoformat(timespec="seconds")


def _map_kr(match: dict) -> str | None:
    md = match.get("metadata") or {}
    mp = md.get("map")
    name = mp.get("name") if isinstance(mp, dict) else mp
    if not name:
        return None
    return _MAP_EN_TO_KR.get(name.lower(), name)


def _apply_links(session, roster: list[dict], links: dict[tuple[str, str], int]) -> None:
    """--link 로 지정한 (name,tag)→player_id 를 PlayerRiotAccount 로 등록(puuid 는 로스터에서)."""
    for r in roster:
        key = ((r["name"] or "").lower(), (r["tag"] or "").lower())
        pid = links.get(key)
        if pid is None:
            continue
        exists = session.scalars(
            select(PlayerRiotAccount).where(
                PlayerRiotAccount.player_id == pid,
                PlayerRiotAccount.riot_name == r["name"],
                PlayerRiotAccount.riot_tag == r["tag"],
            )
        ).first()
        if exists is None:
            session.add(PlayerRiotAccount(
                player_id=pid, riot_name=r["name"], riot_tag=r["tag"],
                puuid=r["puuid"],
            ))
    session.flush()


def _resolve(session, roster: list[dict]) -> tuple[dict[int, int], list[dict]]:
    """로스터 인덱스 → player_id 매핑과 미해결 로스터 목록. puuid 우선, 없으면 name#tag."""
    accts = list(session.scalars(select(PlayerRiotAccount)))
    by_puuid = {a.puuid: a.player_id for a in accts if a.puuid}
    by_nt = {(a.riot_name.lower(), a.riot_tag.lower()): a.player_id for a in accts}
    mapping: dict[int, int] = {}
    unresolved: list[dict] = []
    for i, r in enumerate(roster):
        pid = by_puuid.get(r["puuid"]) or by_nt.get(
            ((r["name"] or "").lower(), (r["tag"] or "").lower())
        )
        if pid is None:
            unresolved.append(r)
        else:
            mapping[i] = pid
    return mapping, unresolved


def _ingest_one(session, client, hid: str, links, dry_run: bool) -> None:
    if session.scalars(
        select(Match).where(Match.external_match_id == hid)
    ).first() is not None:
        print(f"[skip] 이미 적재됨: {hid}")
        return

    match_raw = client.get_match(config.HENRIK_REGION, hid)
    roster = _parse_roster(match_raw)
    tr = _team_rounds(match_raw)
    if len(tr) != 2:
        print(f"[fail] 팀 라운드 파싱 실패({hid}): teams={tr}")
        return

    _apply_links(session, roster, links)
    mapping, unresolved = _resolve(session, roster)
    md = match_raw.get("metadata") or {}
    label = f"{_map_kr(match_raw)} {_kst_played_at(md.get('started_at'))}"
    if unresolved:
        print(f"[중단] {label} ({hid}) — 미등록 Riot 계정 {len(unresolved)}명:")
        for r in unresolved:
            print(f"    {r['name']}#{r['tag']}  (puuid={r['puuid']}, agent={r['agent_en']})")
        print('    → --link "Name#Tag:player_id" 로 기존 player 에 연결 후 재실행')
        raise SystemExit(2)

    team_ids = sorted(tr.keys())
    tid_a, tid_b = team_ids[0], team_ids[1]
    rounds_total = tr[tid_a] + tr[tid_b]

    rows: list[ConfirmedRow] = []
    for i, r in enumerate(roster):
        agent_kr = config.agent_kr_from_en(r["agent_en"]) or (r["agent_en"] or "?")
        acs = round(r["score"] / rounds_total) if (r["score"] and rounds_total) else 0
        rows.append(ConfirmedRow(
            player_id=mapping[i],
            team="A" if r["team_id"] == tid_a else "B",
            agent=agent_kr,
            role=config.AGENT_ROLE.get(agent_kr, "initiator"),
            acs=acs, kills=r["k"] or 0, deaths=r["d"] or 0, assists=r["a"] or 0,
        ))

    match = Match(
        external_match_id=hid,
        played_at=_kst_played_at(md.get("started_at")),
        map_name=_map_kr(match_raw),
        team_a_rounds=tr[tid_a], team_b_rounds=tr[tid_b],
        extraction_raw={"henrik_match_id": hid},
        status="pending",
    )
    session.add(match)
    session.flush()

    winner = "A" if tr[tid_a] > tr[tid_b] else ("B" if tr[tid_b] > tr[tid_a] else "무")
    print(f"[적재] {label} ({hid})")
    print(f"    라운드 A({tid_a}):{tr[tid_a]}  B({tid_b}):{tr[tid_b]}  → 승팀 {winner}")
    for i, r in enumerate(roster):
        row = rows[i]
        print(f"    {row.team}  {r['name']}#{r['tag']:<14} {row.agent:<8} "
              f"{row.kills}/{row.deaths}/{row.assists}  acs={row.acs}  pid={row.player_id}")

    if dry_run:
        session.rollback()
        print("    (dry-run: 저장 안 함)")
        return

    save_and_rate(session, match, rows)
    session.commit()
    try:
        populate_match(session, match, client)
        session.commit()
    except Exception as e:
        session.rollback()
        print(f"    (h2h 갱신 실패, 무해: {type(e).__name__})")
    print("    저장 완료.")


def _parse_links(args: list[str]) -> dict[tuple[str, str], int]:
    links: dict[tuple[str, str], int] = {}
    for spec in args:
        rid, _, pid = spec.rpartition(":")
        name, _, tag = rid.partition("#")
        links[(name.strip().lower(), tag.strip().lower())] = int(pid)
    return links


def main() -> None:
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        raise SystemExit(1)
    dry_run = "--dry-run" in argv
    argv = [a for a in argv if a != "--dry-run"]
    link_specs: list[str] = []
    match_ids: list[str] = []
    it = iter(argv)
    for a in it:
        if a == "--link":
            link_specs.append(next(it))
        else:
            match_ids.append(a)
    links = _parse_links(link_specs)

    session = SessionLocal()
    client = HenrikClient()
    try:
        for hid in match_ids:
            _ingest_one(session, client, hid, links, dry_run)
    finally:
        client.close()
        session.close()


if __name__ == "__main__":
    main()
