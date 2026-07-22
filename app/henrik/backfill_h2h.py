"""확정 경기의 유저간 킬 구도(헤드투헤드)를 Henrik 상세로 일괄 백필.

henrik_match_id 가 extraction_raw 에 남은 확정 경기를 순회하며 상세를 조회해
head_to_head_kills 를 채운다. 이미 채워진 경기는 건너뛴다(--force 로 재계산).

--recover: henrik id 가 없는 옛 경기(enrichment 도입 전 확정분)에 대해 확정된
MatchPlayer 스탯으로 지문매칭을 돌려 henrik id 를 역추적한 뒤 채운다. Henrik
무료 티어 히스토리 깊이를 벗어난 오래된 경기는 못 찾을 수 있다(스킵).

Henrik free tier = 30 req/60s. HenrikClient 가 호출 간 2.2s 페이싱을 강제한다.

실행: uv run python -m app.henrik.backfill_h2h [--recover] [--force]
"""
from __future__ import annotations

import sys

from sqlalchemy import func, select

from app import config
from app.db.models import HeadToHeadKill, Match, MatchPlayer, Player
from app.db.session import SessionLocal, init_db
from app.henrik.client import HenrikClient
from app.henrik.enrich import Enricher
from app.henrik.head_to_head import populate_match
from app.ingest.schemas import ExtractedRow, ExtractionResult


def _has_henrik_id(match: Match) -> bool:
    return bool((match.extraction_raw or {}).get("henrik_match_id"))


def _recover_id(session, match: Match, enricher: Enricher) -> str | None:
    """확정 MatchPlayer 스탯으로 지문매칭해 henrik match id 역추적. 못 찾으면 None.

    확정값은 수기 검증돼 OCR 원본보다 정확하므로 매칭 신뢰도가 높다.
    """
    mps = session.scalars(
        select(MatchPlayer).where(MatchPlayer.match_id == match.id)
    ).all()
    rows = []
    for mp in mps:
        p = session.get(Player, mp.player_id)
        rows.append(ExtractedRow(
            nickname=p.display_name if p else "", agent_kr=mp.agent,
            team_color="", team=mp.team,
            acs=mp.acs, kills=mp.kills, deaths=mp.deaths, assists=mp.assists,
        ))
    result = ExtractionResult(rows=rows, map_name=match.map_name)
    enricher.enrich(session, result, match.played_at)  # 실패 시 henrik_match_id=None
    return result.henrik_match_id


def run(recover: bool = False, force: bool = False) -> int:
    if not config.HENRIK_API_KEY:
        print("HENRIK_API_KEY 미설정 — .env 확인 필요.", file=sys.stderr)
        return 1
    init_db()

    session = SessionLocal()
    done = skipped = empty = failed = recovered = notfound = 0
    try:
        matches = session.scalars(
            select(Match).where(Match.status == "confirmed").order_by(Match.played_at)
        ).all()
        client = HenrikClient()
        # 백필 역추적은 참가자 전원(최대 10명)을 시드로 시도 — 첫 성공에서 멈춤.
        # (라이브 업로드는 기본 3명 유지: rate limit 절약.)
        enricher = Enricher(client=client, max_seeds=10) if recover else None
        try:
            for m in matches:
                if not _has_henrik_id(m):
                    if not recover:
                        continue
                    try:
                        hid = _recover_id(session, m, enricher)
                    except Exception as e:
                        session.rollback()
                        print(f"FAIL(역추적) #{m.id}: {e}", file=sys.stderr)
                        failed += 1
                        continue
                    if not hid:
                        print(f"역추적 실패(히스토리 밖?) #{m.id}")
                        notfound += 1
                        continue
                    raw = dict(m.extraction_raw or {})
                    raw["henrik_match_id"] = hid
                    m.extraction_raw = raw
                    session.commit()
                    recovered += 1
                    print(f"역추적 OK #{m.id} → {hid}")

                has = session.scalar(
                    select(func.count()).select_from(HeadToHeadKill)
                    .where(HeadToHeadKill.match_id == m.id)
                )
                if has and not force:
                    print(f"skip (이미 채워짐): #{m.id}")
                    skipped += 1
                    continue
                try:
                    n = populate_match(session, m, client)
                    session.commit()
                except Exception as e:
                    session.rollback()
                    print(f"FAIL #{m.id}: {e}", file=sys.stderr)
                    failed += 1
                    continue
                if n:
                    print(f"OK  #{m.id}: {n} 쌍")
                    done += 1
                else:
                    print(f"빈 결과 #{m.id} (등록 유저 킬 없음)")
                    empty += 1
        finally:
            client.close()
    finally:
        session.close()

    if recover:
        print(f"\n역추적: 복구 {recovered} · 못찾음 {notfound}")
    print(f"완료: 채움 {done} · 스킵 {skipped} · 빈결과 {empty} · 실패 {failed}")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    raise SystemExit(run(recover="--recover" in args, force="--force" in args))
