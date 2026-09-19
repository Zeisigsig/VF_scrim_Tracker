"""리네임으로 갈라진 중복 유저 탐지 (병합 후보 수집).

puuid 가 없는 유저가 인게임 닉을 바꾸면 적재 매칭이 name#tag 로 떨어져
(ingest_match._resolve) 기존 유저에 붙지 못하고 **새 유저가 생긴다.** 그런데
옛 유저는 puuid 가 없고 저장된 name#tag 는 이미 죽어 있어서(404),
backfill_puuid 로도 puuid 를 채울 수 없다 → 둘이 같은 사람인지 비교할 키가
없다.

**풀이:** 옛 유저가 뛴 확정 경기를 Henrik 상세로 다시 받아, 저장해 둔 스탯
(K/D/A/ACS)으로 로스터에서 그 사람을 특정하면 **그 항목의 puuid** 가 나온다.
그 puuid 를 이미 다른 유저가 쥐고 있으면 둘은 같은 Riot 계정 = 동일인이다
(puuid 는 계정 불변 ID라 판정이 확정적이다).

이 도구는 **읽고 채우기만 한다.** 병합은 되돌릴 수 없어 일상 작업에 묻히면
안 되므로, 후보만 모아 `app_settings` 에 저장하고 실제 병합은 웹 유저 관리
페이지의 '병합 후보' 섹션에서 사람이 확인하고 누른다.

실행(VM):
    uv run python -m app.tools.merge_renamed [--dry-run] [--limit N]

계정당 Henrik 상세 1회(약 2.2초). 먼저 backfill_puuid 를 돌려 채울 수 있는
것을 채운 뒤 남은 유저만 대상으로 하면 빠르다.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from sqlalchemy import select

from app import config
from app.db.models import (
    Match,
    MatchPlayer,
    Player,
    PlayerRiotAccount,
    PlayerTier,
    User,
)
from app.db.session import MERGE_CANDIDATES_KEY, SessionLocal, set_setting
from app.henrik.client import HenrikClient
from app.henrik.enrich import _parse_roster, _team_rounds
from app.services import sync_player_nick

# 로스터 항목 특정: |ΔK|+|ΔD|+|ΔA|+0.5·|ΔACS| 가 이 값 이하 & 2등과 충분히
# 벌어져야 채택. enrich 의 지문매칭과 같은 척도(_PAIR_TOL=6.0)를 쓴다.
_PAIR_TOL = 6.0
_MIN_GAP = 4.0
# 한 유저당 시도할 경기 수. 한 판이면 대개 충분하지만 그 판의 스탯이 다른
# 사람과 겹치면(동점) 다음 판으로 넘어간다.
_MAX_MATCH_TRIES = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _match_count(session, player_id: int) -> int:
    return len(list(session.scalars(
        select(MatchPlayer.id).where(MatchPlayer.player_id == player_id)
    )))


def _has_login(session, player_id: int) -> bool:
    return session.scalar(select(User).where(User.player_id == player_id)) is not None


def _has_manual_tier(session, player_id: int) -> bool:
    return session.scalar(select(PlayerTier).where(
        PlayerTier.player_id == player_id, PlayerTier.source == "manual"
    )) is not None


def _pick_survivor(session, a: Player, b: Player) -> tuple[Player, Player, str]:
    """(생존자, 흡수될 쪽, 근거). 사람 손이 닿은 흔적이 많은 쪽을 남긴다.

    created_at 만으로 고르지 않는 이유: 보통은 옛 유저가 생존자가 맞지만,
    수동 티어·로그인 계정이 새 쪽에 붙어 있으면 그것을 잃는 손실이 더 크다.
    """
    for label, fn in (
        ("로그인 계정 보유", _has_login),
        ("수동 티어 보유", _has_manual_tier),
    ):
        a_has, b_has = fn(session, a.id), fn(session, b.id)
        if a_has != b_has:
            return (a, b, label) if a_has else (b, a, label)
    ca, cb = _match_count(session, a.id), _match_count(session, b.id)
    if ca != cb:
        return (a, b, "판수 많음") if ca > cb else (b, a, "판수 많음")
    return (a, b, "먼저 생성됨") if a.id < b.id else (b, a, "먼저 생성됨")


def _fingerprint_puuid(session, client, player: Player) -> tuple[str, str, str] | None:
    """확정 경기 로스터에서 이 유저의 puuid 를 복원. (puuid, riot_name, riot_tag)."""
    rows = session.execute(
        select(MatchPlayer, Match)
        .join(Match, Match.id == MatchPlayer.match_id)
        .where(MatchPlayer.player_id == player.id, Match.status == "confirmed")
        .order_by(Match.played_at.desc())
    ).all()
    # Henrik 매치 ID 는 두 군데에 있다. CLI 적재분은 external_match_id 에,
    # 스크린샷 업로드분은 enrich 가 지문매칭으로 찾아 넣은
    # extraction_raw.henrik_match_id 에 들어 있다(로컬 기준 58판 중 48판이 후자).
    tries = []
    for mp, match in rows:
        hid = match.external_match_id
        if not hid and isinstance(match.extraction_raw, dict):
            hid = match.extraction_raw.get("henrik_match_id")
        if hid:
            tries.append((mp, hid))
        if len(tries) >= _MAX_MATCH_TRIES:
            break
    for mp, hid in tries:
        try:
            detail = client.get_match(config.HENRIK_REGION, hid)
        except Exception as e:
            print(f"      (상세 조회 실패 {hid}: {type(e).__name__})")
            continue
        roster = _parse_roster(detail)
        tr = _team_rounds(detail)
        rounds = sum(tr.values()) or 1
        scored = []
        for r in roster:
            acs = round(r["score"] / rounds) if r["score"] else 0
            dist = (
                abs((r["k"] or 0) - mp.kills)
                + abs((r["d"] or 0) - mp.deaths)
                + abs((r["a"] or 0) - mp.assists)
                + 0.5 * abs(acs - mp.acs)
            )
            scored.append((dist, r))
        scored.sort(key=lambda x: x[0])
        if not scored or scored[0][0] > _PAIR_TOL:
            continue
        if len(scored) > 1 and scored[1][0] - scored[0][0] < _MIN_GAP:
            continue  # 동점 후보가 있어 특정 불가 → 다음 경기
        r = scored[0][1]
        if r["puuid"]:
            return r["puuid"], r["name"] or "", r["tag"] or ""
    return None


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    limit = None
    if "--limit" in argv:
        limit = int(argv[argv.index("--limit") + 1])

    session = SessionLocal()
    client = HenrikClient()
    try:
        # puuid 를 하나도 못 가진 유저만 대상(부계 중 하나만 없는 경우는 제외).
        with_puuid = {
            pid for (pid,) in session.execute(
                select(PlayerRiotAccount.player_id).where(
                    PlayerRiotAccount.puuid.is_not(None),
                    PlayerRiotAccount.puuid != "",
                )
            ).all()
        }
        targets = [
            p for p in session.scalars(select(Player).order_by(Player.id))
            if p.id not in with_puuid and _match_count(session, p.id) > 0
        ]
        if limit:
            targets = targets[:limit]
        print(f"puuid 없는 유저 {len(targets)}명 조회{' (dry-run)' if dry_run else ''}")
        print(f"예상 소요: 약 {len(targets) * 2.2 / 60:.1f}분 이상 (경기당 상세 1회)\n")

        filled: list[str] = []
        candidates: list[dict] = []
        unknown: list[str] = []

        for i, player in enumerate(targets, 1):
            head = f"[{i}/{len(targets)}] {player.display_name}"
            found = _fingerprint_puuid(session, client, player)
            if found is None:
                unknown.append(player.display_name)
                print(f"{head} — 특정 실패(확정 경기 없음/스탯 동점)")
                continue
            puuid, riot_name, riot_tag = found
            owner = session.scalar(
                select(PlayerRiotAccount).where(PlayerRiotAccount.puuid == puuid)
            )
            if owner is None:
                # 아무도 안 쥐고 있음 → 이 유저 계정에 그대로 채운다(갈라지지 않음).
                acct = session.scalars(select(PlayerRiotAccount).where(
                    PlayerRiotAccount.player_id == player.id
                )).first()
                if acct is None:
                    session.add(PlayerRiotAccount(
                        player_id=player.id, riot_name=riot_name,
                        riot_tag=riot_tag, puuid=puuid,
                    ))
                else:
                    acct.puuid = puuid
                session.flush()
                for msg in sync_player_nick(
                    session, player.id, riot_name, riot_tag, puuid
                ):
                    print(f"      [닉] {msg}")
                filled.append(f"{player.display_name} ← {riot_name}#{riot_tag}")
                print(f"{head} — puuid 복원 ({riot_name}#{riot_tag})")
                continue

            if owner.player_id == player.id:
                print(f"{head} — 이미 본인 계정")
                continue

            other = session.get(Player, owner.player_id)
            survivor, absorbed, reason = _pick_survivor(session, player, other)
            candidates.append({
                "source_id": absorbed.id,
                "target_id": survivor.id,
                "source_label": absorbed.display_name,
                "target_label": survivor.display_name,
                "source_matches": _match_count(session, absorbed.id),
                "target_matches": _match_count(session, survivor.id),
                "puuid": puuid,
                "riot_name": riot_name,
                "riot_tag": riot_tag,
                "reason": reason,
                "detected_at": _now(),
            })
            print(f"{head} — **병합 후보**: {absorbed.display_name} → "
                  f"{survivor.display_name} ({reason}, {riot_name}#{riot_tag})")

        print(f"\n{'=' * 60}")
        print(f"puuid 복원 {len(filled)}명 / 병합 후보 {len(candidates)}건 / "
              f"특정 실패 {len(unknown)}명")
        if candidates:
            print("\n병합 후보 (웹 유저 관리 → '병합 후보' 에서 확인 후 실행):")
            for c in candidates:
                print(f"  - {c['source_label']}({c['source_matches']}판) → "
                      f"{c['target_label']}({c['target_matches']}판)  [{c['reason']}]")
        if unknown:
            print("\n특정 실패 (확정 경기가 없거나 스탯이 동점):")
            for name in unknown:
                print(f"  - {name}")

        if dry_run:
            session.rollback()
            print("\n(dry-run: 저장 안 함)")
        else:
            set_setting(session, MERGE_CANDIDATES_KEY, json.dumps(
                candidates, ensure_ascii=False
            ))
            session.commit()
            print("\n저장 완료. 병합은 웹 유저 관리 페이지에서 하세요.")
        return 0
    finally:
        client.close()
        session.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
