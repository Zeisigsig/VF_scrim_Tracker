"""로컬 OCR → 클라우드 적재 CLI.

스크린샷을 **제자리에서** 로컬 OCR(RapidOCR) 하고, 추출 결과(JSON)만
클라우드 웹의 /api/ingest 로 전송한다. 이미지 원본은 로컬 밖으로 나가지 않으며
어디에도 복사되지 않는다. 확정/레이팅은 웹의 /review 에서 사람이 한다.

파일명 규칙({YYYYMMDD}_{맵}_{세션}_{판}.png)과 맞으면 맵/날짜를 채운다.

환경(.env): CLOUD_BASE_URL(예: https://xxx.ts.net), INGEST_API_KEY, ENABLE_LOCAL_UPLOAD=1

실행:
    uv run python -m app.ingest.push shot1.png shot2.png
    uv run python -m app.ingest.push ./some_dir   # 폴더 내 이미지 일괄
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

from app import config
from app.ingest.backfill import _parse, _played_at
from app.ingest.extractor import extract_scoreboard


def _iter_images(args: list[str]):
    """인자(파일/폴더)를 이미지 경로로 펼친다."""
    for a in args:
        p = Path(a)
        if p.is_dir():
            yield from sorted(
                q for q in p.iterdir()
                if q.is_file() and q.suffix.lower() in config.IMAGE_EXTENSIONS
            )
        elif p.is_file() and p.suffix.lower() in config.IMAGE_EXTENSIONS:
            yield p
        else:
            print(f"skip (이미지 아님/없음): {a}", file=sys.stderr)


def push_extraction(client: httpx.Client, path: Path) -> dict:
    """path 를 로컬 OCR 하고 추출 JSON 을 /api/ingest 로 전송, 응답 dict 반환.

    파일명이 명명 규칙({YYYYMMDD}_{맵}_{세션}_{판})과 맞으면 맵/시간을 채운다.
    응답은 {"skipped": ...}(이미 적재됨) 또는 {"review_url": ...}(신규 생성).
    """
    result = extract_scoreboard(path)  # 로컬 OCR
    parsed = _parse(path.name)
    map_name = parsed[3] or "" if parsed else ""
    played_at = _played_at(parsed[0], parsed[1], parsed[2], parsed[4], parsed[5]) if parsed else ""

    resp = client.post(
        "/api/ingest",
        headers={"X-Ingest-Key": config.INGEST_API_KEY},
        json={
            "extraction": result.model_dump(mode="json"),
            "filename": path.name,
            "map_name": map_name,
            "played_at": played_at,
        },
    )
    resp.raise_for_status()
    return resp.json()


def _push_one(client: httpx.Client, path: Path) -> None:
    data = push_extraction(client, path)
    if "skipped" in data:
        print(f"skip (이미 적재됨): {path.name}")
    else:
        print(f"OK  {path.name} → {config.CLOUD_BASE_URL}{data['review_url']}")


def run(argv: list[str]) -> int:
    if not config.CLOUD_BASE_URL or not config.INGEST_API_KEY:
        print("CLOUD_BASE_URL / INGEST_API_KEY 가 .env 에 설정되어야 합니다.", file=sys.stderr)
        return 2
    if not argv:
        print("사용법: python -m app.ingest.push <이미지|폴더> ...", file=sys.stderr)
        return 2

    images = list(_iter_images(argv))
    if not images:
        print("전송할 이미지가 없습니다.", file=sys.stderr)
        return 1

    failed = 0
    with httpx.Client(base_url=config.CLOUD_BASE_URL, timeout=60.0) as client:
        for path in images:
            try:
                _push_one(client, path)
            except Exception as e:  # 한 장 실패해도 나머지 계속
                print(f"FAIL {path.name} — {e}", file=sys.stderr)
                failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
