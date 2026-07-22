"""전 경기 match_ratings 및 OpenSkill 재계산 (스펙 §3, §8).

파라미터(config)를 바꾼 뒤 과거 경기 전체를 현재 PARAMS_VERSION 으로 재계산한다.
저장된 원시값(match_players)만 사용하며, 스크린샷/추출 원본은 건드리지 않는다.

실행: python -m app.calibration.recompute
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select

from app import config
from app.db.models import Match, MatchPlayer, MatchRating, SkillRating
from app.db.session import SessionLocal
from app.services import ConfirmedRow, save_and_rate


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def recompute_all() -> int:
    """확정 경기를 시간순으로 재계산. 반환값: 재계산한 경기 수."""
    session = SessionLocal()
    try:
        # 기존 파생값 초기화 (원시값은 유지)
        session.execute(delete(MatchRating))
        session.execute(delete(SkillRating))
        # implied tier 도 파생값이므로 정리 후 재기록
        from app.db.models import PlayerTier
        session.execute(delete(PlayerTier).where(PlayerTier.source == "implied"))

        matches = session.scalars(
            select(Match).where(Match.status == "confirmed").order_by(Match.played_at)
        ).all()

        count = 0
        for match in matches:
            mps = session.scalars(
                select(MatchPlayer).where(MatchPlayer.match_id == match.id)
            ).all()
            rows = [
                ConfirmedRow(
                    player_id=mp.player_id, team=mp.team, agent=mp.agent, role=mp.role,
                    acs=mp.acs, kills=mp.kills, deaths=mp.deaths, assists=mp.assists,
                    econ=mp.econ_rating, first_kills=mp.first_kills, plants=mp.plants,
                    defuses=mp.defuses, kast=mp.kast, adr=mp.adr,
                    first_deaths=mp.first_deaths, headshot_pct=mp.headshot_pct,
                )
                for mp in mps
            ]
            # save_and_rate 는 새 MatchPlayer 를 추가하므로, 여기서는 기존 것을 지우고
            # 재사용하기 위해 기존 MatchPlayer 를 삭제한 뒤 재생성한다.
            session.execute(delete(MatchPlayer).where(MatchPlayer.match_id == match.id))
            session.flush()
            save_and_rate(session, match, rows)
            count += 1

        session.commit()
        return count
    finally:
        session.close()


if __name__ == "__main__":
    n = recompute_all()
    print(f"[recompute] PARAMS_VERSION={config.PARAMS_VERSION}: {n}개 경기 재계산 완료")
