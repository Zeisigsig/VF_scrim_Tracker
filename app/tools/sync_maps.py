"""valorant-api.com 에서 맵 메타(미니맵·좌표계수·콜아웃)를 받아 정적 JSON 으로 저장.

개인 페이지 킬/데스 히트맵이 쓰는 데이터다. 브라우저가 /static/maps.json 을
직접 받아 좌표를 변환하므로 서버는 이 파일을 읽지 않는다(앱 기동 중 외부 호출 0).

저장 내용(맵당):
  uuid, name(한글), icon(미니맵 PNG URL), xm/ym/xs/ys(좌표 변환 계수),
  callouts[{name, region, x, y}]  ← 지점 이름 붙이기용

게임 좌표 → 미니맵 비율(0~1) 변환식 (실측 검증: 어센트 콜아웃 22/22 정위치):
    px = y * xm + xs
    py = x * ym + ys
게임의 x/y 가 이미지의 y/x 로 들어가는 축 스왑이 있다.

실행: uv run python -m app.tools.sync_maps
     (맵이 추가/변경될 때만 돌리면 되고, 결과 JSON 은 저장소에 커밋한다.)
"""
from __future__ import annotations

import json
import sys

import httpx

from app import config

API = "https://valorant-api.com/v1/maps"
OUT = config.BASE_DIR / "app" / "web" / "static" / "maps.json"


def run() -> int:
    try:
        resp = httpx.get(API, params={"language": "ko-KR"}, timeout=60)
        resp.raise_for_status()
        data = resp.json()["data"]
    except Exception as e:  # 네트워크/포맷 문제는 조용히 실패시키지 않는다
        print(f"맵 메타 조회 실패: {e}", file=sys.stderr)
        return 1

    maps = []
    for m in data:
        # 미니맵이나 좌표 계수가 없으면 히트맵을 그릴 수 없다. 계수가 0 인 맵
        # (데스매치 전용 — 디스트릭트·카즈바·드리프트·글리치·피아자)도 제외한다.
        if not m.get("displayIcon") or not m.get("xMultiplier") or not m.get("yMultiplier"):
            continue
        maps.append({
            "uuid": m["uuid"],
            "name": m["displayName"],
            "icon": m["displayIcon"],
            "xm": m["xMultiplier"], "ym": m["yMultiplier"],
            "xs": m["xScalarToAdd"], "ys": m["yScalarToAdd"],
            "callouts": [
                {
                    "name": c["regionName"],
                    "region": c.get("superRegionName") or "",
                    "x": c["location"]["x"], "y": c["location"]["y"],
                }
                for c in (m.get("callouts") or [])
            ],
        })

    OUT.write_text(
        json.dumps(maps, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    total_callouts = sum(len(m["callouts"]) for m in maps)
    print(f"저장: {OUT} — 맵 {len(maps)}개 · 콜아웃 {total_callouts}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
