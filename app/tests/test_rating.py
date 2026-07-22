"""rating 모듈 회귀 테스트 (스펙 §10). 스펙 §10 픽스처 사용.

third-party 없이 stdlib 로만 동작한다 (openskill 미의존).
"""
from __future__ import annotations

import math

import pytest

from app import config
from app.rating import tier
from app.rating.leaderboard import eb_adjusted_tacr
from app.rating.tacr import (
    PlayerStat,
    compute_match,
    display_score,
    expected_acs,
    weight,
)

# 스펙 §10 픽스처: (team, nick, agent, tier_eff, acs, k, d, a, econ, fk, plant, defuse)
FIXTURE_ROWS = [
    ("A", "Perik", "레이나", 22, 319, 19, 14, 4, 79, 4, 0, 0),
    ("B", "이구로", "제트", 19, 259, 13, 15, 3, 66, 9, 0, 0),
    ("B", "황석영", "스카이", 22, 257, 18, 8, 3, 83, 1, 7, 1),
    ("B", "横幅", "레이나", 24, 244, 16, 12, 5, 57, 0, 0, 0),
    ("B", "아가라구요", "클로브", 22, 229, 17, 9, 6, 63, 0, 1, 0),
    ("A", "죄송한데죽빵한대만갈겨도될까요", "페이드", 22, 201, 13, 13, 2, 44, 0, 1, 0),
    ("A", "미움받을 용기", "사이퍼", 22, 156, 9, 14, 6, 51, 1, 0, 0),
    ("B", "마리골드", "세이지", 13, 146, 9, 13, 10, 36, 1, 0, 0),
    ("A", "Étoile", "오멘", 13, 143, 8, 15, 7, 34, 0, 1, 0),
    ("A", "겁먹지않아", "레이즈", 22, 141, 6, 17, 3, 42, 2, 0, 0),
]


def build_fixture() -> list[PlayerStat]:
    stats = []
    for team, _nick, agent, t, acs, k, d, a, econ, fk, plant, defuse in FIXTURE_ROWS:
        stats.append(
            PlayerStat(
                team=team,
                role=config.AGENT_ROLE[agent],
                tier_eff=float(t),
                acs=float(acs),
                kills=k,
                deaths=d,
                assists=a,
                econ=float(econ),
                first_kills=fk,
                plants=plant,
                defuses=defuse,
            )
        )
    return stats


def test_display_score_sigmoid_boundary():
    # TACR 100 → 500점 (스펙 §4.5)
    assert display_score(100.0) == pytest.approx(500.0)
    assert display_score(1000.0) == pytest.approx(1000.0, abs=1.0)
    assert display_score(-1000.0) == pytest.approx(0.0, abs=1.0)


def test_expected_acs_sum_invariant():
    # 팀 분리 공식의 기대 ACS 총합은 실제 ACS 총합과 같다.
    # (team_factor_A + team_factor_B = 2, 팀 내 지분 합 = 1)
    players = build_fixture()
    exp = expected_acs(players)
    assert sum(exp) == pytest.approx(sum(p.acs for p in players), rel=1e-9)
    assert len(exp) == 10


def test_expected_acs_matches_lobby_normalization_when_balanced():
    # 팀 밸런스가 맞으면(S_A ≈ S_B) 팀분리 공식이 로비 전체 정규화와 근사 일치.
    balanced = []
    for i in range(10):
        team = "A" if i % 2 == 0 else "B"
        balanced.append(
            PlayerStat(team=team, role="initiator", tier_eff=16.0, acs=200.0,
                       kills=15, deaths=15, assists=5, econ=50.0)
        )
    exp = expected_acs(balanced)
    weights = [weight(p.tier_eff, p.role) for p in balanced]
    s_total = sum(weights)
    lobby_avg = sum(p.acs for p in balanced) / len(balanced)
    lobby_wide = [lobby_avg * 10 * w / s_total for w in weights]
    for e, lw in zip(exp, lobby_wide):
        assert e == pytest.approx(lw, rel=1e-6)


def test_ratios_are_capped():
    players = build_fixture()
    results = compute_match(players)
    lo, hi = config.RATIO_CAP
    for r in results:
        for name, val in r.components.items():
            assert lo - 1e-9 <= val <= hi + 1e-9, f"{name}={val} 캡 범위 밖"


def test_compute_match_basic_shape():
    players = build_fixture()
    results = compute_match(players)
    assert len(results) == 10
    for r in results:
        assert math.isfinite(r.tacr)
        assert 0.0 <= r.display_score <= 1000.0
        assert set(r.components) == {"r_acs", "r_kd", "r_econ", "r_obj"}


def test_top_acs_overperformer_beats_expected():
    # Perik: 낮은 편 티어 대비 최고 ACS → r_acs > 1 이어야 함
    players = build_fixture()
    results = compute_match(players)
    perik = results[0]
    assert perik.components["r_acs"] > 1.0
    assert perik.tacr > 100.0  # 티어 기대치 초과 수행


def test_phase2_formula_selected_when_enriched():
    players = build_fixture()
    for i, p in enumerate(players):
        p.kast = 0.70
        p.adr = 140.0 + i
        p.first_deaths = 2
    results = compute_match(players, rounds=24)
    for r in results:
        assert set(r.components) == {"r_acs", "r_kast", "r_fkfd", "r_adr"}


def test_eb_shrink_pulls_toward_prior():
    # 판수 적으면 사전값(100)에 가깝게 수축
    few = eb_adjusted_tacr(1, 200.0)
    many = eb_adjusted_tacr(50, 200.0)
    assert few < many
    assert abs(few - config.EB_PRIOR_TACR) < abs(many - config.EB_PRIOR_TACR)


def test_tier_to_value():
    assert tier.tier_to_value("불멸") == 22.0
    assert tier.tier_to_value("레디언트") == 24.0
    assert tier.tier_to_value("아이언", division=1) == 0.0
    assert tier.tier_to_value("골드", division=3) == 11.0
    assert tier.tier_to_value("platinum") == 13.0  # 영문 별칭


def test_effective_tier_priority():
    # μ 존재 + 판수 충분 → μ 사용
    val, label = tier.effective_tier(mu=18.0, games_counted=5,
                                     prior=tier.RankPrior(current=13.0))
    assert val == 18.0 and label == "openskill"
    # μ 부족 → 랭크 prior
    val2, label2 = tier.effective_tier(mu=18.0, games_counted=1,
                                       prior=tier.RankPrior(current=13.0, n_games=20))
    assert label2 != "openskill"
