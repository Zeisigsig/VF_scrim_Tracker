"""리더보드 수축 계산 — 순수 함수 (스펙 §4.7)."""
from __future__ import annotations

from app import config
from app.rating.tacr import display_score


def eb_adjusted_tacr(n: int, mean_tacr: float) -> float:
    """empirical Bayes 수축: adj = (n·mean + m·100) / (n + m)."""
    m = config.EB_SHRINK_M
    return (n * mean_tacr + m * config.EB_PRIOR_TACR) / (n + m)


def eb_adjusted_score(n: int, mean_tacr: float) -> float:
    """수축 조정된 평균 display_score (정렬 기준)."""
    return display_score(eb_adjusted_tacr(n, mean_tacr))
