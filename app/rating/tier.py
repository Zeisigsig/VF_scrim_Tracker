"""티어 수치화 및 유효 티어(tier_eff) 계산 — 순수 함수 (스펙 §4.1, §4.2)."""
from __future__ import annotations

import statistics
from dataclasses import dataclass

from app import config


def tier_to_value(tier_name: str, division: int | None = None) -> float:
    """티어명(+선택 디비전) → 수치.

    division 은 1~3 (1이 가장 낮음). 미상이면 티어 중간값을 반환.
    Iron 1 = 0, 디비전당 +1.
    """
    key = _normalize_tier(tier_name)
    if key is None:
        raise ValueError(f"알 수 없는 티어: {tier_name!r}")
    low, mid, high = config.TIER_TABLE[key]
    if division is None:
        return float(mid)
    if key == "레디언트":
        return float(high)
    # division 1 -> low, 2 -> low+1, 3 -> low+2 (범위 상한으로 클램프)
    return float(min(low + (division - 1), high))


def _normalize_tier(tier_name: str) -> str | None:
    name = tier_name.strip()
    if name in config.TIER_TABLE:
        return name
    return config.TIER_ALIASES.get(name.lower())


@dataclass(frozen=True)
class RankPrior:
    """랭크 기반 prior 입력.

    current: 현재 액트 랭크 수치 (없으면 None)
    n_games: 현재 액트 랭크 판수 (current 있을 때만 의미)
    peak: 최고 랭크 수치 (없으면 None)
    acts_since_peak: peak 달성 후 경과 액트 수 (없으면 0)
    """
    current: float | None = None
    n_games: int = 0
    peak: float | None = None
    acts_since_peak: int = 0


def peak_decayed(peak: float, acts_since_peak: int, floor: float | None = None) -> float:
    """peak_decayed = peak - 0.5 * 경과 액트 수, 하한 = floor(있으면)."""
    val = peak - config.PEAK_DECAY_PER_ACT * max(acts_since_peak, 0)
    if floor is not None:
        val = max(val, floor)
    return val


def rank_based_tier(prior: RankPrior, lobby_median: float | None = None) -> tuple[float, str]:
    """랭크 기반 tier_eff 계산. (값, 신뢰도라벨) 반환.

    신뢰도라벨은 OpenSkill 초기 σ 선택에 사용된다 (스펙 §4.6).
    """
    if prior.current is not None:
        c = prior.n_games / (prior.n_games + config.RANK_CONFIDENCE_DENOM)
        if prior.peak is not None:
            pd = peak_decayed(prior.peak, prior.acts_since_peak, floor=prior.current)
        else:
            pd = prior.current
        value = c * prior.current + (1 - c) * pd
        label = "ranked_confident" if prior.n_games >= config.OPENSKILL_MIN_GAMES else "peak_only"
        return value, label
    if prior.peak is not None:
        return peak_decayed(prior.peak, prior.acts_since_peak), "peak_only"
    # 언랭: 해당 경기 로비 tier_eff 중앙값 (없으면 실버 중간값 fallback)
    fallback = lobby_median if lobby_median is not None else config.TIER_TABLE["실버"][1]
    return float(fallback), "unranked"


def effective_tier(
    mu: float | None,
    games_counted: int,
    prior: RankPrior,
    lobby_median: float | None = None,
) -> tuple[float, str]:
    """유효 티어 우선순위 적용 (스펙 §4.2).

    1) OpenSkill μ 존재 & games_counted >= 3 → μ 사용
    2) 아니면 랭크 기반 prior
    """
    if mu is not None and games_counted >= config.OPENSKILL_MIN_GAMES:
        return mu, "openskill"
    return rank_based_tier(prior, lobby_median)


def lobby_median_tier(values: list[float]) -> float:
    return statistics.median(values)
