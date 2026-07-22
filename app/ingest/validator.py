"""추출 결과 sanity check (스펙 §5.3).

전부 순수 함수. 위반 시 경고 리스트를 반환하되 저장을 차단하지 않는다
(사용자가 검토 화면에서 수정 후 확정).
"""
from __future__ import annotations

from dataclasses import dataclass

from app import config
from app.ingest.schemas import ExtractionResult


@dataclass
class Warning:
    code: str
    message: str


def validate(result: ExtractionResult) -> list[Warning]:
    rows = result.rows
    warnings: list[Warning] = []

    if len(rows) != 10:
        warnings.append(Warning("row_count", f"행이 10개가 아님: {len(rows)}개"))

    sum_k = sum(r.kills for r in rows)
    sum_d = sum(r.deaths for r in rows)
    if abs(sum_k - sum_d) > 3:
        warnings.append(Warning("kd_balance", f"|ΣK−ΣD| > 3: ΣK={sum_k}, ΣD={sum_d}"))

    for r in rows:
        if not (0 <= r.acs <= 500):
            warnings.append(Warning("acs_range", f"{r.nickname}: ACS {r.acs} 범위 밖 [0,500]"))
        for label, v in (("K", r.kills), ("D", r.deaths), ("A", r.assists)):
            if not (0 <= v <= 60):
                warnings.append(Warning("kda_range", f"{r.nickname}: {label} {v} 범위 밖 [0,60]"))

    teams = [r.team for r in rows]
    if teams.count("A") != 5 or teams.count("B") != 5:
        warnings.append(
            Warning("team_split", f"팀 5:5 아님: A={teams.count('A')}, B={teams.count('B')}")
        )

    valid = config.valid_agents()
    for r in rows:
        if r.agent_kr not in valid:
            warnings.append(
                Warning("unknown_agent", f"{r.nickname}: 미지 요원 '{r.agent_kr}' — 수동 지정 필요")
            )

    seen: set[str] = set()
    for r in rows:
        if r.nickname in seen:
            warnings.append(Warning("dup_nickname", f"닉네임 중복: {r.nickname}"))
        seen.add(r.nickname)

    return warnings
