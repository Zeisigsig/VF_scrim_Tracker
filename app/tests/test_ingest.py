"""validator / matcher 테스트 (스펙 §10)."""
from __future__ import annotations

from app.ingest.matcher import rank_candidates
from app.ingest.schemas import ExtractedRow, ExtractionResult
from app.ingest.validator import validate


def _row(nick="p", agent="제트", team="A", acs=200, k=15, d=15, a=5) -> ExtractedRow:
    return ExtractedRow(
        nickname=nick, agent_kr=agent, team_color="초록", team=team,
        acs=acs, kills=k, deaths=d, assists=a, econ=50,
        first_kills=0, plants=0, defuses=0,
    )


def _good_result() -> ExtractionResult:
    rows = []
    agents = ["제트", "레이나", "소바", "오멘", "사이퍼",
              "레이즈", "스카이", "바이퍼", "킬조이", "네온"]
    for i in range(10):
        rows.append(_row(nick=f"p{i}", agent=agents[i], team="A" if i < 5 else "B"))
    return ExtractionResult(rows=rows)


def test_valid_result_has_no_warnings():
    assert validate(_good_result()) == []


def test_row_count_violation():
    res = _good_result()
    res.rows = res.rows[:9]
    codes = {w.code for w in validate(res)}
    assert "row_count" in codes
    assert "team_split" in codes  # 9명이면 5:5도 깨짐


def test_kd_balance_violation():
    res = _good_result()
    res.rows[0].kills = 60  # ΣK 크게 틀어짐
    assert "kd_balance" in {w.code for w in validate(res)}


def test_range_violations():
    res = _good_result()
    res.rows[0].acs = 999
    res.rows[1].kills = 99
    codes = {w.code for w in validate(res)}
    assert "acs_range" in codes and "kda_range" in codes


def test_unknown_agent():
    res = _good_result()
    res.rows[0].agent_kr = "없는요원"
    assert "unknown_agent" in {w.code for w in validate(res)}


def test_duplicate_nickname():
    res = _good_result()
    res.rows[1].nickname = res.rows[0].nickname
    assert "dup_nickname" in {w.code for w in validate(res)}


def test_matcher_exact_and_fuzzy():
    known = [(1, "Perik"), (2, "이구로"), (3, "황석영")]
    cands = rank_candidates("Perik", known)
    assert cands[0].player_id == 1 and cands[0].score >= 99
    # 오타 유사 매칭
    cands2 = rank_candidates("Perlk", known)
    assert cands2[0].player_id == 1
    assert len(cands2) <= 3
