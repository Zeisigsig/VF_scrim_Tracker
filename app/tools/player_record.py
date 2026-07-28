"""특정 유저의 확정 경기별 승패 기록을 출력 (승패 오류 확인용).

사용: uv run python -m app.tools.player_record <닉네임>
발로닉(display_name)·디코닉(discord_name) 부분일치·대소문자 무관으로 찾는다.
"""
from __future__ import annotations

import sys

from sqlalchemy import or_, select

from app.db.models import Match, MatchPlayer, Player
from app.db.session import SessionLocal


def _find_players(session, needle: str) -> list[Player]:
    like = f"%{needle}%"
    stmt = select(Player).where(
        or_(Player.display_name.ilike(like), Player.discord_name.ilike(like))
    )
    return list(session.scalars(stmt))


def _print_record(session, player: Player) -> None:
    rows = list(
        session.scalars(
            select(MatchPlayer)
            .join(Match, MatchPlayer.match_id == Match.id)
            .where(MatchPlayer.player_id == player.id)
            .where(Match.status == "confirmed")
            .order_by(Match.played_at)
        )
    )
    print(f"\n=== {player.label}  (player_id={player.id}) ===")
    print(f"확정 경기 수: {len(rows)}")
    print(f"{'match':>6} {'played_at':<20} {'map':<10} {'요원':<8} {'팀':<3} "
          f"{'A':>3} {'B':>3} {'결과':<4}")
    wins = losses = draws = skipped = 0
    for mp in rows:
        m = mp.match
        a, b = m.team_a_rounds, m.team_b_rounds
        if a is None or b is None:
            result = "?라운드없음"
            skipped += 1
        elif a == b:
            result = "무"
            draws += 1
        else:
            my = a if mp.team == "A" else b
            opp = b if mp.team == "A" else a
            if my > opp:
                result = "승"
                wins += 1
            else:
                result = "패"
                losses += 1
        print(f"{m.id:>6} {str(m.played_at)[:19]:<20} {str(m.map_name or '')[:10]:<10} "
              f"{str(mp.agent or '')[:8]:<8} {mp.team:<3} {str(a if a is not None else '-'):>3} "
              f"{str(b if b is not None else '-'):>3} {result:<4}")
    print(f"\n합계: {wins}승 {losses}패"
          + (f" {draws}무" if draws else "")
          + (f" (라운드미입력 {skipped})" if skipped else ""))


def main() -> None:
    if len(sys.argv) < 2:
        print("사용: uv run python -m app.tools.player_record <닉네임>")
        raise SystemExit(1)
    needle = sys.argv[1]
    session = SessionLocal()
    try:
        players = _find_players(session, needle)
        if not players:
            print(f"'{needle}' 로 매칭되는 유저가 없습니다.")
            raise SystemExit(1)
        if len(players) > 1:
            print(f"'{needle}' 매칭 유저 {len(players)}명 — 모두 출력합니다.")
        for p in players:
            _print_record(session, p)
    finally:
        session.close()


if __name__ == "__main__":
    main()
