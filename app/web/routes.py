"""웹 라우트 (Jinja2 서버 렌더링) + JSON API (스펙 §6)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from fastapi import (
    APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import config
from app.auth import (
    AuthUser,
    COOKIE_NAME,
    ensure_accounts,
    hash_password,
    make_session_cookie,
    require_admin,
    require_user,
    reset_password,
    verify_password,
)
from app.db.models import (
    Match,
    MatchPlayer,
    MatchRating,
    Player,
    PlayerAlias,
    PlayerRiotAccount,
    PlayerTier,
    SkillRating,
    User,
)
from app.db.session import get_session
from app.ingest.matcher import match_nickname, register_alias
from app.ingest.schemas import ExtractionResult
from app.ingest.validator import validate
from app.rating.leaderboard import eb_adjusted_score, eb_adjusted_tacr
from app.services import (
    ConfirmedRow,
    delete_player_permanently,
    get_or_create_player,
    merge_players,
    resolve_existing_player,
    save_and_rate,
)

router = APIRouter()


def _user_ctx(request: Request) -> dict:
    """모든 템플릿에 로그인 사용자/어드민 여부를 주입(base.html 내비 등)."""
    user = getattr(request.state, "user", None)
    return {"user": user, "is_admin": bool(user and user.is_admin)}


templates = Jinja2Templates(
    directory=str(config.BASE_DIR / "app" / "web" / "templates"),
    context_processors=[_user_ctx],
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- 인증 / 대문 --------------------------------------------------------

def _tier_distribution(session: Session) -> list[dict]:
    """내전 참가자(확정 경기 1판 이상)의 수동 티어 분포. 대문 원 그래프용.

    수동 티어 미설정 참가자는 '미설정'으로 묶는다. 점수 노출 없음(공개 안전).
    """
    played_pids = set(session.scalars(
        select(MatchPlayer.player_id)
        .join(Match, Match.id == MatchPlayer.match_id)
        .where(Match.status == "confirmed")
    ).all())
    counts: dict[str, int] = {}
    for pid in played_pids:
        manual = session.scalar(
            select(PlayerTier).where(PlayerTier.player_id == pid, PlayerTier.source == "manual")
            .order_by(PlayerTier.recorded_at.desc())
        )
        name = config.tier_name(manual.tier_value) if manual else "미설정"
        counts[name] = counts.get(name, 0) + 1
    order = list(config.TIER_TABLE.keys()) + ["미설정"]
    return [{"tier": t, "count": counts[t]} for t in order if t in counts]


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, session: Session = Depends(get_session), error: str = ""):
    if getattr(request.state, "user", None) is not None:
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        request, "landing.html",
        {"tier_dist": _tier_distribution(session), "error": error},
    )


@router.post("/login")
def login_submit(
    username: str = Form(...), password: str = Form(...),
    session: Session = Depends(get_session),
):
    user = session.scalar(select(User).where(User.username == username.strip()))
    if user is None or not verify_password(password, user.password_hash):
        return RedirectResponse(url="/login?error=1", status_code=303)
    resp = RedirectResponse(
        url="/account/password" if user.must_change_password else "/", status_code=303
    )
    # max_age/expires 미지정 = 세션 쿠키 → 브라우저 종료 시 삭제(창 끄면 로그아웃).
    resp.set_cookie(
        COOKIE_NAME, make_session_cookie(user.id),
        httponly=True, samesite="lax",
    )
    return resp


@router.get("/logout")
def logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


@router.get("/account/password", response_class=HTMLResponse)
def password_form(
    request: Request, user: AuthUser = Depends(require_user), error: str = "",
):
    return templates.TemplateResponse(
        request, "set_password.html",
        {"forced": user.must_change_password, "error": error},
    )


@router.post("/account/password")
def password_submit(
    request: Request, new_password: str = Form(...), confirm_password: str = Form(...),
    user: AuthUser = Depends(require_user), session: Session = Depends(get_session),
):
    pw = new_password.strip()
    if len(pw) < 4 or pw != confirm_password.strip():
        return RedirectResponse(url="/account/password?error=1", status_code=303)
    u = session.get(User, user.id)
    u.password_hash = hash_password(pw)
    u.must_change_password = False
    session.commit()
    return RedirectResponse(url="/", status_code=303)


# --- 홈 -----------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
def home(
    request: Request, user: AuthUser = Depends(require_user),
    session: Session = Depends(get_session), dup: str = "",
):
    matches = session.scalars(
        select(Match).where(Match.status == "confirmed").order_by(Match.played_at.desc()).limit(10)
    ).all()
    pending = session.scalars(
        select(Match).where(Match.status == "pending").order_by(Match.created_at.desc())
    ).all()
    dist = _score_distribution(_leaderboard_rows(session, min_games=1), user.player_id)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "matches": matches, "dist": dist,
            "pending": pending, "inbox_count": len(_inbox_files()),
            "dup_files": [n for n in dup.split(",") if n],
        },
    )


# --- 스크린샷 → 검토 대기 경기 생성 (공유 헬퍼, 스펙 §5.0) -----------------

def _inbox_files() -> list[Path]:
    """인박스 폴더의 처리 대기 이미지 목록."""
    if not config.INBOX_DIR.exists():
        return []
    return sorted(
        p for p in config.INBOX_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in config.IMAGE_EXTENSIONS
    )


def _store_pending_match(
    session: Session, result: ExtractionResult, *, screenshot_path: str = "",
    map_name: str = "", team_a_rounds: str = "", team_b_rounds: str = "",
    played_at: str = "", enricher=None,
) -> Match:
    """이미 추출된 결과로 검토 대기(pending) 경기를 저장. OCR·파일 저장을 하지 않는다.

    로컬 /upload(OCR 후)와 클라우드 /api/ingest(로컬이 보낸 결과) 공용 저장 로직.
    screenshot_path 는 파일이 아니라 중복 방지·참조용 식별 문자열이다.
    enricher 가 주어지면 Henrik 자동 보정을 시도(best-effort).
    """
    match = Match(
        played_at=played_at or _now(),
        map_name=map_name or None,
        team_a_rounds=int(team_a_rounds) if str(team_a_rounds).strip() else None,
        team_b_rounds=int(team_b_rounds) if str(team_b_rounds).strip() else None,
        status="pending",
        created_at=_now(),
    )
    session.add(match)
    session.flush()  # match_id 확보
    if screenshot_path:
        match.screenshot_path = screenshot_path

    if enricher is not None:
        try:
            enricher.enrich(session, result, match.played_at)
        except Exception:  # Henrik 실패는 업로드를 막지 않음
            pass
    match.extraction_raw = result.model_dump()
    if not match.map_name:
        match.map_name = result.map_name
    # Henrik 매칭 성공 시 팀별 라운드는 권위값(teams[].won) → 폼/OCR 손입력보다 우선.
    # (2026-07-22 승패 대규모 오류: 승자 점수를 A칸에 잘못 넣던 문제 재발 방지.)
    if result.henrik_match_id and result.team_a_rounds is not None:
        match.team_a_rounds = result.team_a_rounds
        match.team_b_rounds = result.team_b_rounds
    else:
        # 폼에 스코어 미입력 시 추출값으로 검토 화면을 미리 채운다(확신 없으면 None).
        if match.team_a_rounds is None:
            match.team_a_rounds = result.team_a_rounds
        if match.team_b_rounds is None:
            match.team_b_rounds = result.team_b_rounds
    session.commit()
    return match


def _create_pending_match(
    session: Session, raw: bytes, *, filename: str = "", map_name: str = "",
    team_a_rounds: str = "", team_b_rounds: str = "", played_at: str = "",
    enricher=None,
) -> Match:
    """원본 바이트를 로컬에서 OCR 해 검토 대기 경기를 생성(로컬 /upload·/inbox 전용).

    원본은 screenshots/{filename} 로 보관(파일명 미지정 시 uuid). 추출까지 로컬에서 수행.
    """
    from app.ingest.extractor import extract_scoreboard  # 지연 임포트(테스트 시 SDK 미필요)

    config.SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    fpath = config.SCREENSHOT_DIR / (filename or f"{uuid4().hex}.png")
    fpath.write_bytes(raw)
    result = extract_scoreboard(fpath)
    return _store_pending_match(
        session, result,
        screenshot_path=str(fpath.relative_to(config.BASE_DIR)),
        map_name=map_name, team_a_rounds=team_a_rounds,
        team_b_rounds=team_b_rounds, played_at=played_at, enricher=enricher,
    )


# --- 업로드 (다중 파일 지원, 스펙 §5.0) ---------------------------------

@router.get("/upload", response_class=HTMLResponse)
def upload_form(request: Request, _: AuthUser = Depends(require_admin)):
    if not config.ENABLE_LOCAL_UPLOAD:
        raise HTTPException(status_code=404, detail="local upload disabled")
    return templates.TemplateResponse(request, "upload.html")


@router.post("/upload")
async def upload_submit(
    screenshots: list[UploadFile] = File(...),
    map_name: str = Form(""),
    team_a_rounds: str = Form(""),
    team_b_rounds: str = Form(""),
    played_at: str = Form(""),
    session: Session = Depends(get_session),
    _: AuthUser = Depends(require_admin),
):
    if not config.ENABLE_LOCAL_UPLOAD:
        raise HTTPException(status_code=404, detail="local upload disabled")
    from app.ingest.backfill import _parse, _played_at
    from app.henrik.enrich import Enricher

    files = [f for f in screenshots if f.filename]
    created: list[Match] = []
    skipped: list[str] = []  # 같은 파일명이 이미 있어 건너뛴 목록
    enricher = Enricher() if config.HENRIK_API_KEY else None
    try:
        for f in files:
            # 경로 요소 제거(디렉터리 트래버설 방지) 후 원본 파일명 그대로 보관.
            name = Path(f.filename).name
            if not name or Path(name).suffix.lower() not in config.IMAGE_EXTENSIONS:
                continue
            if (config.SCREENSHOT_DIR / name).exists():
                skipped.append(name)
                continue
            raw = await f.read()
            # 파일명이 규칙({YYYYMMDD}_{맵}_{세션}_{판})과 맞으면 맵/날짜를 그 값으로 채운다.
            parsed = _parse(name)
            fn_map = parsed[3] if parsed else ""
            fn_at = _played_at(parsed[0], parsed[1], parsed[2], parsed[4], parsed[5]) if parsed else ""
            # 맵/스코어/날짜는 단일 업로드일 때만 폼값 적용 (다중은 검토에서 개별 입력).
            if len(files) == 1:
                m = _create_pending_match(
                    session, raw, filename=name, map_name=map_name or fn_map or "",
                    team_a_rounds=team_a_rounds, team_b_rounds=team_b_rounds,
                    played_at=played_at or fn_at or "", enricher=enricher,
                )
            else:
                m = _create_pending_match(
                    session, raw, filename=name, map_name=fn_map or "",
                    played_at=fn_at or "", enricher=enricher,
                )
            created.append(m)
    finally:
        if enricher is not None:
            enricher.close()

    # 중복이 없고 단일 성공이면 바로 검토 화면으로, 그 외엔 홈으로(중복 있으면 알림).
    if not skipped and len(created) == 1:
        return RedirectResponse(url=f"/review/{created[0].id}", status_code=303)
    url = "/"
    if skipped:
        url += "?dup=" + quote(",".join(skipped))
    return RedirectResponse(url=url, status_code=303)


# --- 원격 적재 API (로컬 OCR → 클라우드) --------------------------------

class IngestPayload(BaseModel):
    """로컬 push.py 가 보내는 추출 결과. 이미지 없이 값만 전송한다."""
    extraction: ExtractionResult
    filename: str
    map_name: str = ""
    team_a_rounds: str = ""
    team_b_rounds: str = ""
    played_at: str = ""


@router.post("/api/ingest")
def api_ingest(
    payload: IngestPayload,
    session: Session = Depends(get_session),
    x_ingest_key: str | None = Header(default=None, alias="X-Ingest-Key"),
):
    """로컬에서 OCR 한 추출 결과를 받아 검토 대기 경기로 저장(재OCR·이미지 저장 없음).

    인증은 세션이 아니라 공유 시크릿(X-Ingest-Key)으로 한다. Henrik 보정은 여기(클라우드)서 수행.
    """
    if not config.INGEST_API_KEY or x_ingest_key != config.INGEST_API_KEY:
        raise HTTPException(status_code=401, detail="invalid ingest key")

    # 파일명은 중복 방지·참조용 식별자로만 쓴다(기존 백필과 같은 키 공간 유지).
    name = Path(payload.filename).name
    screenshot_path = f"data/screenshots/{name}"
    if session.scalar(select(Match.id).where(Match.screenshot_path == screenshot_path)):
        return JSONResponse({"skipped": name})

    from app.henrik.enrich import Enricher

    enricher = Enricher() if config.HENRIK_API_KEY else None
    try:
        match = _store_pending_match(
            session, payload.extraction, screenshot_path=screenshot_path,
            map_name=payload.map_name, team_a_rounds=payload.team_a_rounds,
            team_b_rounds=payload.team_b_rounds, played_at=payload.played_at,
            enricher=enricher,
        )
    finally:
        if enricher is not None:
            enricher.close()
    return JSONResponse({"match_id": match.id, "review_url": f"/review/{match.id}"})


# --- 인박스 처리 (스펙 §5.0) --------------------------------------------

@router.post("/inbox/process")
def inbox_process(
    session: Session = Depends(get_session), _: AuthUser = Depends(require_admin),
):
    if not config.ENABLE_LOCAL_UPLOAD:
        raise HTTPException(status_code=404, detail="local upload disabled")
    """인박스 폴더의 모든 이미지를 검토 대기 경기로 전환.

    각 파일은 screenshots/{match_id}.png 로 이동(보관)되고 인박스에서 제거된다.
    """
    from app.henrik.enrich import Enricher

    created: list[Match] = []
    enricher = Enricher() if config.HENRIK_API_KEY else None
    try:
        for path in _inbox_files():
            if (config.SCREENSHOT_DIR / path.name).exists():
                continue  # 같은 파일명이 이미 보관됨 → 인박스에 남겨 사용자가 직접 처리
            raw = path.read_bytes()
            m = _create_pending_match(session, raw, filename=path.name, enricher=enricher)
            path.unlink()  # 인박스에서 제거 (원본은 screenshots/ 로 이미 이동됨)
            created.append(m)
    finally:
        if enricher is not None:
            enricher.close()

    if len(created) == 1:
        return RedirectResponse(url=f"/review/{created[0].id}", status_code=303)
    return RedirectResponse(url="/", status_code=303)


# --- 검토 --------------------------------------------------------------

@router.get("/review/{match_id}", response_class=HTMLResponse)
def review(
    match_id: int, request: Request, session: Session = Depends(get_session),
    _: AuthUser = Depends(require_admin),
):
    match = session.get(Match, match_id)
    if match is None or match.extraction_raw is None:
        return HTMLResponse("경기를 찾을 수 없음", status_code=404)
    result = ExtractionResult.model_validate(match.extraction_raw)
    warnings = validate(result)

    rows = []
    for idx, r in enumerate(result.rows):
        outcome = match_nickname(session, r.nickname)
        role = config.AGENT_ROLE.get(r.agent_kr, "")
        # 정확 일치는 아니지만 최상위 후보 유사도가 임계값 이상이면 그 유저를 기본 제안.
        suggested_player_id = None
        if outcome.exact_player_id is None and outcome.candidates:
            top = outcome.candidates[0]
            if top.score >= config.NICKNAME_AUTOMATCH_MIN:
                suggested_player_id = top.player_id
        exact_label = None
        if outcome.exact_player_id is not None:
            ep = session.get(Player, outcome.exact_player_id)
            exact_label = ep.label if ep else None
        rows.append({
            "idx": idx, "row": r, "role": role,
            "exact_player_id": outcome.exact_player_id,
            "exact_label": exact_label,
            "suggested_player_id": suggested_player_id,
            "candidates": outcome.candidates,
        })
    return templates.TemplateResponse(
        request,
        "review.html",
        {
            "match": match, "rows": rows,
            "warnings": warnings, "agents": sorted(config.valid_agents()),
            "roles": list(config.ROLE_COEF.keys()),
            "agent_role": config.AGENT_ROLE,
            "corrections": result.corrections,
            "henrik_match_id": result.henrik_match_id,
        },
    )


@router.get("/api/resolve-nick")
def resolve_nick(
    name: str, session: Session = Depends(get_session),
    _: AuthUser = Depends(require_admin),
):
    """검토 화면에서 닉을 수정하면 어떤 유저로 매칭될지 미리보기(생성 없음)."""
    p = resolve_existing_player(session, name.strip()) if name.strip() else None
    if p is None:
        return {"matched": False}
    label = p.display_name if p.departed else p.label
    return {"matched": True, "player_id": p.id, "label": label}


@router.post("/review/{match_id}/confirm")
async def confirm(
    match_id: int, request: Request, session: Session = Depends(get_session),
    _: AuthUser = Depends(require_admin),
):
    match = session.get(Match, match_id)
    if match is None:
        return HTMLResponse("경기를 찾을 수 없음", status_code=404)
    form = await request.form()

    n = int(form.get("row_count", 0))
    confirmed: list[ConfirmedRow] = []
    for i in range(n):
        def g(field: str, default: str = "") -> str:
            return str(form.get(f"{field}_{i}", default))

        nickname = g("nickname")
        resolution = g("player_res")  # 'new' | 'existing:<pid>'
        if resolution.startswith("existing:"):
            player_id = int(resolution.split(":", 1)[1])
        else:
            player = get_or_create_player(session, nickname)
            player_id = player.id
        register_alias(session, player_id, nickname)

        agent = g("agent")
        role = g("role") or config.AGENT_ROLE.get(agent, "initiator")

        def gi(field: str) -> int | None:
            v = g(field)
            return int(v) if v.strip() else None

        confirmed.append(
            ConfirmedRow(
                player_id=player_id, team=g("team", "A"), agent=agent, role=role,
                acs=int(g("acs", "0")), kills=int(g("kills", "0")),
                deaths=int(g("deaths", "0")), assists=int(g("assists", "0")),
                econ=gi("econ"), first_kills=gi("first_kills"),
                plants=gi("plants"), defuses=gi("defuses"),
            )
        )

    # 스코어(라운드) 수정값 반영
    ta, tb = form.get("team_a_rounds"), form.get("team_b_rounds")
    match.team_a_rounds = int(ta) if ta and str(ta).strip() else match.team_a_rounds
    match.team_b_rounds = int(tb) if tb and str(tb).strip() else match.team_b_rounds

    save_and_rate(session, match, confirmed)
    session.commit()

    # 헤드투헤드(유저간 킬 구도) 갱신 — best-effort, Henrik 실패는 확정 결과에 무해.
    if config.HENRIK_API_KEY:
        from app.henrik.client import HenrikClient
        from app.henrik.head_to_head import populate_match
        client = HenrikClient()
        try:
            populate_match(session, match, client)
            session.commit()
        except Exception:
            session.rollback()
        finally:
            client.close()

    return RedirectResponse(url=f"/match/{match.id}", status_code=303)


# --- 경기 상세 ----------------------------------------------------------

def _match_detail(session: Session, match_id: int) -> dict | None:
    match = session.get(Match, match_id)
    if match is None:
        return None
    rows = session.execute(
        select(MatchPlayer, MatchRating, Player)
        .join(MatchRating, MatchRating.match_player_id == MatchPlayer.id, isouter=True)
        .join(Player, Player.id == MatchPlayer.player_id)
        .where(MatchPlayer.match_id == match_id)
    ).all()
    items = []
    for mp, rating, player in rows:
        manual = session.scalar(
            select(PlayerTier).where(
                PlayerTier.player_id == player.id, PlayerTier.source == "manual"
            ).order_by(PlayerTier.recorded_at.desc())
        )
        items.append({
            "mp": mp, "rating": rating, "player": player,
            "rank": config.tier_name(manual.tier_value) if manual else None,
            "display_score": rating.display_score if rating else None,
            "tacr": rating.tacr if rating else None,
            "expected_acs": rating.expected_acs if rating else None,
        })
    items.sort(key=lambda x: (x["display_score"] or 0), reverse=True)
    return {"match": match, "items": items, "agents": sorted(config.valid_agents())}


@router.get("/match/{match_id}", response_class=HTMLResponse)
def match_detail(
    match_id: int, request: Request, session: Session = Depends(get_session),
    _: AuthUser = Depends(require_user),
):
    data = _match_detail(session, match_id)
    if data is None:
        return HTMLResponse("경기를 찾을 수 없음", status_code=404)
    return templates.TemplateResponse(request, "match.html", data)


@router.post("/match/{match_id}/map")
def set_map_name(
    match_id: int, map_name: str = Form(""), next: str = Form("/"),
    session: Session = Depends(get_session), _: AuthUser = Depends(require_admin),
):
    match = session.get(Match, match_id)
    if match is None:
        return HTMLResponse("경기를 찾을 수 없음", status_code=404)
    match.map_name = map_name.strip() or None
    session.commit()
    return RedirectResponse(url=next, status_code=303)


@router.post("/match/{match_id}/score")
async def edit_match(
    match_id: int, request: Request, session: Session = Depends(get_session),
    _: AuthUser = Depends(require_admin),
):
    """확정 경기의 맵·스코어 및 각 선수의 요원·ACS·K/D/A 원시값 전체 수정.
    원시값이 바뀌면 TACR·표시점수·OpenSkill 이 모두 영향을 받고 OpenSkill 은 누적이라
    부분 갱신이 불가하므로 수정 후 전 경기를 재계산한다."""
    match = session.get(Match, match_id)
    if match is None:
        return HTMLResponse("경기를 찾을 수 없음", status_code=404)
    form = await request.form()

    match.map_name = str(form.get("map_name", "")).strip() or None
    ta, tb = str(form.get("team_a_rounds", "")), str(form.get("team_b_rounds", ""))
    match.team_a_rounds = int(ta) if ta.strip() else None
    match.team_b_rounds = int(tb) if tb.strip() else None

    mps = session.scalars(
        select(MatchPlayer).where(MatchPlayer.match_id == match_id)
    ).all()
    valid = config.valid_agents()
    for mp in mps:
        def g(field: str, default: str) -> str:
            return str(form.get(f"{field}_{mp.id}", default))

        def gi(field: str) -> int:
            v = g(field, "").strip()
            return int(v) if v else 0

        team = g("team", mp.team).strip().upper()
        if team in ("A", "B"):
            mp.team = team
        agent = g("agent", mp.agent).strip()
        if agent in valid:
            mp.agent = agent
            mp.role = config.AGENT_ROLE.get(agent, mp.role)
        mp.acs = gi("acs")
        mp.kills = gi("kills")
        mp.deaths = gi("deaths")
        mp.assists = gi("assists")
    session.commit()

    from app.calibration.recompute import recompute_all
    recompute_all()
    return RedirectResponse(url=f"/match/{match_id}", status_code=303)


@router.post("/match/{match_id}/delete")
def delete_match(
    match_id: int, session: Session = Depends(get_session),
    _: AuthUser = Depends(require_admin),
):
    """잘못 등록된 경기 삭제. MatchPlayer·MatchRating 은 cascade 로 함께 삭제되고,
    OpenSkill 은 누적이라 삭제 후 전 경기를 재계산한다(스크린샷 파일은 보존)."""
    match = session.get(Match, match_id)
    if match is None:
        return HTMLResponse("경기를 찾을 수 없음", status_code=404)
    session.delete(match)
    session.commit()

    from app.calibration.recompute import recompute_all
    recompute_all()
    return RedirectResponse(url="/", status_code=303)


# --- 선수 프로필 --------------------------------------------------------

def _match_result(match: Match, team: str) -> str | None:
    """해당 팀 관점의 경기 결과 'win'|'loss'|'draw'. 스코어 미입력이면 None(집계 제외)."""
    a, b = match.team_a_rounds, match.team_b_rounds
    if a is None or b is None:
        return None
    if a == b:
        return "draw"
    my, opp = (a, b) if team == "A" else (b, a)
    return "win" if my > opp else "loss"


@router.get("/player/{player_id}", response_class=HTMLResponse)
def player_profile(
    player_id: int, request: Request, session: Session = Depends(get_session),
    user: AuthUser = Depends(require_user),
):
    player = session.get(Player, player_id)
    if player is None:
        return HTMLResponse("유저를 찾을 수 없음", status_code=404)
    # 점수(표시점수·평균) 노출은 어드민이거나 본인 프로필일 때만.
    can_see_scores = user.is_admin or user.player_id == player_id

    history = session.execute(
        select(Match, MatchRating, MatchPlayer)
        .join(MatchPlayer, MatchPlayer.match_id == Match.id)
        .join(MatchRating, MatchRating.match_player_id == MatchPlayer.id)
        .where(MatchPlayer.player_id == player_id, Match.status == "confirmed")
        .order_by(Match.played_at)
    ).all()

    trend = [
        {"played_at": m.played_at, "display_score": r.display_score, "tacr": r.tacr,
         "match_id": m.id, "agent": mp.agent}
        for m, r, mp in history
    ]

    # 요원별 평균 + 승/무/패 (스코어 미입력 경기는 무승부로 집계 안 함)
    agent_agg: dict[str, dict] = {}
    for m, r, mp in history:
        a = agent_agg.setdefault(mp.agent, {"scores": [], "win": 0, "loss": 0, "draw": 0})
        a["scores"].append(r.display_score)
        res = _match_result(m, mp.team)
        if res is not None:
            a[res] += 1
    agent_stats = [
        {"agent": a, "games": len(v["scores"]), "avg_score": sum(v["scores"]) / len(v["scores"]),
         "win": v["win"], "loss": v["loss"], "draw": v["draw"]}
        for a, v in sorted(agent_agg.items(), key=lambda kv: -len(kv[1]["scores"]))
    ]

    # 맵별 평균 (레이더용)
    map_agg: dict[str, list[float]] = {}
    for m, r, _mp in history:
        map_agg.setdefault(m.map_name or "미지정", []).append(r.display_score)
    map_stats = [
        {"map": k, "games": len(v), "avg_score": sum(v) / len(v)}
        for k, v in sorted(map_agg.items(), key=lambda kv: -len(kv[1]))
    ]

    skill = session.get(SkillRating, player_id)

    # 라이벌/천적 (유저간 킬 구도). 상대 표시명을 붙여 넘긴다.
    from app.henrik.head_to_head import relationships
    rel = relationships(session, player_id)

    def _decorate(items):
        out = []
        for e in items:
            opp = session.get(Player, e.opponent_id)
            if opp is None:
                continue
            out.append({
                "label": opp.label, "opponent_id": e.opponent_id,
                "my_kills": e.my_kills, "their_kills": e.their_kills,
                "encounters": e.encounters, "tier_diff": e.tier_diff,
            })
        return out

    rivals = _decorate(rel["rivals"])
    challengers = _decorate(rel["challengers"])
    nemeses = _decorate(rel["nemeses"])
    prey = _decorate(rel["prey"])

    return templates.TemplateResponse(
        request,
        "player.html",
        {
            "player": player, "trend": trend,
            "agent_stats": agent_stats, "map_stats": map_stats, "skill": skill,
            "can_see_scores": can_see_scores,
            "rivals": rivals, "challengers": challengers,
            "nemeses": nemeses, "prey": prey,
        },
    )


# --- 리더보드 -----------------------------------------------------------

SERVER_OWNER = "Vice"  # 서버장 디코닉 — 리더보드 최상단 고정.


def _age_key(discord: str | None) -> int:
    """디코닉 앞의 두 자리(출생연도)로 나이 정렬 키를 만든다. 오래된 해가 먼저(작은 값).
    30 이상은 19XX, 미만은 20XX 로 해석(9X→1990년대, 0X→2000년대). 숫자 없으면 맨 뒤."""
    if not discord:
        return 9999
    digits = ""
    for ch in discord.strip():
        if ch.isdigit():
            digits += ch
        else:
            break
    if len(digits) < 2:
        return 9999
    yy = int(digits[:2])
    return 1900 + yy if yy >= 30 else 2000 + yy


def _leaderboard_rows(session: Session, min_games: int, by_age: bool = False) -> list[dict]:
    stmt = (
        select(
            Player.id, Player.display_name, Player.discord_name, Player.departed,
            func.count(MatchRating.id).label("n"),
            func.avg(MatchRating.tacr).label("mean_tacr"),
            func.avg(MatchRating.display_score).label("mean_score"),
        )
        .join(MatchPlayer, MatchPlayer.player_id == Player.id)
        .join(MatchRating, MatchRating.match_player_id == MatchPlayer.id)
        .join(Match, Match.id == MatchPlayer.match_id)
        .where(Match.status == "confirmed")
        .group_by(Player.id)
        .having(func.count(MatchRating.id) >= min_games)
    )
    rows = []
    for pid, name, discord, departed, n, mean_tacr, mean_score in session.execute(stmt).all():
        skill = session.get(SkillRating, pid)
        manual = session.scalar(
            select(PlayerTier).where(PlayerTier.player_id == pid, PlayerTier.source == "manual")
            .order_by(PlayerTier.recorded_at.desc())
        )
        tier_value = manual.tier_value if manual else None
        label = "[나간 유저]" if departed else (f"{discord} ({name})" if discord else name)
        rows.append({
            "player_id": pid, "display_name": name, "discord_name": discord, "label": label, "games": n,
            "mean_tacr": mean_tacr, "mean_score": mean_score,
            "adj_tacr": eb_adjusted_tacr(n, mean_tacr),
            "adj_score": eb_adjusted_score(n, mean_tacr),
            "mu": skill.mu if skill else None,
            "sigma": skill.sigma if skill else None,
            "tier_value": tier_value,
            "tier_name": config.tier_name(tier_value) if tier_value is not None else None,
        })
    if by_age:
        rows.sort(key=lambda r: (_age_key(r["discord_name"]), r["display_name"]))
        # 서버장(Vice)은 나이순일 때만 맨 위 고정. 조정점수순에선 실제 순위대로.
        rows.sort(key=lambda r: 0 if (r["discord_name"] or "").strip() == SERVER_OWNER else 1)
    else:
        rows.sort(key=lambda r: r["adj_score"], reverse=True)
    return rows


def _score_distribution(rows: list[dict], my_player_id: int | None, nbins: int = 12) -> dict | None:
    """조정점수 분포(구간별 빈도)와 본인 위치(0~1 비율)를 계산한다.
    개별 수치는 클라이언트로 보내지 않고 구간 빈도와 내 위치 비율만 넘긴다."""
    scores = [r["adj_score"] for r in rows]
    if len(scores) < 3:
        return None
    lo, hi = min(scores), max(scores)
    span = (hi - lo) or 1.0
    counts = [0] * nbins
    for s in scores:
        counts[min(int((s - lo) / span * nbins), nbins - 1)] += 1
    my_frac = None
    if my_player_id is not None:
        for r in rows:
            if r["player_id"] == my_player_id:
                my_frac = max(0.0, min(1.0, (r["adj_score"] - lo) / span))
                break
    return {"counts": counts, "my_frac": my_frac}


@router.get("/leaderboard", response_class=HTMLResponse)
def leaderboard(
    request: Request, min_games: int = 1, tier: str = "", sort: str = "age",
    session: Session = Depends(get_session), user: AuthUser = Depends(require_user),
):
    # 기본은 모두 나이순. 어드민만 조정점수순으로 전환 가능(일반 유저는 점수가 가려져 무의미).
    by_score = user.is_admin and sort == "score"
    rows = _leaderboard_rows(session, min_games, by_age=not by_score)
    # 특정 티어 선택 시 그 티어만(수동 티어 없는 유저는 자동 제외).
    if tier:
        rows = [r for r in rows if r["tier_name"] == tier]
    return templates.TemplateResponse(
        request, "leaderboard.html",
        {"rows": rows, "min_games": min_games, "tier": tier,
         "sort": "score" if by_score else "age",
         "tier_names": list(config.TIER_TABLE.keys())},
    )


# --- 선수 관리 ----------------------------------------------------------

@router.get("/players", response_class=HTMLResponse)
def players_admin(
    request: Request, session: Session = Depends(get_session),
    user: AuthUser = Depends(require_user),
):
    players = session.scalars(select(Player).order_by(Player.display_name)).all()
    # 일반 유저는 자기 자신만 조회 가능.
    if not user.is_admin:
        players = [p for p in players if p.id == user.player_id]
    data = []
    departed = []
    for p in players:
        aliases = session.scalars(
            select(PlayerAlias.alias).where(PlayerAlias.player_id == p.id)
        ).all()
        latest_tier = session.scalar(
            select(PlayerTier).where(PlayerTier.player_id == p.id, PlayerTier.source == "manual")
            .order_by(PlayerTier.recorded_at.desc())
        )
        tier_name = config.tier_name(latest_tier.tier_value) if latest_tier else None
        entry = {
            "player": p, "aliases": aliases, "tier": latest_tier,
            "tier_name": tier_name, "riot_accounts": p.riot_accounts,
        }
        (departed if p.departed else data).append(entry)

    # 어드민에게만: 계정 목록(비번 초기화)·미발급 참가자 수(계정 발급 버튼).
    accounts = []
    unprovisioned = 0
    if user.is_admin:
        for u in session.scalars(select(User).order_by(User.username)).all():
            p = session.get(Player, u.player_id)
            accounts.append({
                "user": u, "label": p.label if p else u.username,
                "is_admin": u.username in config.admin_usernames(),
            })
        provisioned_pids = set(session.scalars(select(User.player_id)).all())
        unprovisioned = sum(
            1 for p in session.scalars(select(Player)).all()
            if p.discord_name and not p.departed and p.id not in provisioned_pids
        )
    return templates.TemplateResponse(
        request, "players.html",
        {
            "players": data, "departed": departed, "tier_table": config.TIER_TABLE,
            "accounts": accounts, "unprovisioned": unprovisioned,
        },
    )


@router.post("/players/{player_id}/edit")
def edit_player(
    player_id: int, display_name: str = Form(...), discord_name: str = Form(""),
    session: Session = Depends(get_session), _: AuthUser = Depends(require_admin),
):
    player = session.get(Player, player_id)
    if player is None:
        return HTMLResponse("유저를 찾을 수 없음", status_code=404)
    name = display_name.strip()
    if name:
        player.display_name = name
    player.discord_name = discord_name.strip() or None
    session.commit()
    return RedirectResponse(url="/players", status_code=303)


@router.post("/players/{player_id}/tier")
def set_manual_tier(
    player_id: int, tier_value: float = Form(...),
    session: Session = Depends(get_session), _: AuthUser = Depends(require_admin),
):
    session.add(PlayerTier(
        player_id=player_id, source="manual", tier_value=tier_value,
        ranked_games_in_act=None, recorded_at=_now(),
    ))
    session.commit()
    return RedirectResponse(url="/players", status_code=303)


@router.post("/players/{player_id}/alias")
def add_alias(
    player_id: int, alias: str = Form(...), session: Session = Depends(get_session),
    _: AuthUser = Depends(require_admin),
):
    register_alias(session, player_id, alias)
    session.commit()
    return RedirectResponse(url="/players", status_code=303)


@router.post("/players/{player_id}/riot")
def add_riot_account(
    player_id: int, riot_name: str = Form(...), riot_tag: str = Form(...),
    session: Session = Depends(get_session), _: AuthUser = Depends(require_admin),
):
    """Riot 계정(name#tag) 등록. tag 앞의 '#'은 있어도 없어도 됨. puuid는 추후 보강."""
    if session.get(Player, player_id) is None:
        return HTMLResponse("유저를 찾을 수 없음", status_code=404)
    name = riot_name.strip()
    tag = riot_tag.strip().lstrip("#")
    if not name or not tag:
        return HTMLResponse("name#tag 를 입력하세요.", status_code=400)
    exists = session.scalar(
        select(PlayerRiotAccount).where(
            PlayerRiotAccount.player_id == player_id,
            PlayerRiotAccount.riot_name == name,
            PlayerRiotAccount.riot_tag == tag,
        )
    )
    if exists is None:
        session.add(PlayerRiotAccount(player_id=player_id, riot_name=name, riot_tag=tag))
        session.commit()
    return RedirectResponse(url="/players", status_code=303)


@router.post("/players/{player_id}/riot/delete")
def delete_riot_account(
    player_id: int, riot_name: str = Form(...), riot_tag: str = Form(...),
    session: Session = Depends(get_session), _: AuthUser = Depends(require_admin),
):
    """등록된 Riot 계정 삭제. (player_id, name, tag) 로 특정."""
    acct = session.scalar(
        select(PlayerRiotAccount).where(
            PlayerRiotAccount.player_id == player_id,
            PlayerRiotAccount.riot_name == riot_name.strip(),
            PlayerRiotAccount.riot_tag == riot_tag.strip().lstrip("#"),
        )
    )
    if acct is not None:
        session.delete(acct)
        session.commit()
    return RedirectResponse(url="/players", status_code=303)


@router.post("/players/{player_id}/merge")
def merge_player(
    player_id: int, target_id: int = Form(...),
    session: Session = Depends(get_session), _: AuthUser = Depends(require_admin),
):
    """중복 유저(player_id)를 target_id 로 병합. 기록 이전 후 전 경기 재계산."""
    try:
        merge_players(session, source_id=player_id, target_id=target_id)
    except ValueError as e:
        return HTMLResponse(str(e), status_code=400)
    session.commit()
    from app.calibration.recompute import recompute_all
    recompute_all()
    return RedirectResponse(url="/players", status_code=303)


@router.post("/players/{player_id}/delete")
def delete_player(
    player_id: int, session: Session = Depends(get_session),
    _: AuthUser = Depends(require_admin),
):
    """나간 유저 완전 삭제(하드). 실수 방지를 위해 departed 상태만 허용."""
    player = session.get(Player, player_id)
    if player is None:
        return HTMLResponse("유저를 찾을 수 없음", status_code=404)
    if not player.departed:
        return HTMLResponse("나간 유저만 완전 삭제할 수 있습니다.", status_code=400)
    delete_player_permanently(session, player_id)
    session.commit()
    from app.calibration.recompute import recompute_all
    recompute_all()
    return RedirectResponse(url="/players", status_code=303)


@router.post("/players/{player_id}/depart")
def toggle_departed(
    player_id: int, session: Session = Depends(get_session),
    _: AuthUser = Depends(require_admin),
):
    """서버 이탈 소프트 처리 토글 — 기록·랭크·점수는 유지, 이름만 '[나간 유저]'로 표시."""
    player = session.get(Player, player_id)
    if player is None:
        return HTMLResponse("유저를 찾을 수 없음", status_code=404)
    player.departed = not player.departed
    session.commit()
    return RedirectResponse(url="/players", status_code=303)


@router.post("/players/reset-season")
def reset_season(
    session: Session = Depends(get_session), _: AuthUser = Depends(require_admin),
):
    """시즌 초기화 — 경기/레이팅/OpenSkill/파생 티어 삭제, 선수·별칭·수동티어 보존.
    이탈(departed) 유저는 이 시점에 완전히 제거한다."""
    session.execute(delete(MatchRating))
    session.execute(delete(MatchPlayer))
    session.execute(delete(Match))
    session.execute(delete(SkillRating))
    session.execute(delete(PlayerTier).where(PlayerTier.source != "manual"))
    departed_ids = select(Player.id).where(Player.departed.is_(True))
    session.execute(delete(PlayerTier).where(PlayerTier.player_id.in_(departed_ids)))
    session.execute(delete(PlayerAlias).where(PlayerAlias.player_id.in_(departed_ids)))
    session.execute(delete(SkillRating).where(SkillRating.player_id.in_(departed_ids)))
    session.execute(delete(Player).where(Player.departed.is_(True)))
    session.commit()
    return RedirectResponse(url="/players", status_code=303)


# --- 계정 관리 (어드민) -------------------------------------------------

@router.post("/accounts/provision")
def provision_accounts(
    session: Session = Depends(get_session), _: AuthUser = Depends(require_admin),
):
    """디코닉 보유 미이탈 참가자 중 계정 없는 사람에게 기본 비번으로 계정 발급."""
    ensure_accounts(session)
    return RedirectResponse(url="/players", status_code=303)


@router.post("/users/{user_id}/reset-password")
def reset_user_password(
    user_id: int, session: Session = Depends(get_session),
    _: AuthUser = Depends(require_admin),
):
    """어드민이 계정 비번을 기본값으로 초기화(다음 로그인 시 강제 변경)."""
    reset_password(session, user_id)
    return RedirectResponse(url="/players", status_code=303)


# --- JSON API (스펙 §6: 같은 데이터 재사용) -----------------------------

@router.get("/api/matches")
def api_matches(
    session: Session = Depends(get_session), _: AuthUser = Depends(require_admin),
):
    matches = session.scalars(
        select(Match).where(Match.status == "confirmed").order_by(Match.played_at.desc())
    ).all()
    return JSONResponse([
        {"id": m.id, "played_at": m.played_at, "map_name": m.map_name,
         "team_a_rounds": m.team_a_rounds, "team_b_rounds": m.team_b_rounds}
        for m in matches
    ])


@router.get("/api/match/{match_id}")
def api_match(
    match_id: int, session: Session = Depends(get_session),
    _: AuthUser = Depends(require_admin),
):
    data = _match_detail(session, match_id)
    if data is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    m = data["match"]
    return JSONResponse({
        "id": m.id, "played_at": m.played_at, "map_name": m.map_name,
        "team_a_rounds": m.team_a_rounds, "team_b_rounds": m.team_b_rounds,
        "players": [
            {
                "player_id": it["mp"].player_id, "name": it["player"].label,
                "team": it["mp"].team, "agent": it["mp"].agent, "role": it["mp"].role,
                "acs": it["mp"].acs, "kills": it["mp"].kills, "deaths": it["mp"].deaths,
                "assists": it["mp"].assists, "expected_acs": it["expected_acs"],
                "tacr": it["tacr"], "display_score": it["display_score"],
            }
            for it in data["items"]
        ],
    })


@router.get("/api/leaderboard")
def api_leaderboard(
    min_games: int = 1, session: Session = Depends(get_session),
    _: AuthUser = Depends(require_admin),
):
    return JSONResponse(_leaderboard_rows(session, min_games))
