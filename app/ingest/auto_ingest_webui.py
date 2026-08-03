"""로컬 자동적재 GUI (디코닉 → 그날 커스텀 내전 자동 적재).

브라우저 텍스트영역에 디코닉을 쉼표로 붙여넣고 버튼을 누르면, 그 목록을
클라우드 웹의 /api/auto-ingest 로 보내 서버에서 실행하고(Henrik 검색·확정 저장은
VM 정본에서), 결과 요약을 화면에 표시한다. 스크린샷 webui 와 같은 A안 구조로,
인증 시크릿(INGEST_API_KEY)은 로컬 서버에만 있고 브라우저로 노출되지 않는다.

실행:
    uv run python -m app.ingest.auto_ingest_webui
    → 자동으로 http://127.0.0.1:8766 를 연다.

환경(.env): CLOUD_BASE_URL, INGEST_API_KEY (push.py 와 동일).
"""
from __future__ import annotations

import webbrowser

import httpx
import uvicorn
from fastapi import Body, FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from app import config

HOST = "127.0.0.1"
PORT = 8766

app = FastAPI(title="VF 자동적재")

PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VF 자동적재</title>
<style>
  body{font-family:system-ui,'Segoe UI',sans-serif;background:#0f1116;color:#e6e6e6;
       margin:0;padding:2rem;display:flex;flex-direction:column;align-items:center}
  h1{font-size:1.2rem;font-weight:600;margin:.2rem 0 .3rem}
  p.hint{color:#8a93a6;font-size:.85rem;margin:0 0 1rem}
  textarea{width:min(560px,90vw);min-height:120px;border:1px solid #3a4152;border-radius:12px;
           padding:.8rem 1rem;background:#171a22;color:#e6e6e6;font-size:.95rem;resize:vertical}
  .btn{margin-top:.8rem;padding:.55rem 1.4rem;border:0;border-radius:9px;cursor:pointer;
       background:#3355dd;color:#fff;font-size:.95rem}
  .btn:disabled{opacity:.5;cursor:default}
  #out{width:min(560px,90vw);margin-top:1.2rem}
  .card{background:#161922;border-radius:10px;padding:.7rem .9rem;margin-bottom:.5rem;font-size:.9rem}
  .card h3{margin:0 0 .4rem;font-size:.95rem}
  .row{display:flex;gap:.5rem;padding:.15rem 0}
  .ok{color:#63d18b} .skip{color:#d9b64e} .warn{color:#f2726f} .muted{color:#8a93a6}
  .pend{border:1px solid #4a3d2a}
  .pend label{display:flex;gap:.5rem;align-items:center;cursor:pointer;font-size:.92rem}
  .pl{display:flex;flex-wrap:wrap;gap:.3rem;margin:.4rem 0 .1rem 1.6rem}
  .chip{font-size:.78rem;padding:.08rem .45rem;border-radius:6px;background:#222634}
  .chip.stranger{background:#3a2626;color:#f2a7a4}
  #confirm{margin-top:.6rem}
</style></head>
<body>
  <h1>VF 자동적재</h1>
  <p class="hint">내전 끝난 직후, 참가자 디코닉을 <b>쉼표(,)로 구분</b>해서 붙여넣고 실행하세요.</p>
  <textarea id="nicks" placeholder="예) 97 승진, 98 타나, 03 민호"></textarea>
  <button id="go" class="btn">적재 실행</button>
  <div id="out"></div>
<script>
const ta=document.getElementById('nicks');
const go=document.getElementById('go');
const out=document.getElementById('out');

function card(title, items, cls){
  if(!items || !items.length) return '';
  const rows=items.map(t=>`<div class="row ${cls||''}">${t}</div>`).join('');
  return `<div class="card"><h3>${title}</h3>${rows}</div>`;
}
function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function matchLine(m){return `${m.saved?'✅ 저장':'↩︎ 기존'} · ${m.map||'?'} ${m.played_at} `
    +`<span class="muted">(${m.match_id.slice(0,8)})</span>`;}
function pendingCard(m){
  const chips=m.roster.map(x=>`<span class="chip ${x.typed?'':'stranger'}">${esc(x.label)}</span>`).join('');
  const typed=m.roster.filter(x=>x.typed).length;
  return `<div class="card pend">`
    +`<label><input type="checkbox" class="pchk" value="${m.match_id}">`
    +`<b>${m.map||'?'}</b> ${m.played_at} · <span class="skip">낯선 ${m.strangers}명</span>`
    +` <span class="muted">(입력 ${typed}명)</span></label>`
    +`<div class="pl">${chips}</div></div>`;
}
function render(d){
  const saved=d.matches.filter(m=>m.saved).length;
  const ml=d.matches.map(matchLine);
  let html=card(`자동 적재 ${d.matches.length}개 (신규 ${saved}, 기존 ${d.matches.length-saved})`, ml);
  html+=card('신규 유저', d.new_players, 'ok');
  html+=card('Riot 계정 채움', d.new_accounts, 'ok');
  const pend=d.pending||[];
  if(pend.length){
    html+=`<div class="card"><h3>❓ 확인 필요 — 낯선(입력 안 됨) 사람이 낀 매치. `
      +`낯선 중엔 디코닉만 안 붙은 기존 유저도 있습니다. 적재할 것만 체크하세요 `
      +`(적재 시 로스터 전원 발로닉으로 자동 연결/생성)</h3>`
      +pend.map(pendingCard).join('')
      +`<button id="confirm" class="btn">선택 적재</button></div>`;
  }
  html+=card('디코닉 미매칭 — 적재는 가능 (아래 매치를 적재하면 발로닉으로 자동 연결/생성됩니다)', d.unmatched, 'skip');
  html+=card('⚠️ 여러 명 매칭(정확히 입력)', d.ambiguous, 'warn');
  html+=card('⚠️ Riot 계정 미연결(자동검색 불가)', d.no_account, 'warn');
  html+=card('제외됨(비표준 매치)', d.filtered, 'skip');
  html+=card('최근 커스텀 없음', d.no_matches, 'skip');
  out.innerHTML=html || '<div class="card muted">처리 결과 없음</div>';
  const cb=document.getElementById('confirm');
  if(cb) cb.addEventListener('click',confirmSel);
}
async function confirmSel(){
  const ids=[...document.querySelectorAll('.pchk:checked')].map(c=>c.value);
  if(!ids.length){return;}
  const btn=document.getElementById('confirm');
  btn.disabled=true; btn.textContent='⏳ 적재 중…';
  try{
    const r=await fetch('/confirm',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({match_ids:ids})});
    const d=await r.json();
    if(d.error){btn.textContent='오류: '+d.error; btn.disabled=false; return;}
    const saved=d.matches.filter(m=>m.saved).length;
    let h=card(`확정 적재 ${d.matches.length}개 (신규 ${saved})`, d.matches.map(matchLine));
    h+=card('신규 유저', d.new_players, 'ok');
    h+=card('Riot 계정 채움', d.new_accounts, 'ok');
    out.insertAdjacentHTML('afterbegin', `<div id="cout">${h}</div>`);
    btn.textContent='적재 완료';
  }catch(e){btn.textContent='오류: '+String(e); btn.disabled=false;}
}
async function run(){
  const csv=ta.value.trim();
  if(!csv){ta.focus();return;}
  go.disabled=true; out.innerHTML='<div class="card muted">⏳ 실행 중… (Henrik 검색으로 수십 초 걸릴 수 있어요)</div>';
  try{
    const r=await fetch('/run',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({nicks_csv:csv})});
    const d=await r.json();
    if(d.error){out.innerHTML=`<div class="card warn">오류: ${d.error}</div>`;}
    else render(d);
  }catch(e){out.innerHTML=`<div class="card warn">오류: ${String(e)}</div>`;}
  go.disabled=false;
}
go.addEventListener('click',run);
</script>
</body></html>"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return PAGE


@app.post("/run")
async def run(payload: dict = Body(...)) -> JSONResponse:
    if not config.CLOUD_BASE_URL or not config.INGEST_API_KEY:
        return JSONResponse({"error": "CLOUD_BASE_URL / INGEST_API_KEY 미설정 (.env 확인)"},
                            status_code=500)
    nicks = [n.strip() for n in (payload.get("nicks_csv") or "").split(",") if n.strip()]
    if not nicks:
        return JSONResponse({"error": "디코닉을 입력하세요"}, status_code=400)
    return await _proxy("/api/auto-ingest", {"nicks": nicks})


@app.post("/confirm")
async def confirm(payload: dict = Body(...)) -> JSONResponse:
    if not config.CLOUD_BASE_URL or not config.INGEST_API_KEY:
        return JSONResponse({"error": "CLOUD_BASE_URL / INGEST_API_KEY 미설정 (.env 확인)"},
                            status_code=500)
    match_ids = [m for m in (payload.get("match_ids") or []) if m]
    if not match_ids:
        return JSONResponse({"error": "적재할 매치를 선택하세요"}, status_code=400)
    return await _proxy("/api/auto-ingest/confirm", {"match_ids": match_ids})


async def _proxy(path: str, body: dict) -> JSONResponse:
    """VM 엔드포인트로 프록시. 인증키(INGEST_API_KEY)는 로컬 서버에만·브라우저 미노출."""
    def _work() -> dict:
        with httpx.Client(base_url=config.CLOUD_BASE_URL, timeout=300.0) as client:
            resp = client.post(
                path, headers={"X-Ingest-Key": config.INGEST_API_KEY}, json=body,
            )
            resp.raise_for_status()
            return resp.json()

    try:
        return JSONResponse(await run_in_threadpool(_work))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


def main() -> None:
    if not config.CLOUD_BASE_URL or not config.INGEST_API_KEY:
        print("경고: CLOUD_BASE_URL / INGEST_API_KEY 가 .env 에 없어 자동적재가 실패합니다.")
    url = f"http://{HOST}:{PORT}"
    print(f"자동적재 GUI 실행: {url}  (Ctrl+C 종료)")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
