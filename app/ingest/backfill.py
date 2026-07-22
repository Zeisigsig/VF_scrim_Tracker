"""기존 data/screenshots 의 명명 규칙 파일을 일괄로 검토 대기(pending) 경기로 적재.

파일명 규칙: {YYYYMMDD}_{맵}_{session:02d}_{game:02d}.png
  - {맵} 생략 시 ({YYYYMMDD}_{session}_{game}.png) map_name 은 None → 검토/홈에서 입력

추출은 로컬 OCR(RapidOCR)이라 API 키가 필요 없다.
이미 적재된 파일(같은 screenshot_path 가 DB 에 존재)은 건너뛴다(idempotent).
확정/레이팅은 하지 않는다 — 스펙 "무검토 저장 없음" 에 따라 /review 에서 사람이 확정한다.

실행: uv run python -m app.ingest.backfill
"""
from __future__ import annotations

import re
import sys

from sqlalchemy import select

from app import config
from app.db.models import Match
from app.db.session import SessionLocal, init_db
from app.ingest.extractor import extract_scoreboard

# {YYYYMMDD}_{맵}_{세션}_{판}.png  (맵은 밑줄 없는 임의 문자열)
_PATTERN_MAP = re.compile(r"^(\d{4})(\d{2})(\d{2})_([^_]+)_(\d{2})_(\d{2})\.png$")
# {YYYYMMDD}_{세션}_{판}.png  (맵 생략 → None)
_PATTERN_NOMAP = re.compile(r"^(\d{4})(\d{2})(\d{2})_(\d{2})_(\d{2})\.png$")


def _parse(name: str) -> tuple[str, str, str, str | None, str, str] | None:
    """파일명 → (y, mo, d, map_name, session_no, game_no) 또는 None."""
    m = _PATTERN_MAP.match(name)
    if m:
        y, mo, d, map_name, session_no, game_no = m.groups()
        return y, mo, d, map_name, session_no, game_no
    m = _PATTERN_NOMAP.match(name)
    if m:
        y, mo, d, session_no, game_no = m.groups()
        return y, mo, d, None, session_no, game_no
    return None


def _played_at(y: str, mo: str, d: str, session_no: str, game_no: str) -> str:
    """날짜 + 세션(시)/판(분) 으로 시간순 정렬이 보장되는 ISO 타임스탬프."""
    return f"{y}-{mo}-{d}T{session_no}:{game_no}:00+00:00"


def run() -> int:
    init_db()
    files = sorted(
        p for p in config.SCREENSHOT_DIR.iterdir()
        if p.is_file() and _parse(p.name) is not None
    )
    if not files:
        print("적재할 대상 파일이 없습니다 (명명 규칙과 일치하는 png 없음).")
        return 0

    session = SessionLocal()
    created = skipped = failed = 0
    try:
        existing = set(session.scalars(select(Match.screenshot_path)).all())
        for path in files:
            rel = str(path.relative_to(config.BASE_DIR))
            if rel in existing:
                print(f"skip (이미 적재됨): {path.name}")
                skipped += 1
                continue

            y, mo, d, map_name, session_no, game_no = _parse(path.name)
            try:
                result = extract_scoreboard(path)
            except Exception as e:  # 추출 실패해도 나머지 파일은 계속 진행
                print(f"FAIL (추출 오류): {path.name} — {e}", file=sys.stderr)
                failed += 1
                continue

            match = Match(
                played_at=_played_at(y, mo, d, session_no, game_no),
                map_name=map_name,
                screenshot_path=rel,
                extraction_raw=result.model_dump(),
                status="pending",
            )
            session.add(match)
            session.commit()
            created += 1
            print(f"OK  pending #{match.id}: {path.name} (map={match.map_name})")
    finally:
        session.close()

    print(f"\n완료: 생성 {created} · 스킵 {skipped} · 실패 {failed}")
    print("이제 홈의 '검토 대기' 목록 또는 /review/{id} 에서 확정하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
