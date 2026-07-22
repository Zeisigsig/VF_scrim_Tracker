"""기대 ACS · TACR · 표시 점수 계산 — 순수 함수 (스펙 §4.3~§4.6).

DB 의존성 없음. 입력은 한 경기(로비 10명)의 스탯 리스트.
모든 튜닝 파라미터는 app.config 에서 가져온다 (매직 넘버 금지).
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

from app import config


@dataclass
class PlayerStat:
    """한 경기 내 한 선수의 원시 스탯 + 유효 티어."""
    team: str  # 'A' | 'B'
    role: str  # duelist|initiator|controller|sentinel
    tier_eff: float
    acs: float
    kills: int
    deaths: int
    assists: int
    econ: float = 0.0
    first_kills: int = 0
    plants: int = 0
    defuses: int = 0
    # Phase 2 enrichment (없으면 None)
    kast: float | None = None
    adr: float | None = None
    first_deaths: int | None = None


@dataclass
class PlayerResult:
    expected_acs: float
    tacr: float
    display_score: float
    tier_eff_used: float
    components: dict[str, float] = field(default_factory=dict)


def _cap(x: float) -> float:
    lo, hi = config.RATIO_CAP
    return max(lo, min(hi, x))


def weight(tier_eff: float, role: str) -> float:
    """weight_i = exp(K_TIER × tier_eff) × ROLE_COEF[role]."""
    coef = config.ROLE_COEF.get(role, 1.0)
    return math.exp(config.K_TIER * tier_eff) * coef


def expected_acs(players: list[PlayerStat]) -> list[float]:
    """팀 분리 기대 ACS (스펙 §4.3). players 순서대로 반환.

    로비 총 ACS 를 팀 강도 비 S^γ (γ=TEAM_STRENGTH_EXP)로 두 팀에 배분한 뒤,
    각 팀 내부에서 가중치 지분(weight_i / S_own)으로 분배한다. 총합은 보존된다
    (Σ expected = Σ ACS). γ=1 이면 팀 항이 약분돼 로비 전체 정규화로 붕괴하므로,
    팀 격차를 실제로 반영하려면 γ>1 이어야 한다(강팀일수록 기대 ACS 상향).
    """
    weights = [weight(p.tier_eff, p.role) for p in players]
    s_a = sum(w for w, p in zip(weights, players) if p.team == "A") or 1e-9
    s_b = sum(w for w, p in zip(weights, players) if p.team == "B") or 1e-9
    gamma = config.TEAM_STRENGTH_EXP
    pa, pb = s_a ** gamma, s_b ** gamma
    share_a = pa / (pa + pb)
    total_acs = sum(p.acs for p in players)

    out: list[float] = []
    for w, p in zip(weights, players):
        if p.team == "A":
            team_total = total_acs * share_a
            s_own = s_a
        else:
            team_total = total_acs * (1 - share_a)
            s_own = s_b
        out.append(team_total * w / s_own)
    return out


def _use_phase2(players: list[PlayerStat]) -> bool:
    """10명 전원 enrichment(kast, adr, first_deaths) 존재 시에만 Phase 2 공식."""
    return all(
        p.kast is not None and p.adr is not None and p.first_deaths is not None
        for p in players
    )


def compute_match(players: list[PlayerStat], rounds: int | None = None) -> list[PlayerResult]:
    """로비 전체 TACR/표시점수 계산. players 순서대로 결과 반환.

    동일 경기 내에서는 하나의 공식만 사용한다 (스펙 §4.4).
    """
    if not players:
        return []
    weights = [weight(p.tier_eff, p.role) for p in players]
    exp_acs = expected_acs(players)
    lobby_mean_weight = statistics.mean(weights)
    lobby_avg_econ = statistics.mean(p.econ for p in players) or 1e-9
    sum_k = sum(p.kills for p in players)
    sum_d = sum(p.deaths for p in players)
    lobby_kd = sum_k / max(sum_d, 1)
    obj_vals = [p.first_kills + p.plants + p.defuses for p in players]
    lobby_avg_obj = statistics.mean(obj_vals)
    lobby_mean_tier = statistics.mean(p.tier_eff for p in players)

    phase2 = _use_phase2(players)
    if phase2:
        lobby_avg_adr = statistics.mean(p.adr for p in players) or 1e-9  # type: ignore[arg-type]

    results: list[PlayerResult] = []
    for i, p in enumerate(players):
        rel = weights[i] / lobby_mean_weight if lobby_mean_weight else 1.0
        r_acs = _cap(p.acs / (exp_acs[i] or 1e-9))

        if phase2:
            assert p.kast is not None and p.adr is not None and p.first_deaths is not None
            exp_kast = config.KAST_BASE + config.KAST_TIER_SLOPE * (p.tier_eff - lobby_mean_tier)
            r_kast = _cap(p.kast / max(exp_kast, 1e-9))
            r_fkfd = _cap(1 + (p.first_kills - p.first_deaths) / max(rounds or 1, 1) * config.FKFD_ROUND_GAIN)
            r_adr = _cap(p.adr / max(rel * lobby_avg_adr, 1e-9))
            w = config.TACR_WEIGHTS_P2
            tacr = 100 * (w["acs"] * r_acs + w["kast"] * r_kast + w["fkfd"] * r_fkfd + w["adr"] * r_adr)
            comps = {"r_acs": r_acs, "r_kast": r_kast, "r_fkfd": r_fkfd, "r_adr": r_adr}
        else:
            kd_num = (p.kills + config.KD_ASSIST_WEIGHT * p.assists) / max(p.deaths, 1)
            kd_den = config.KD_LOBBY_FACTOR * rel * lobby_kd
            r_kd = _cap(kd_num / max(kd_den, 1e-9))
            r_econ = _cap(p.econ / max(rel * lobby_avg_econ, 1e-9))
            r_obj = _cap((p.first_kills + p.plants + p.defuses + 1) / (lobby_avg_obj + 1))
            w = config.TACR_WEIGHTS_P1
            tacr = 100 * (w["acs"] * r_acs + w["kd"] * r_kd + w["econ"] * r_econ + w["obj"] * r_obj)
            comps = {"r_acs": r_acs, "r_kd": r_kd, "r_econ": r_econ, "r_obj": r_obj}

        results.append(
            PlayerResult(
                expected_acs=exp_acs[i],
                tacr=tacr,
                display_score=display_score(tacr),
                tier_eff_used=p.tier_eff,
                components=comps,
            )
        )
    return results


def display_score(tacr: float) -> float:
    """표시 점수 0–1000 (스펙 §4.5). TACR 100 → 500점."""
    return 1000.0 / (1.0 + math.exp(-(tacr - config.DISPLAY_MIDPOINT) / config.DISPLAY_SCALE))


def implied_tier(players: list[PlayerStat]) -> list[float]:
    """관측 ACS 지분을 §4.3 공식에 역대입한 implied tier (스펙 §4.6).

    검증용이며 tier_eff 에는 사용하지 않는다. 팀 결합 항(team_factor, S_own)이
    weight_i 에 의존하므로 정확한 역해는 어렵다. 여기서는 1차 근사로
    implied_weight ≈ weight_i × (ACS_i / expected_ACS_i) 로 두고 티어를 역산한다.
    """
    exp_acs = expected_acs(players)
    out: list[float] = []
    for p, e in zip(players, exp_acs):
        w = weight(p.tier_eff, p.role)
        ratio = p.acs / (e or 1e-9)
        implied_w = max(w * ratio, 1e-9)
        coef = config.ROLE_COEF.get(p.role, 1.0)
        implied_t = math.log(implied_w / coef) / config.K_TIER
        out.append(implied_t)
    return out
