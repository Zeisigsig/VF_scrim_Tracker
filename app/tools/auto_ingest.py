"""디코 닉만으로 그날 커스텀 내전을 자동으로 찾아 적재 (OCR 없이).

내전이 끝난 직후, 참가자 디코 닉들을 주면:
  1) 각 디코 닉 → Player(discord_name) 해석.
  2) 시드 한 명의 Riot 계정으로 Henrik 최근 커스텀 매치 2개를 조회해 적재.
  3) 그 매치 로스터에 잡힌 참가자(최대 9명)는 이미 처리됐으니 목록에서 제외.
  4) 남은 사람으로 2~3을 반복 → 시드 몇 명이면 40명도 전부 커버(API 호출 최소화).

적재는 기존 ingest_match 파이프라인(Henrik 승패=절대정답, 멱등)을 그대로 쓴다.
미등록 Riot 계정은 auto_register 로 자동 처리: 발로닉이 이미 있으면 그 유저에
Riot 계정(name#tag/puuid)을 채워넣고, 완전 신규면 새 유저를 만든다(겹치면 사람이 병합).

execute() 는 결과를 dict 로 돌려주어 로컬 브라우저 페이지·VM 엔드포인트가 공용한다.

CLI 사용:
  uv run python -m app.tools.auto_ingest "디코닉1" "디코닉2" ... [--dry-run]
"""
from __future__ import annotations

import re
import sys

from sqlalchemy import select

from app import config
from app.db.models import Player, PlayerRiotAccount
from app.db.session import SessionLocal
from app.henrik.client import HenrikClient
from app.henrik.enrich import _parse_roster
from app.services import _norm_nick
from app.tools.ingest_match import _ingest_one, _kst_played_at, _map_kr, _resolve

_MATCHES_PER_SEED = 2  # 시드당 조회할 최근 커스텀 매치 수 (내전 직후 실행 전제)
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


def _custom_matches(client, name: str, tag: str) -> list[dict]:
    """계정의 최근 커스텀 매치(인라인 로스터 포함) 최대 _MATCHES_PER_SEED 개."""
    items = client.get_matches(config.HENRIK_REGION, name, tag, mode="custom")
    return [m for m in items[:_MATCHES_PER_SEED]
            if (m.get("metadata") or {}).get("match_id")]


def execute(nicks: list[str], dry_run: bool = False) -> dict:
    """시드-확장 자동 적재. 결과 dict 반환(로컬 페이지·엔드포인트 공용, 프린트 안 함).

    반환: matches[{match_id,map,played_at,saved}], new_players[], new_accounts[],
          unmatched[], no_account[], no_matches[].
    """
    session = SessionLocal()
    client = HenrikClient()

    players_before = {pid for (pid,) in session.execute(select(Player.id)).all()}
    accts_before = {
        (a.player_id, a.riot_name.lower(), a.riot_tag.lower())
        for a in session.scalars(select(PlayerRiotAccount))
    }

    remaining: dict[int, str] = {}   # player_id → 디코닉(표시용)
    unmatched: list[str] = []        # Player 로 해석 안 된 디코닉
    ambiguous: list[str] = []        # 여러 명이 걸린 디코닉(골라야 함)
    no_account: list[str] = []       # Riot 계정 미연결이라 자동검색 불가
    no_matches: list[str] = []       # 최근 커스텀 매치가 없던 시드
    matches: list[dict] = []         # 적재/확인한 매치
    seen_mid: set[str] = set()

    try:
        for nick in nicks:
            p, cands = _resolve_nick(session, nick)
            if p is not None:
                remaining[p.id] = nick
            elif cands:
                ambiguous.append(f"{nick} → " + " / ".join(c.discord_name for c in cands))
            else:
                unmatched.append(nick)

        # 시드 확장 루프: 남은 사람 중 하나를 시드로 매치를 끌어와 로스터로 dedup.
        while remaining:
            seed_pid = next(iter(remaining))
            seed_nick = remaining.pop(seed_pid)  # 시드는 항상 소진
            accts = _seed_accounts(session, seed_pid)
            if not accts:
                no_account.append(seed_nick)
                continue

            items: list[dict] = []
            for name, tag in accts:
                items = _custom_matches(client, name, tag)
                if items:
                    break  # 매치가 나온 계정 하나면 충분
            if not items:
                no_matches.append(seed_nick)
                continue

            for m in items:
                md = m.get("metadata") or {}
                mid = md["match_id"]
                if mid not in seen_mid:
                    seen_mid.add(mid)
                    saved = _ingest_one(session, client, mid, {}, dry_run,
                                        review=False, auto_register=True)
                    matches.append({
                        "match_id": mid, "map": _map_kr(m),
                        "played_at": _kst_played_at(md.get("started_at")),
                        "saved": saved is not None,
                    })
                # 이 매치 로스터(인라인)에 잡힌 batch 멤버를 remaining 에서 제거(dedup).
                for pid in _resolve(session, _parse_roster(m))[0].values():
                    remaining.pop(pid, None)

        new_players = [p.display_name for p in session.scalars(select(Player))
                       if p.id not in players_before]
        new_accounts = [
            f"{a.riot_name}#{a.riot_tag}"
            for a in session.scalars(select(PlayerRiotAccount))
            if (a.player_id, a.riot_name.lower(), a.riot_tag.lower()) not in accts_before
        ]
        return {
            "dry_run": dry_run, "matches": matches,
            "new_players": new_players, "new_accounts": new_accounts,
            "unmatched": unmatched, "ambiguous": ambiguous,
            "no_account": no_account, "no_matches": no_matches,
        }
    finally:
        client.close()
        session.close()


def _print_result(r: dict) -> None:
    tag = " (dry-run)" if r["dry_run"] else ""
    saved = sum(1 for m in r["matches"] if m["saved"])
    print(f"\n===== 자동 적재 요약{tag} =====")
    print(f"매치: {len(r['matches'])}개 (신규 저장 {saved}, 기존 {len(r['matches']) - saved})")
    for m in r["matches"]:
        mark = "저장" if m["saved"] else "기존"
        print(f"  [{mark}] {m['map']} {m['played_at']} ({m['match_id']})")
    if r["new_players"]:
        print(f"신규 유저 {len(r['new_players'])}: " + ", ".join(r["new_players"]))
    if r["new_accounts"]:
        print(f"Riot 계정 채움 {len(r['new_accounts'])}: " + ", ".join(r["new_accounts"]))
    if r["unmatched"]:
        print(f"[미해결] 디코닉 매칭 실패 {len(r['unmatched'])}: " + ", ".join(r["unmatched"]))
    if r.get("ambiguous"):
        print(f"[선택필요] 여러 명 매칭 {len(r['ambiguous'])}:")
        for a in r["ambiguous"]:
            print(f"  {a}")
    if r["no_account"]:
        print(f"[미해결] Riot 계정 미연결 {len(r['no_account'])}: " + ", ".join(r["no_account"]))
    if r["no_matches"]:
        print(f"[정보] 최근 커스텀 없음 {len(r['no_matches'])}: " + ", ".join(r["no_matches"]))


def main() -> None:
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        raise SystemExit(1)
    dry_run = "--dry-run" in argv
    nicks = [a for a in argv if a != "--dry-run"]
    _print_result(execute(nicks, dry_run))


if __name__ == "__main__":
    main()
