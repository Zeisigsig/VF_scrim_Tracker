"""업로드 시 자동 Henrik 보정 (스펙 §7 확장).

OCR 결과의 닉이 등록된 유저와 매칭되면, 그 유저의 Riot ID로 Henrik 매치
히스토리를 조회한다. 각 매치 항목은 10명 전체 로스터를 인라인 포함하므로,
로스터 겹침(닉·요원·KDA)으로 '그 경기'를 지문매칭한 뒤 K/D/A·ACS·요원·닉을
권위값으로 덮어쓴다. 바뀐 값은 Correction 으로 남겨 검토 화면에 표시한다.

원칙: Henrik 실패(rate limit·네트워크·미매칭)는 업로드를 막지 않는다(best-effort).
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.db.models import PlayerRiotAccount
from app.henrik.client import HenrikClient
from app.ingest.schemas import Correction, ExtractedRow, ExtractionResult
from app.services import resolve_existing_player

# 정렬·판별은 오직 숫자(K/D/A/ACS)로 한다. 닉·요원은 OCR이 자주 깨지는(그래서
# 우리가 보정하려는) 필드라 정렬 기준으로 쓰면 안 된다. 같은 멤버가 여러 판을
# 해도 '그 판'의 KDA+ACS 조합은 사실상 유일 → 로스터 스탯 거리로 정확히 특정됨.
_PAIR_TOL = 6.0           # 한 선수 스탯 거리(|ΔK|+|ΔD|+|ΔA|+0.5·|ΔACS|) 이하면 일치로 셈
_MIN_MATCHED = 6          # 이 수 이상 선수 스탯이 맞아야 그 경기로 확정(오탐 방지)
_MAX_SEEDS = 3            # 시드로 시도할 유저 수 상한


def _parse_roster(match: dict) -> list[dict]:
    out = []
    for p in match.get("players") or []:
        st = p.get("stats") or {}
        agent = p.get("agent")
        agent_en = agent.get("name") if isinstance(agent, dict) else agent
        out.append({
            "name": p.get("name") or "", "tag": p.get("tag") or "",
            "puuid": p.get("puuid"), "agent_en": agent_en,
            "k": st.get("kills"), "d": st.get("deaths"), "a": st.get("assists"),
            "score": st.get("score"), "team_id": p.get("team_id"),
        })
    return out


def _match_date(match: dict) -> date | None:
    s = (match.get("metadata") or {}).get("started_at")
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _played_date(played_at: str | None) -> date | None:
    if not played_at:
        return None
    try:
        return datetime.fromisoformat(played_at).date()
    except ValueError:
        return None


def _detail_stats(detail: dict) -> dict[str, dict[str, int]]:
    """매치 상세 → puuid별 {fk, plants, defuses}. 리스트 응답엔 없는 필드라 상세가 필요.

    - FK: 라운드별 최초 킬(kills[]의 그 라운드 최소 time)의 killer.
    - 설치/해제: 라운드 plant/defuse 의 player.
    로스터 전원 0으로 초기화 → '안 함'도 권위값 0으로 채운다.
    """
    out: dict[str, dict[str, int]] = {}
    for p in detail.get("players") or []:
        pu = p.get("puuid")
        if pu:
            out[pu] = {"fk": 0, "plants": 0, "defuses": 0}

    first: dict[int, tuple[int, str]] = {}  # round → (time, killer_puuid)
    for k in detail.get("kills") or []:
        rd, t = k.get("round"), k.get("time_in_round_in_ms")
        killer = (k.get("killer") or {}).get("puuid")
        if rd is None or t is None or not killer:
            continue
        cur = first.get(rd)
        if cur is None or t < cur[0]:
            first[rd] = (t, killer)
    for _t, killer in first.values():
        out.setdefault(killer, {"fk": 0, "plants": 0, "defuses": 0})["fk"] += 1

    for r in detail.get("rounds") or []:
        for ev, field in (("plant", "plants"), ("defuse", "defuses")):
            e = r.get(ev)
            pu = (e.get("player") or {}).get("puuid") if e else None
            if pu:
                out.setdefault(pu, {"fk": 0, "plants": 0, "defuses": 0})[field] += 1
    return out


def _total_rounds(match: dict) -> int | None:
    teams = match.get("teams") or []
    if teams:
        r = teams[0].get("rounds") or {}
        won, lost = r.get("won"), r.get("lost")
        if won is not None and lost is not None:
            return won + lost
    return None


def _team_rounds(match: dict) -> dict[str, int]:
    """team_id → 획득 라운드(won). 승패 절대정답(스펙 §7)."""
    out: dict[str, int] = {}
    for t in match.get("teams") or []:
        tid, r = t.get("team_id"), t.get("rounds") or {}
        if tid is not None and r.get("won") is not None:
            out[tid] = r["won"]
    return out


def _cost(row: ExtractedRow, r: dict, rounds: int | None) -> float | None:
    """OCR 행 ↔ 로스터 선수의 스탯 거리. 로스터 KDA 결측이면 None(정렬 불가)."""
    if r["k"] is None or r["d"] is None or r["a"] is None:
        return None
    kd = (abs((row.kills or 0) - r["k"])
          + abs((row.deaths or 0) - r["d"])
          + abs((row.assists or 0) - r["a"]))
    acs_r = round(r["score"] / rounds) if (r["score"] and rounds) else None
    acs_d = abs(row.acs - acs_r) if acs_r is not None else 12
    return kd + 0.5 * acs_d


def _assign(
    rows: list[ExtractedRow], roster: list[dict], rounds: int | None
) -> tuple[dict[int, int], int, float]:
    """스탯 거리 최소 그리디 1:1 정렬 → (매핑, 임계내 인원, 총 거리)."""
    pairs: list[tuple[float, int, int]] = []
    for i, row in enumerate(rows):
        for j, r in enumerate(roster):
            c = _cost(row, r, rounds)
            if c is not None:
                pairs.append((c, i, j))
    pairs.sort(key=lambda x: x[0])
    used_i: set[int] = set()
    used_j: set[int] = set()
    mapping: dict[int, int] = {}
    matched = 0
    total = 0.0
    for c, i, j in pairs:
        if i in used_i or j in used_j:
            continue
        mapping[i] = j
        used_i.add(i)
        used_j.add(j)
        total += c
        if c <= _PAIR_TOL:
            matched += 1
    return mapping, matched, total


class Enricher:
    """배치(다중 업로드) 동안 클라이언트·매치리스트 캐시를 재사용."""

    def __init__(
        self, client: HenrikClient | None = None, max_seeds: int = _MAX_SEEDS
    ) -> None:
        self._client = client or HenrikClient()
        self._own = client is None
        self._max_seeds = max_seeds
        # (name.lower, tag.lower) → {"recent": [...], "custom": [...] | None}
        self._cache: dict[tuple[str, str], dict] = {}
        # match_id → puuid별 {fk, plants, defuses} (상세 응답 파싱 결과 캐시)
        self._detail: dict[str, dict[str, dict[str, int]]] = {}

    def close(self) -> None:
        if self._own:
            self._client.close()

    def _lists(self, name: str, tag: str) -> dict:
        key = (name.lower(), tag.lower())
        if key not in self._cache:
            self._cache[key] = {"recent": None, "custom": None}
        return self._cache[key]

    def _recent(self, name: str, tag: str) -> list[dict]:
        c = self._lists(name, tag)
        if c["recent"] is None:
            c["recent"] = self._client.get_matches(config.HENRIK_REGION, name, tag)
        return c["recent"]

    def _custom(self, name: str, tag: str) -> list[dict]:
        c = self._lists(name, tag)
        if c["custom"] is None:
            c["custom"] = self._client.get_matches(
                config.HENRIK_REGION, name, tag, mode="custom"
            )
        return c["custom"]

    def _detail_stats(self, match_id: str) -> dict[str, dict[str, int]]:
        """확정 매치의 puuid별 FK/설치/해제. 상세 조회 실패 시 빈 dict(best-effort)."""
        if match_id not in self._detail:
            try:
                detail = self._client.get_match(config.HENRIK_REGION, match_id)
                self._detail[match_id] = _detail_stats(detail)
            except Exception:
                self._detail[match_id] = {}
        return self._detail[match_id]

    def _best_match(
        self,
        rows: list[ExtractedRow],
        candidates: list[dict],
        played: date | None,
    ) -> dict | None:
        # 판별은 스탯이 주(主). 날짜는 보조 — 동률에 가까운 후보들의 tiebreak로만 쓴다
        # (하드 필터로 쓰면 OCR 날짜·타임존 오차로 정답이 걸러졌던 이력 있어 소프트).
        best_key: tuple[int, int, float] | None = None
        best_match: dict | None = None
        for m in candidates:
            roster = _parse_roster(m)
            if len(roster) < _MIN_MATCHED:
                continue
            _, matched, total = _assign(rows, roster, _total_rounds(m))
            if matched < _MIN_MATCHED:
                continue
            md = _match_date(m)
            date_bonus = -abs((md - played).days) if (played and md) else 0
            key = (matched, date_bonus, -total)
            if best_key is None or key > best_key:
                best_key, best_match = key, m
        return best_match

    def enrich(
        self, session: Session, result: ExtractionResult, played_at: str | None = None
    ) -> ExtractionResult:
        """result 를 제자리 보정. Henrik 실패/미매칭이면 원본 그대로 반환.

        played_at 은 스탯 판별의 보조조건(동률 tiebreak)으로만 쓴다.
        """
        played = _played_date(played_at)
        # 1) 시드 후보: OCR 닉이 등록 유저 & Riot 계정 보유. puuid 있는 계정 우선.
        seeds: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for row in result.rows:
            player = resolve_existing_player(session, row.nickname)
            if player is None:
                continue
            accts = session.scalars(
                select(PlayerRiotAccount)
                .where(PlayerRiotAccount.player_id == player.id)
                .order_by(PlayerRiotAccount.puuid.is_(None))  # puuid 보유 먼저
            ).all()
            for a in accts:
                key = (a.riot_name.lower(), a.riot_tag.lower())
                if key not in seen:
                    seen.add(key)
                    seeds.append((a.riot_name, a.riot_tag))

        # 2) 시드별로 히스토리 조회 → 지문매칭. 최근 리스트 먼저, 없으면 커스텀.
        for name, tag in seeds[:self._max_seeds]:
            try:
                found = self._best_match(result.rows, self._recent(name, tag), played)
                if found is None:
                    found = self._best_match(
                        result.rows, self._custom(name, tag), played
                    )
            except Exception:
                continue  # 이 시드는 실패 → 다음 시드
            if found is not None:
                self._apply(result, found)
                return result
        return result

    def _apply(self, result: ExtractionResult, match: dict) -> None:
        roster = _parse_roster(match)
        rounds = _total_rounds(match)
        mapping, _, _ = _assign(result.rows, roster, rounds)
        match_id = (match.get("metadata") or {}).get("match_id")
        detail = self._detail_stats(match_id) if match_id else {}
        corrections: list[Correction] = []

        for i, j in mapping.items():
            row = result.rows[i]
            r = roster[j]

            def fix(field: str, old, new) -> None:
                if new is None or str(old) == str(new):
                    return
                corrections.append(Correction(
                    nickname=r["name"], field=field, old=str(old), new=str(new),
                ))

            new_acs = round(r["score"] / rounds) if (r["score"] and rounds) else None
            fix("ACS", row.acs, new_acs)
            fix("K", row.kills, r["k"])
            fix("D", row.deaths, r["d"])
            fix("A", row.assists, r["a"])
            new_agent = config.agent_kr_from_en(r["agent_en"])
            fix("요원", row.agent_kr, new_agent)
            fix("닉", row.nickname, r["name"])
            obj = detail.get(r["puuid"]) if r["puuid"] else None
            new_fk = obj["fk"] if obj else None
            new_plants = obj["plants"] if obj else None
            new_defuses = obj["defuses"] if obj else None
            fix("FK", row.first_kills, new_fk)
            fix("설치", row.plants, new_plants)
            fix("해제", row.defuses, new_defuses)

            if new_acs is not None:
                row.acs = new_acs
            if r["k"] is not None:
                row.kills = r["k"]
            if r["d"] is not None:
                row.deaths = r["d"]
            if r["a"] is not None:
                row.assists = r["a"]
            if new_agent is not None:
                row.agent_kr = new_agent
            if r["name"]:
                row.nickname = r["name"]
            if obj:
                row.first_kills = obj["fk"]
                row.plants = obj["plants"]
                row.defuses = obj["defuses"]

        # 팀 소속·팀별 라운드를 Henrik 권위값으로 확정(스펙 §7). OCR 팀칸/점수칸
        # 오배정으로 승패가 뒤집히던 문제(2026-07-22 대규모 교정)의 재발 방지.
        # A/B 라벨은 임의라 '현재 A로 찍힌 다수'가 속한 팀을 A로 유지(표시 안정).
        tr = _team_rounds(match)
        row_tid = {i: roster[j]["team_id"] for i, j in mapping.items()
                   if roster[j]["team_id"] is not None}
        if len(tr) == 2 and row_tid:
            from collections import Counter
            a_side = Counter(
                row_tid[i] for i in row_tid if result.rows[i].team == "A"
            )
            tidA = a_side.most_common(1)[0][0] if a_side else next(iter(tr))
            tidB = next(t for t in tr if t != tidA)
            for i, tid in row_tid.items():
                new_team = "A" if tid == tidA else "B"
                if result.rows[i].team != new_team:
                    corrections.append(Correction(
                        nickname=result.rows[i].nickname, field="팀",
                        old=result.rows[i].team, new=new_team,
                    ))
                    result.rows[i].team = new_team
            new_a, new_b = tr[tidA], tr[tidB]
            if (result.team_a_rounds, result.team_b_rounds) != (new_a, new_b):
                corrections.append(Correction(
                    nickname="", field="라운드",
                    old=f"{result.team_a_rounds}-{result.team_b_rounds}",
                    new=f"{new_a}-{new_b}",
                ))
                result.team_a_rounds, result.team_b_rounds = new_a, new_b

        if not result.map_name:
            result.map_name = (match.get("metadata") or {}).get("map", {}).get("name")
        result.henrik_match_id = (match.get("metadata") or {}).get("match_id")
        result.corrections = corrections
