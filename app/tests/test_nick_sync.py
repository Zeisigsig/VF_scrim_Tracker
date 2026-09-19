"""리네임 추종(services.sync_player_nick) 테스트.

인게임 닉 변경(Monalisa→Orbit)이 발로닉·별칭·Riot 계정에 어떻게 반영되는지,
그리고 건드리면 안 되는 경우(별칭 충돌·부계정)를 지키는지 확인한다.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.models import Base, Player, PlayerAlias, PlayerRiotAccount
from app.services import sync_player_nick


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _make(session: Session, nick: str, riot: tuple[str, str], puuid: str | None) -> Player:
    p = Player(display_name=nick)
    session.add(p)
    session.flush()
    session.add(PlayerRiotAccount(
        player_id=p.id, riot_name=riot[0], riot_tag=riot[1], puuid=puuid,
    ))
    session.flush()
    return p


def _aliases(session: Session, player_id: int) -> set[str]:
    return set(session.scalars(
        select(PlayerAlias.alias).where(PlayerAlias.player_id == player_id)
    ))


def test_rename_updates_nick_and_keeps_old_as_alias(session: Session) -> None:
    p = _make(session, "Monalisa", ("Monalisa", "KR1"), "puuid-1")

    logs = sync_player_nick(session, p.id, "Orbit", "KR1", "puuid-1")

    assert p.display_name == "Orbit"
    # 옛 닉은 과거 스크린샷 OCR 이 계속 붙도록, 새 닉은 다음 스크린샷이
    # 자동매칭되도록 둘 다 별칭으로 남는다.
    assert _aliases(session, p.id) == {"Monalisa", "Orbit"}
    acct = session.scalars(select(PlayerRiotAccount)).one()
    assert (acct.riot_name, acct.riot_tag) == ("Orbit", "KR1")
    assert any("Monalisa → Orbit" in m for m in logs)


def test_no_change_is_silent(session: Session) -> None:
    p = _make(session, "Orbit", ("Orbit", "KR1"), "puuid-1")

    assert sync_player_nick(session, p.id, "Orbit", "KR1", "puuid-1") == []
    assert _aliases(session, p.id) == set()


def test_alias_taken_by_other_player_is_reported_not_stolen(session: Session) -> None:
    p = _make(session, "Monalisa", ("Monalisa", "KR1"), "puuid-1")
    other = _make(session, "다른사람", ("Other", "KR1"), "puuid-2")
    session.add(PlayerAlias(player_id=other.id, alias="Monalisa"))
    session.flush()

    logs = sync_player_nick(session, p.id, "Orbit", "KR1", "puuid-1")

    assert p.display_name == "Orbit"
    # 남의 별칭을 뺏지 않는다(전역 유니크라 조용히 넘기면 OCR 오매칭이 남는다).
    taken = session.scalar(select(PlayerAlias).where(PlayerAlias.alias == "Monalisa"))
    assert taken.player_id == other.id
    assert any("경고" in m and "Monalisa" in m for m in logs)


def test_multi_account_player_keeps_nick(session: Session) -> None:
    p = _make(session, "본계닉", ("본계닉", "KR1"), "puuid-main")
    session.add(PlayerRiotAccount(
        player_id=p.id, riot_name="부계닉", riot_tag="KR2", puuid="puuid-alt",
    ))
    session.flush()

    logs = sync_player_nick(session, p.id, "부계닉", "KR2", "puuid-alt")

    # 어느 계정 닉을 대표로 쓸지 규칙이 없어 자동 갱신하지 않고 알리기만 한다.
    assert p.display_name == "본계닉"
    assert any("보류" in m for m in logs)


def test_puuid_is_backfilled_from_roster(session: Session) -> None:
    p = _make(session, "Monalisa", ("Monalisa", "KR1"), None)

    logs = sync_player_nick(session, p.id, "Monalisa", "KR1", "puuid-1")

    acct = session.scalars(select(PlayerRiotAccount)).one()
    assert acct.puuid == "puuid-1"
    assert any("puuid 보강" in m for m in logs)
