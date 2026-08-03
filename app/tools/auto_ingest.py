"""디코 닉만으로 그날 커스텀 내전을 자동으로 찾아 적재 (OCR 없이).

내전이 끝난 직후, 참가자 디코 닉들을 주면:
  1) 각 디코 닉 → Player(discord_name) 해석 → 입력 그룹 구성.
  2) 시드 한 명의 Riot 계정으로 Henrik 최근 표준 커스텀을 조회.
     - 로스터가 '입력한 사람만'(낯선 0명) → 바로 확정 적재.
     - 낯선(입력 안 된) 사람이 있으면 → 적재 안 하고 pending 으로 돌려 로스터를
       보여주고 확인받는다(2단계). 1명만 넣어도 동작하되, 미완성 로스터는
       새 유저 생성 전에 사람이 고른다. (Skirmish/Drift 등 비표준은 항상 제외.)
  3) 확정/확인 매치 로스터에 잡힌 참가자는 목록에서 제외(dedup).
  4) 남은 사람으로 2~3을 반복 → 시드 몇 명이면 40명도 전부 커버(API 호출 최소화).

적재는 기존 ingest_match 파이프라인(Henrik 승패=절대정답, 멱등)을 그대로 쓴다.
미등록 Riot 계정은 auto_register 로 자동 처리: 발로닉이 이미 있으면 그 유저에
Riot 계정(name#tag/puuid)을 채워넣고, 완전 신규면 새 유저를 만든다(겹치면 사람이 병합).

execute()=1단계(plan, 낯선0명 자동적재+pending 반환), ingest_ids()=2단계(고른
match_id 확정 적재). 둘 다 dict 반환 → 로컬 브라우저 페이지·VM 엔드포인트 공용.

CLI 사용:
  uv run python -m app.tools.auto_ingest "디코닉1" "디코닉2" ... [--dry-run]
  uv run python -m app.tools.auto_ingest --ingest <match_id> [<match_id> ...]  # 2단계
"""
from __future__ import annotations

import re
import sys

from sqlalchemy import select

from app import config
from app.db.models import Match, Player, PlayerRiotAccount
from app.db.session import SessionLocal
from app.henrik.client import HenrikClient
from app.henrik.enrich import _parse_roster
from app.services import _norm_nick
from app.tools.ingest_match import _ingest_one, _kst_played_at, _map_kr, _resolve

_MATCHES_PER_SEED = 6  # 시드당 후보로 볼 최근 표준 커스텀 수 (내전 직후, 여러 맵 커버)
_PREFIX = re.compile(r"^\d{2}\s+")  # 디코닉 앞 생년 2자리 접두 (예: "97 승진")


def _strip_prefix(s: str) -> str:
    return _PREFIX.sub("", s or "").strip()


def _resolve_nick(session, nick: str) -> tuple[Player | None, list[Player]]:
    """디코 닉 → (Player, 모호후보). 접두("97 ") 뗀 이름만 쳐도 매칭.

    완전 일치 → 정규화 일치 → 접두 제거 이름 일치 순. 여러 명 걸리면
    (None, 후보들) 로 돌려 호출측이 골라달라고 리포트한다.
    """
    p = session.scalar(select(Player).where(Player.discord_name == nick))
    if p is not None:
        return p, []
    players = list(session.scalars(select(Player).where(Player.discord_name.is_not(None))))

    exact = [c for c in players if _norm_nick(c.discord_name) == _norm_nick(nick)]
    if len(exact) == 1:
        return exact[0], []
    if len(exact) > 1:
        return None, exact

    base = _norm_nick(_strip_prefix(nick))
    hits = [c for c in players if _norm_nick(_strip_prefix(c.discord_name)) == base]
    if len(hits) == 1:
        return hits[0], []
    return (None, hits) if len(hits) > 1 else (None, [])


def _seed_accounts(session, player_id: int) -> list[tuple[str, str]]:
    """플레이어의 Riot 계정 (name, tag). puuid 보유 계정 먼저(검색 신뢰도)."""
    accts = session.scalars(
        select(PlayerRiotAccount)
        .where(PlayerRiotAccount.player_id == player_id)
        .order_by(PlayerRiotAccount.puuid.is_(None))
    ).all()
    return [(a.riot_name, a.riot_tag) for a in accts]


def _is_custom(m: dict) -> bool:
    """비공개 커스텀 게임인지. queue.id 는 최근 내전이 ''(빈값)이라 신뢰 불가 →
    queue.name == 'Custom Game' 로 판별한다(경쟁전=Competitive 등은 제외)."""
    q = (m.get("metadata") or {}).get("queue") or {}
    return (q.get("name") or "").strip().lower() == "custom game"


def _custom_items(client, name: str, tag: str) -> list[dict]:
    """계정의 최근 커스텀 매치(인라인 로스터 포함, match_id 있는 것만) 최신순.

    Henrik `mode='custom'` 필터는 queue.id=='custom' 만 잡는데, 최근 내전은
    queue.id 가 ''(빈값)이라 그 필터로는 통째로 누락되고 옛날 것만 반환된다.
    → 필터 없이 최근 전체 매치를 받아 queue.name=='Custom Game' 로 로컬 필터한다
    (내전 직후 실행 전제라 최근 창에 그날 내전이 들어온다)."""
    items = client.get_matches(config.HENRIK_REGION, name, tag)
    return [m for m in items
            if (m.get("metadata") or {}).get("match_id") and _is_custom(m)]


def _is_standard(m: dict) -> bool:
    """표준 5v5 커스텀만 True. Skirmish/Drift/Deathmatch 등은 제외.

    Henrik v4 metadata.queue.mode_type 이 'Standard' 인 것만 내전으로 본다
    (Deathmatch 등은 mode_type 이 다르고 로스터 인원·팀 구조도 다르다).
    """
    q = (m.get("metadata") or {}).get("queue") or {}
    return (q.get("mode_type") or "").strip().lower() == "standard"


def _mode_type(m: dict) -> str:
    return ((m.get("metadata") or {}).get("queue") or {}).get("mode_type") or "?"


def _snapshot(session) -> tuple[set, set]:
    players = {pid for (pid,) in session.execute(select(Player.id)).all()}
    accts = {
        (a.player_id, a.riot_name.lower(), a.riot_tag.lower())
        for a in session.scalars(select(PlayerRiotAccount))
    }
    return players, accts


def _diff_new(session, players_before: set, accts_before: set) -> tuple[list[str], list[str]]:
    new_players = [p.display_name for p in session.scalars(select(Player))
                   if p.id not in players_before]
    new_accounts = [
        f"{a.riot_name}#{a.riot_tag}"
        for a in session.scalars(select(PlayerRiotAccount))
        if (a.player_id, a.riot_name.lower(), a.riot_tag.lower()) not in accts_before
    ]
    return new_players, new_accounts


def _exists(session, mid: str) -> bool:
    return session.scalars(
        select(Match.id).where(Match.external_match_id == mid)
    ).first() is not None


def _roster_preview(session, roster: list[dict], mapping: dict[int, int],
                    group_ids: set[int]) -> list[dict]:
    """로스터 미리보기: 각 항목 {label, typed}. typed=이번에 입력한 사람인지."""
    out = []
    for i, r in enumerate(roster):
        pid = mapping.get(i)
        if pid is not None:
            p = session.get(Player, pid)
            label = (p.display_name if p else None) or (r["name"] or "?")
            out.append({"label": label, "typed": pid in group_ids})
        else:
            nt = f'{r["name"]}#{r["tag"]}' if r.get("tag") else (r["name"] or "?")
            out.append({"label": nt, "typed": False})  # 미등록 = 낯선
    return out


def execute(nicks: list[str], dry_run: bool = False) -> dict:
    """1단계(plan): 시드-확장으로 그날 표준 내전 후보를 찾는다.

    - 로스터가 '입력한 사람만'(낯선 0명)이면 **자동 확정 적재**(matches).
    - 낯선 사람이 있으면 적재하지 않고 **pending** 으로 돌려, 사람이 로스터를
      보고 고르게 한다(2단계 ingest_ids). 이렇게 하면 1명만 넣어도 동작하되
      미완성 로스터는 새 유저 생성 전에 확인을 받는다.

    반환: matches[{...saved}], pending[{match_id,map,played_at,mode,strangers,roster}],
          filtered[], new_players[], new_accounts[], unmatched[], ambiguous[],
          no_account[], no_matches[].
    """
    session = SessionLocal()
    client = HenrikClient()
    players_before, accts_before = _snapshot(session)

    remaining: dict[int, str] = {}   # player_id → 디코닉(표시용)
    group_ids: set[int] = set()      # 입력한 유저 전체(그룹) — 낯선 판정 기준
    unmatched: list[str] = []        # Player 로 해석 안 된 디코닉
    ambiguous: list[str] = []        # 여러 명이 걸린 디코닉(골라야 함)
    no_account: list[str] = []       # Riot 계정 미연결이라 자동검색 불가
    no_matches: list[str] = []       # 최근 커스텀 매치가 없던 시드
    matches: list[dict] = []         # 낯선 0명이라 자동 적재한 매치
    pending: list[dict] = []         # 낯선 있어 확인 대기(적재 안 함)
    filtered: list[str] = []         # 비표준이라 제외한 매치(투명성용)
    seen_mid: set[str] = set()

    def _dedup(roster: list[dict]) -> None:
        for pid in _resolve(session, roster)[0].values():
            remaining.pop(pid, None)

    try:
        for nick in nicks:
            p, cands = _resolve_nick(session, nick)
            if p is not None:
                remaining[p.id] = nick
                group_ids.add(p.id)
            elif cands:
                ambiguous.append(f"{nick} → " + " / ".join(c.discord_name for c in cands))
            else:
                unmatched.append(nick)

        while remaining:
            seed_pid = next(iter(remaining))
            seed_nick = remaining.pop(seed_pid)  # 시드는 항상 소진
            accts = _seed_accounts(session, seed_pid)
            if not accts:
                no_account.append(seed_nick)
                continue

            items: list[dict] = []
            for name, tag in accts:
                items = _custom_items(client, name, tag)
                if items:
                    break  # 매치가 나온 계정 하나면 충분
            if not items:
                no_matches.append(seed_nick)
                continue

            considered = 0
            for m in items:
                if considered >= _MATCHES_PER_SEED:
                    break  # 시드당 최근 표준 커스텀 _MATCHES_PER_SEED 개까지만
                md = m.get("metadata") or {}
                mid = md["match_id"]
                if mid in seen_mid:
                    continue
                if not _is_standard(m):  # Skirmish/Drift/Deathmatch 등은 배제(카운트 X)
                    filtered.append(f"{_map_kr(m)} {_kst_played_at(md.get('started_at'))} "
                                    f"· {_mode_type(m)} · 비표준")
                    seen_mid.add(mid)
                    continue
                seen_mid.add(mid)
                considered += 1

                roster = _parse_roster(m)
                mapping = _resolve(session, roster)[0]
                strangers = len(roster) - sum(1 for pid in mapping.values()
                                              if pid in group_ids)
                if _exists(session, mid):  # 이미 적재됨 → 커버로 보고 dedup만
                    _dedup(roster)
                    continue

                entry = {
                    "match_id": mid, "map": _map_kr(m),
                    "played_at": _kst_played_at(md.get("started_at")),
                    "mode": _mode_type(m), "strangers": strangers,
                }
                if strangers == 0:  # 입력한 사람만 → 바로 확정 적재
                    saved = _ingest_one(session, client, mid, {}, dry_run,
                                        review=False, auto_register=True)
                    matches.append({**entry, "saved": saved is not None})
                else:               # 낯선 있음 → 확인 대기(적재 안 함)
                    entry["roster"] = _roster_preview(session, roster, mapping, group_ids)
                    pending.append(entry)
                _dedup(roster)

        new_players, new_accounts = _diff_new(session, players_before, accts_before)
        return {
            "dry_run": dry_run, "matches": matches, "pending": pending,
            "filtered": filtered, "new_players": new_players, "new_accounts": new_accounts,
            "unmatched": unmatched, "ambiguous": ambiguous,
            "no_account": no_account, "no_matches": no_matches,
        }
    finally:
        client.close()
        session.close()


def ingest_ids(match_ids: list[str], dry_run: bool = False) -> dict:
    """2단계(confirm): 사람이 고른 match_id 들을 확정 적재(새 유저 auto_register).

    반환: matches[{match_id,map,played_at,saved}], new_players[], new_accounts[].
    """
    session = SessionLocal()
    client = HenrikClient()
    players_before, accts_before = _snapshot(session)
    matches: list[dict] = []
    try:
        for mid in match_ids:
            saved = _ingest_one(session, client, mid, {}, dry_run,
                                review=False, auto_register=True)
            row = session.scalars(
                select(Match).where(Match.external_match_id == mid)
            ).first()
            matches.append({
                "match_id": mid,
                "map": row.map_name if row else None,
                "played_at": row.played_at if row else None,
                "saved": saved is not None,
            })
        new_players, new_accounts = _diff_new(session, players_before, accts_before)
        return {"dry_run": dry_run, "matches": matches,
                "new_players": new_players, "new_accounts": new_accounts}
    finally:
        client.close()
        session.close()


def _print_result(r: dict) -> None:
    tag = " (dry-run)" if r["dry_run"] else ""
    saved = sum(1 for m in r["matches"] if m["saved"])
    print(f"\n===== 자동 적재 요약{tag} =====")
    print(f"자동 적재(낯선 0명): {len(r['matches'])}개 (신규 저장 {saved}, 기존 {len(r['matches']) - saved})")
    for m in r["matches"]:
        mark = "저장" if m["saved"] else "기존"
        print(f"  [{mark}] {m['map']} {m['played_at']} ({m['match_id']})")
    for m in r.get("pending", []):
        typed = sum(1 for x in m["roster"] if x["typed"])
        print(f"[확인필요] {m['map']} {m['played_at']} · 낯선 {m['strangers']}명 "
              f"(입력 {typed}명) ({m['match_id']})")
        for x in m["roster"]:
            print(f"    {'입력' if x['typed'] else '낯선'}  {x['label']}")
        print(f"    → 적재하려면: uv run python -m app.tools.auto_ingest --ingest {m['match_id']}")
    if r["new_players"]:
        print(f"신규 유저 {len(r['new_players'])}: " + ", ".join(r["new_players"]))
    if r["new_accounts"]:
        print(f"Riot 계정 채움 {len(r['new_accounts'])}: " + ", ".join(r["new_accounts"]))
    if r["unmatched"]:
        print(f"[디코닉 미매칭·적재는 가능] {', '.join(r['unmatched'])}"
              f" — 디코닉만 안 붙었을 뿐 발로닉으로는 이미 등록된 경우가 많음."
              f" 위 [확인필요] 매치를 적재하면 로스터의 이 사람들도 발로닉으로"
              f" 자동 연결/생성됩니다(적재를 막지 않음).")
    if r.get("ambiguous"):
        print(f"[선택필요] 여러 명 매칭 {len(r['ambiguous'])}:")
        for a in r["ambiguous"]:
            print(f"  {a}")
    if r["no_account"]:
        print(f"[미해결] Riot 계정 미연결 {len(r['no_account'])}: " + ", ".join(r["no_account"]))
    if r["no_matches"]:
        print(f"[정보] 최근 커스텀 없음 {len(r['no_matches'])}: " + ", ".join(r["no_matches"]))
    if r.get("filtered"):
        print(f"[제외] 비표준/무관 매치 {len(r['filtered'])}:")
        for f in r["filtered"]:
            print(f"  {f}")


def main() -> None:
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        raise SystemExit(1)
    dry_run = "--dry-run" in argv
    argv = [a for a in argv if a != "--dry-run"]
    if "--ingest" in argv:  # 2단계: 확인 대기 match_id 들을 확정 적재
        ids = [a for a in argv if a != "--ingest"]
        r = ingest_ids(ids, dry_run)
        saved = sum(1 for m in r["matches"] if m["saved"])
        print(f"확정 적재: {len(r['matches'])}개 (신규 저장 {saved})")
        if r["new_players"]:
            print(f"신규 유저 {len(r['new_players'])}: " + ", ".join(r["new_players"]))
        if r["new_accounts"]:
            print(f"Riot 계정 채움 {len(r['new_accounts'])}: " + ", ".join(r["new_accounts"]))
        return
    _print_result(execute(argv, dry_run))


if __name__ == "__main__":
    main()
