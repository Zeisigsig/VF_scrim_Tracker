"""OpenSkill μ/σ 업데이트 래퍼 (스펙 §4.6).

openskill 패키지의 PlackettLuce 모델을 얇게 감싼다. DB 의존성 없음:
현재 (mu, sigma) 상태와 팀 구성을 받아 갱신된 상태를 반환하는 순수 함수.
"""
from __future__ import annotations

from dataclasses import dataclass

from openskill.models import PlackettLuce

from app import config

_model = PlackettLuce()


@dataclass
class SkillState:
    mu: float
    sigma: float


def initial_state(tier_prior: float, confidence_label: str) -> SkillState:
    """미등록 선수 초기화: μ = tier prior, σ = 랭크 신뢰도 역수."""
    sigma = config.SIGMA_INIT.get(confidence_label, config.SIGMA_INIT["unranked"])
    return SkillState(mu=tier_prior, sigma=sigma)


def update_match(
    team_a: list[SkillState],
    team_b: list[SkillState],
    a_won: bool,
) -> tuple[list[SkillState], list[SkillState]]:
    """팀 승패로 표준 업데이트. 낮은 rank 값이 승리.

    무승부는 호출부에서 스킵한다 (스펙 §4.6). 여기서는 명확한 승/패만 처리.
    """
    ratings_a = [_model.rating(mu=s.mu, sigma=s.sigma) for s in team_a]
    ratings_b = [_model.rating(mu=s.mu, sigma=s.sigma) for s in team_b]
    ranks = [0, 1] if a_won else [1, 0]
    new_a, new_b = _model.rate([ratings_a, ratings_b], ranks=ranks)
    return (
        [SkillState(mu=r.mu, sigma=r.sigma) for r in new_a],
        [SkillState(mu=r.mu, sigma=r.sigma) for r in new_b],
    )
