"""HenrikDev 비공식 API 클라이언트 (스펙 §7, Phase 2).

Phase 1 은 이것 없이 완결 동작한다. 여기서는 PUUID 확보, 랭크 조회,
KAST/ADR/FD enrichment 를 위한 얇은 래퍼를 제공한다.

TODO(구현 첫 단계 검증 필요, 스펙 §7):
  1. 커스텀 게임이 매치 히스토리 응답에 나오는지, `mode` 필드로 필터 가능한지.
  2. v4 매치 응답에서 kast/adr/first_deaths 의 정확한 필드 경로.
  3. free tier 의 과거 매치 조회 한도(개수/기간).
아래 파싱 경로(_parse_*)는 위 검증 전까지 가정값이며, 실제 응답으로 조정할 것.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from app import config

BASE_URL = "https://api.henrikdev.xyz/valorant"
_MAX_RETRIES = 4
# free tier = 30 req / 60s. 호출 간 고정 간격 페이싱이 reset-헤더 백오프보다 안정적
# (reset 기반은 과다수면 버그를 유발했음).
_MIN_INTERVAL = 2.2


@dataclass
class RankInfo:
    current_tier: str | None
    current_games: int | None
    peak_tier: str | None


class HenrikClient:
    def __init__(self, api_key: str | None = None) -> None:
        self._key = api_key or config.HENRIK_API_KEY
        self._client = httpx.Client(
            headers={"Authorization": self._key} if self._key else {},
            timeout=15.0,
        )
        self._last_request = 0.0  # 페이싱용 마지막 요청 시각(monotonic)

    def _pace(self) -> None:
        """직전 요청 이후 _MIN_INTERVAL 초가 지나도록 대기."""
        elapsed = time.monotonic() - self._last_request
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        self._last_request = time.monotonic()

    def _get(self, path: str, params: dict | None = None) -> dict:
        """고정 간격 페이싱 GET. 429 는 reset 헤더만큼(상한) 대기 후 재시도."""
        url = f"{BASE_URL}{path}"
        for _ in range(_MAX_RETRIES):
            self._pace()
            resp = self._client.get(url, params=params)
            if resp.status_code == 429:
                reset = resp.headers.get("x-ratelimit-reset")
                wait = min(float(reset), 60.0) if reset and reset.isdigit() else _MIN_INTERVAL
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()  # 마지막 429 도 실패로 처리
        return {}

    # --- account: name#tag → puuid --------------------------------------
    def get_account(self, name: str, tag: str) -> dict:
        data = self._get(f"/v2/account/{name}/{tag}")
        return data.get("data", {})

    def get_puuid(self, name: str, tag: str) -> str | None:
        return self.get_account(name, tag).get("puuid")

    # --- MMR: 현재/최고 랭크 --------------------------------------------
    def get_rank(self, region: str, name: str, tag: str) -> RankInfo:
        data = self._get(f"/v3/mmr/{region}/pc/{name}/{tag}").get("data", {})
        return _parse_rank(data)

    # --- 매치 히스토리 (지문매칭·enrichment 소스) -----------------------
    def get_matches(
        self, region: str, name: str, tag: str, mode: str | None = None
    ) -> list[dict]:
        """최근 매치 목록. 각 항목은 10명 전체 로스터(name#tag/agent/stats)를 인라인 포함.

        mode=None 은 최근 경기, mode='custom' 은 (더 오래된) 커스텀만 반환 →
        커버리지를 위해 둘을 합쳐 쓴다.
        """
        params = {"mode": mode} if mode else None
        data = self._get(f"/v4/matches/{region}/pc/{name}/{tag}", params).get("data", [])
        return data if isinstance(data, list) else []

    # --- 매치 상세 (지역 경로 필요) -------------------------------------
    def get_match(self, region: str, match_id: str) -> dict:
        return self._get(f"/v4/match/{region}/{match_id}").get("data", {})

    def close(self) -> None:
        self._client.close()


def _parse_rank(data: dict) -> RankInfo:
    # TODO: v3 mmr 응답 실제 필드로 검증.
    current = data.get("current", {}) or {}
    peak = data.get("peak", {}) or {}
    return RankInfo(
        current_tier=(current.get("tier") or {}).get("name"),
        current_games=current.get("games_needed_for_rating"),
        peak_tier=(peak.get("tier") or {}).get("name"),
    )
