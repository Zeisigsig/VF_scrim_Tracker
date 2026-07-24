"""로컬 업로드 GUI.

브라우저에서 스크린샷을 드래그앤드롭하면 **제자리 로컬 OCR** 후 추출 JSON 만
클라우드 웹의 /api/ingest 로 전송한다(이미지 원본은 로컬 밖으로 안 나감).
CLI(push.py)와 동일한 A안 흐름을 그대로 재사용하며, 브라우저 UI 만 얹은 것.

실행:
    uv run python -m app.ingest.webui
    → 자동으로 http://127.0.0.1:8765 를 연다.

환경(.env): CLOUD_BASE_URL, INGEST_API_KEY, ENABLE_LOCAL_UPLOAD=1 (push.py 와 동일).
"""
from __future__ import annotations

import tempfile
import webbrowser
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from app import config
from app.ingest.push import push_extraction

HOST = "127.0.0.1"
PORT = 8765

app = FastAPI(title="VF 업로드")

PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VF 스크린샷 업로드</title>
<style>
  body{font-family:system-ui,'Segoe UI',sans-serif;background:#0f1116;color:#e6e6e6;
       margin:0;padding:2rem;display:flex;flex-direction:column;align-items:center}
  h1{font-size:1.2rem;font-weight:600;margin:.2rem 0 1rem}
  #drop{width:min(520px,90vw);border:2px dashed #3a4152;border-radius:14px;
        padding:2.4rem 1rem;text-align:center;background:#171a22;cursor:pointer;
        transition:.15s;color:#aab}
  #drop.hot{border-color:#5b8cff;background:#1b2233;color:#dbe6ff}
  #drop b{color:#e6e6e6}
  input[type=file]{display:none}
  .btn{display:inline-block;margin-top:.8rem;padding:.45rem .9rem;border-radius:8px;
       background:#2a3346;color:#dbe6ff;font-size:.9rem}
  ul{list-style:none;padding:0;width:min(520px,90vw);margin:1.2rem 0 0}
  li{display:flex;align-items:center;gap:.6rem;padding:.5rem .7rem;border-radius:8px;
     background:#161922;margin-bottom:.4rem;font-size:.9rem}
  .name{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .st{font-variant-emoji:text}
  .ok{color:#63d18b} .skip{color:#d9b64e} .err{color:#f2726f} .pend{color:#8a93a6}
  a{color:#7aa2ff}
</style></head>
<body>
  <h1>VF 스크린샷 업로드</h1>
  <label id="drop" for="file">
    <div>여기로 <b>드래그</b> 하거나 클릭해서 선택</div>
    <span class="btn">파일 선택</span>
    <input id="file" type="file" accept="image/*" multiple>
  </label>
  <ul id="list"></ul>
<script>
const drop=document.getElementById('drop');
const input=document.getElementById('file');
const list=document.getElementById('list');
let queue=Promise.resolve();

function row(name){
  const li=document.createElement('li');
  li.innerHTML=`<span class="st pend">⏳</span><span class="name"></span><span class="msg"></span>`;
  li.querySelector('.name').textContent=name;
  list.prepend(li);
  return li;
}
function done(li,cls,icon,html){
  const st=li.querySelector('.st');st.className='st '+cls;st.textContent=icon;
  li.querySelector('.msg').innerHTML=html;
}
async function send(file){
  const li=row(file.name);
  const fd=new FormData();fd.append('file',file,file.name);
  try{
    const r=await fetch('/upload',{method:'POST',body:fd});
    const d=await r.json();
    if(d.status==='ok'){done(li,'ok','✅',`<a href="${d.review_url}" target="_blank">검토</a>`);}
    else if(d.status==='skipped'){done(li,'skip','↩︎','이미 적재됨');}
    else{done(li,'err','⚠️',d.message||'실패');}
  }catch(e){done(li,'err','⚠️',String(e));}
}
function enqueue(files){
  for(const f of files){ queue=queue.then(()=>send(f)); }
}
input.addEventListener('change',e=>{enqueue(e.target.files);input.value='';});
;['dragenter','dragover'].forEach(ev=>drop.addEventListener(ev,e=>{
  e.preventDefault();drop.classList.add('hot');}));
;['dragleave','drop'].forEach(ev=>drop.addEventListener(ev,e=>{
  e.preventDefault();drop.classList.remove('hot');}));
drop.addEventListener('drop',e=>{if(e.dataTransfer?.files?.length)enqueue(e.dataTransfer.files);});
</script>
</body></html>"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return PAGE


@app.post("/upload")
async def upload(file: UploadFile) -> JSONResponse:
    if not config.CLOUD_BASE_URL or not config.INGEST_API_KEY:
        return JSONResponse(
            {"status": "error", "message": "CLOUD_BASE_URL / INGEST_API_KEY 미설정 (.env 확인)"},
            status_code=500,
        )
    name = file.filename or "upload.png"
    if Path(name).suffix.lower() not in config.IMAGE_EXTENSIONS:
        return JSONResponse({"status": "error", "message": "이미지 파일이 아님"}, status_code=400)

    data = await file.read()

    def _work() -> dict:
        # 파일명 규칙(맵/시간)을 살리려 원본 파일명 그대로 임시 저장 후 OCR.
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / name
            p.write_bytes(data)
            with httpx.Client(base_url=config.CLOUD_BASE_URL, timeout=60.0) as client:
                return push_extraction(client, p)

    try:
        result = await run_in_threadpool(_work)
    except Exception as e:  # OCR/전송 실패 — 한 장 실패해도 페이지는 계속
        return JSONResponse({"status": "error", "message": str(e)}, status_code=502)

    if "skipped" in result:
        return JSONResponse({"status": "skipped"})
    return JSONResponse(
        {"status": "ok", "review_url": config.CLOUD_BASE_URL + result.get("review_url", "")}
    )


def main() -> None:
    if not config.CLOUD_BASE_URL or not config.INGEST_API_KEY:
        print("경고: CLOUD_BASE_URL / INGEST_API_KEY 가 .env 에 없어 업로드가 실패합니다.")
    url = f"http://{HOST}:{PORT}"
    print(f"업로드 GUI 실행: {url}  (Ctrl+C 종료)")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
