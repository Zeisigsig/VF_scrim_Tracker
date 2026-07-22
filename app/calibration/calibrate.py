"""캘리브레이션 리포트 (스펙 §8, Phase 3, 경기 20+ 누적 후).

DB 의 확정 경기에서 파라미터를 재적합 제안한다. **자동 적용 금지** — 리포트만 출력.
numpy 없이 stdlib 로 최소제곱/로지스틱 회귀를 구현한다.

실행: python -m app.calibration.calibrate
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from sqlalchemy import select

from app import config
from app.db.models import Match, MatchPlayer, MatchRating
from app.db.session import SessionLocal
from app.rating.tacr import PlayerStat, compute_match


@dataclass
class LobbyData:
    match_id: int
    stats: list[PlayerStat]
    teams: list[str]  # 각 stat 의 팀
    a_won: bool | None


def _load_lobbies(session) -> list[LobbyData]:
    lobbies: list[LobbyData] = []
    matches = session.scalars(
        select(Match).where(Match.status == "confirmed").order_by(Match.played_at)
    ).all()
    for m in matches:
        mps = session.scalars(
            select(MatchPlayer).where(MatchPlayer.match_id == m.id)
        ).all()
        # tier_eff 는 계산 당시 저장된 match_ratings.tier_eff_used 사용
        stats = []
        for mp in mps:
            rating = session.scalar(
                select(MatchRating).where(MatchRating.match_player_id == mp.id)
            )
            te = rating.tier_eff_used if rating else 0.0
            stats.append(PlayerStat(
                team=mp.team, role=mp.role, tier_eff=te, acs=mp.acs, kills=mp.kills,
                deaths=mp.deaths, assists=mp.assists, econ=float(mp.econ_rating or 0),
                first_kills=mp.first_kills or 0, plants=mp.plants or 0,
                defuses=mp.defuses or 0,
            ))
        a_won = None
        if m.team_a_rounds is not None and m.team_b_rounds is not None:
            if m.team_a_rounds != m.team_b_rounds:
                a_won = m.team_a_rounds > m.team_b_rounds
        lobbies.append(LobbyData(m.id, stats, [s.team for s in stats], a_won))
    return lobbies


def _ols_slope(xs: list[float], ys: list[float]) -> float:
    """단순 최소제곱 기울기 (절편 포함, 기울기만 반환)."""
    n = len(xs)
    if n < 2:
        return float("nan")
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    return num / den if den else float("nan")


def calibrate_k_tier(lobbies: list[LobbyData]) -> float:
    """K_TIER: log(ACS_i/lobby_avg) ~ (tier_eff_i - lobby_mean_tier) 기울기."""
    xs, ys = [], []
    for lob in lobbies:
        if not lob.stats:
            continue
        lobby_avg = statistics.mean(s.acs for s in lob.stats)
        lobby_tier = statistics.mean(s.tier_eff for s in lob.stats)
        for s in lob.stats:
            if s.acs > 0 and lobby_avg > 0:
                xs.append(s.tier_eff - lobby_tier)
                ys.append(math.log(s.acs / lobby_avg))
    return _ols_slope(xs, ys)


def calibrate_role_coef(lobbies: list[LobbyData]) -> dict[str, float]:
    """ROLE_COEF: 역할별 mean(r_acs) 의 역수로 정규화 재산출."""
    role_ratios: dict[str, list[float]] = {}
    for lob in lobbies:
        results = compute_match(lob.stats)
        for s, res in zip(lob.stats, results):
            role_ratios.setdefault(s.role, []).append(res.components["r_acs"])
    means = {r: statistics.mean(v) for r, v in role_ratios.items() if v}
    if not means:
        return {}
    # 역수 정규화: initiator 를 1.0 기준으로
    base = means.get("initiator", statistics.mean(means.values()))
    return {r: base / m * config.ROLE_COEF.get(r, 1.0) for r, m in means.items()}


def validate_team_winrate(lobbies: list[LobbyData]) -> tuple[float, int]:
    """TACR 합 우세 팀의 실제 승률 (목표 ≥ 0.70). (승률, 표본수) 반환."""
    correct = 0
    total = 0
    for lob in lobbies:
        if lob.a_won is None:
            continue
        results = compute_match(lob.stats)
        sum_a = sum(r.tacr for r, t in zip(results, lob.teams) if t == "A")
        sum_b = sum(r.tacr for r, t in zip(results, lob.teams) if t == "B")
        if sum_a == sum_b:
            continue
        predicted_a = sum_a > sum_b
        if predicted_a == lob.a_won:
            correct += 1
        total += 1
    return (correct / total if total else float("nan"), total)


def run_report() -> None:
    session = SessionLocal()
    try:
        lobbies = _load_lobbies(session)
    finally:
        session.close()

    n_games = len(lobbies)
    print("=" * 60)
    print(f"캘리브레이션 리포트  (확정 경기 {n_games}개, PARAMS_VERSION={config.PARAMS_VERSION})")
    print("=" * 60)
    if n_games < 20:
        print(f"⚠ 경기가 20개 미만입니다 ({n_games}). 제안값의 신뢰도가 낮습니다.")

    k = calibrate_k_tier(lobbies)
    print(f"\n[1] K_TIER  현재={config.K_TIER}  제안={k:.4f}")

    roles = calibrate_role_coef(lobbies)
    print("\n[2] ROLE_COEF")
    for r in config.ROLE_COEF:
        print(f"    {r:11s} 현재={config.ROLE_COEF[r]:.3f}  제안={roles.get(r, float('nan')):.3f}")

    wr, total = validate_team_winrate(lobbies)
    print(f"\n[3] 검증: TACR 합 우세 팀 실제 승률 = {wr:.1%} ({total}경기, 목표 ≥ 70%)")

    print("\n※ 위 제안값은 자동 적용되지 않습니다. config.py 를 수동 수정하고")
    print("  PARAMS_VERSION 을 올린 뒤 `python -m app.calibration.recompute` 를 실행하세요.")


if __name__ == "__main__":
    run_report()
