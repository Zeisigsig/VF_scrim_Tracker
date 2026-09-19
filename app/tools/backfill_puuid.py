"""등록 유저 Riot 계정의 puuid 일괄 보강 (1회성).

puuid 는 Riot 계정의 불변 ID로 **인게임 닉을 바꿔도 변하지 않는다.** 이게 비어
있으면 적재 매칭이 name#tag 로 떨어져서(ingest_match._resolve), 닉을 바꾼 사람이
기존 유저에 붙지 못하고 신규 유저로 갈라진다. 리네임 추종
(services.sync_player_nick)도 puuid 로 신원이 확정된 경우에만 동작하므로,
한 번 전수로 채워 두면 이후로는 닉을 몇 번 바꾸든 자동으로 따라간다.

실행(VM):
    uv run python -m app.tools.backfill_puuid [--dry-run]

Henrik 30req/60s 페이싱이라 계정 하나당 약 2.2초 걸린다.

**이미 닉을 바꾼 사람은 저장된 name#tag 가 낡아 조회가 404 로 실패한다**(조회
방향이 name#tag→puuid 뿐이라 구조적 한계). 실패 목록을 마지막에 따로 출력하니,
최신 Riot ID 를 확인해 유저 관리에서 고치거나 `ingest_match --link` 로 연결한 뒤
다시 돌리면 된다. 그냥 두어도 그 사람이 낀 내전이 한 번 적재되면 로스터에서
puuid 가 채워진다.
"""
from __future__ import annotations

import sys

import httpx
from sqlalchemy import select

from app.db.models import Player, PlayerRiotAccount
from app.db.session import SessionLocal
from app.henrik.client import HenrikClient
from app.services import sync_player_nick


def _label(session, acct: PlayerRiotAccount) -> str:
    player = session.get(Player, acct.player_id)
    who = player.display_name if player else f"player#{acct.player_id}"
    return f"{who} ({acct.riot_name}#{acct.riot_tag})"


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv

    session = SessionLocal()
    client = HenrikClient()
    try:
        targets = list(session.scalars(
            select(PlayerRiotAccount).where(
                (PlayerRiotAccount.puuid.is_(None)) | (PlayerRiotAccount.puuid == "")
            )
        ))
        total_accounts = len(list(session.scalars(select(PlayerRiotAccount))))
        print(f"Riot 계정 {total_accounts}개 중 puuid 없는 계정 {len(targets)}개"
              f"{' (dry-run)' if dry_run else ''}")
        if not targets:
            print("보강할 계정이 없습니다.")
            return 0
        print(f"예상 소요: 약 {len(targets) * 2.2 / 60:.1f}분 (Henrik 페이싱)\n")

        filled: list[str] = []
        failed: list[tuple[str, str]] = []
        conflicts: list[str] = []

        for i, acct in enumerate(targets, 1):
            label = _label(session, acct)
            try:
                data = client.get_account(acct.riot_name, acct.riot_tag)
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                why = "Riot ID 없음(닉 변경 의심)" if code == 404 else f"HTTP {code}"
                failed.append((label, why))
                print(f"[{i}/{len(targets)}] 실패 {label} — {why}")
                continue
            except Exception as e:  # 네트워크 등
                failed.append((label, type(e).__name__))
                print(f"[{i}/{len(targets)}] 실패 {label} — {type(e).__name__}")
                continue

            puuid = data.get("puuid")
            if not puuid:
                failed.append((label, "응답에 puuid 없음"))
                print(f"[{i}/{len(targets)}] 실패 {label} — 응답에 puuid 없음")
                continue

            # puuid 는 전역 유니크. 다른 계정 행이 이미 쥐고 있으면 같은 사람이
            # 두 유저로 갈라져 있다는 뜻이라, 덮어쓰지 않고 병합 대상으로 보고한다.
            owner = session.scalar(
                select(PlayerRiotAccount).where(PlayerRiotAccount.puuid == puuid)
            )
            if owner is not None and owner.id != acct.id:
                msg = f"{label} ↔ {_label(session, owner)} — 같은 puuid (병합 대상)"
                conflicts.append(msg)
                print(f"[{i}/{len(targets)}] 충돌 {msg}")
                continue

            acct.puuid = puuid
            filled.append(label)
            print(f"[{i}/{len(targets)}] 보강 {label}")
            # 조회가 성공했다는 건 저장된 name#tag 가 현재값이라는 뜻 → 발로닉이
            # 어긋나 있으면 이 김에 맞춘다(응답의 표기가 정본).
            for msg in sync_player_nick(
                session, acct.player_id,
                data.get("name") or acct.riot_name,
                data.get("tag") or acct.riot_tag,
                puuid,
            ):
                print(f"        [닉] {msg}")

        print(f"\n{'=' * 60}")
        print(f"보강 {len(filled)}개 / 실패 {len(failed)}개 / 충돌 {len(conflicts)}개")
        if failed:
            print("\n실패 (닉을 바꿨을 가능성 — 최신 Riot ID 확인 후 재실행):")
            for label, why in failed:
                print(f"  - {label}: {why}")
        if conflicts:
            print("\n충돌 (같은 puuid = 중복 유저, merge_players 로 정리):")
            for msg in conflicts:
                print(f"  - {msg}")

        if dry_run:
            session.rollback()
            print("\n(dry-run: 저장 안 함)")
        else:
            session.commit()
            print("\n저장 완료.")
        return 0
    finally:
        client.close()
        session.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
