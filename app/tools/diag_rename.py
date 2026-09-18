"""리네임(예: 그리드→Tofu) 적재 누락 진단.

VM 정본 DB(data/scrim.db)에서, 어떤 사람이 인게임 닉을 바꾼 뒤 자동적재가
그 기록을 (A)기존 유저로 흡수했는지, (B)아예 적재 안 했는지, 혹은
(C)별도 신규 유저로 만들어 병합만 남았는지를 가려준다.

실행(VM):
    uv run python -m app.tools.diag_rename 그리드 고가선 Tofu
    (인자=검색 힌트. 생략 시 기본 힌트 사용.)
"""
from __future__ import annotations

import sys

from sqlalchemy import select

from app.db.models import Match, MatchPlayer, Player, PlayerAlias, PlayerRiotAccount
from app.db.session import SessionLocal


def _accounts(session, pid: int) -> list[PlayerRiotAccount]:
    return list(session.scalars(
        select(PlayerRiotAccount).where(PlayerRiotAccount.player_id == pid)
    ))


def _match_count(session, pid: int) -> int:
    return len(list(session.scalars(
        select(MatchPlayer.id).where(MatchPlayer.player_id == pid)
    )))


def _recent_matches(session, pid: int, limit: int = 12) -> list[Match]:
    mids = [m for (m,) in session.execute(
        select(MatchPlayer.match_id).where(MatchPlayer.player_id == pid)
    ).all()]
    if not mids:
        return []
    rows = list(session.scalars(
        select(Match).where(Match.id.in_(mids)).order_by(Match.played_at.desc())
    ))
    return rows[:limit]


def _find_players(session, hints: list[str]) -> list[Player]:
    found: dict[int, Player] = {}
    all_players = list(session.scalars(select(Player)))
    aliases = list(session.scalars(select(PlayerAlias)))
    for h in hints:
        hl = h.lower()
        for p in all_players:
            fields = [p.display_name, p.discord_name, p.riot_name]
            if any(f and hl in f.lower() for f in fields):
                found[p.id] = p
        for al in aliases:
            if al.alias and hl in al.alias.lower():
                pp = session.get(Player, al.player_id)
                if pp:
                    found[pp.id] = pp
    return list(found.values())


def _find_accounts(session, hints: list[str]) -> list[PlayerRiotAccount]:
    out = []
    for a in session.scalars(select(PlayerRiotAccount)):
        if any(h.lower() in (a.riot_name or "").lower() for h in hints):
            out.append(a)
    return out


def main() -> None:
    hints = sys.argv[1:] or ["그리드", "고가선", "Tofu", "tofu"]
    print(f"검색 힌트: {', '.join(hints)}\n")
    session = SessionLocal()
    try:
        players = _find_players(session, hints)
        accts = _find_accounts(session, hints)

        print("===== 힌트에 걸린 플레이어 =====")
        if not players:
            print("  (없음)")
        for p in players:
            cnt = _match_count(session, p.id)
            print(f"\n[player id={p.id}] 표시명={p.display_name!r}  "
                  f"디코닉={p.discord_name!r}  경기수={cnt}")
            pas = _accounts(session, p.id)
            if not pas:
                print("    Riot 계정: (없음) — puuid 매칭 불가")
            for a in pas:
                pu = "puuid✔" if a.puuid else "puuid✘(없음)"
                print(f"    Riot: {a.riot_name}#{a.riot_tag}  [{pu}]")
            print("    최근 매치:")
            for m in _recent_matches(session, p.id):
                print(f"      {m.played_at}  {m.map_name}  "
                      f"({(m.external_match_id or '')[:8]})  status={m.status}")

        print("\n===== 힌트 이름의 Riot 계정(계정 테이블 전체 검색) =====")
        if not accts:
            print("  (없음)")
        for a in accts:
            owner = session.get(Player, a.player_id)
            pu = "puuid✔" if a.puuid else "puuid✘"
            print(f"  {a.riot_name}#{a.riot_tag} [{pu}] → player id={a.player_id} "
                  f"({owner.display_name if owner else '?'})")

        # ---- 판정 힌트 ----
        print("\n===== 판정 힌트 =====")
        tofu_players = [p for p in players
                        if p.display_name and "tofu" in p.display_name.lower()]
        tofu_accts = [a for a in accts if "tofu" in (a.riot_name or "").lower()]
        if tofu_players or tofu_accts:
            print("  → 경우 C: 'Tofu'가 별도 유저/계정으로 이미 등록됨. "
                  "병합(merge_players)만 하면 됨.")
        else:
            print("  → 'Tofu'로 등록된 유저/계정 없음. 다음으로 A/B 구분:")
            print("     · 리네임한 사람의 기존 계정에 puuid✔ 이고, 그날 매치가 "
                  "위 '최근 매치'에 있으면 → 경우 A(기존 유저로 흡수됨, 병합 불필요).")
            print("     · puuid✘ 이고 그날 매치가 안 보이면 → 경우 B(pending 미확인으로 "
                  "적재 자체 안 됨). 자동적재 다시 돌려 '확인 필요'에서 체크·적재.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
