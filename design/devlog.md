# 개발 기록 (devlog)

발로란트 내전 퍼포먼스 트래커 구현 로그. 스펙: [valorant_scrim_tracker_spec.md](./valorant_scrim_tracker_spec.md)

---

## 2026-07-08 — 프로젝트 착수

### 환경 파악
- 개발 머신: Windows + PyCharm, `.venv`는 uv 관리 CPython 3.12 (Windows).
- 현재 작업 셸은 WSL(Linux). 이 셸에는 pip/uv/sqlite3 CLI가 없고 시스템 python3는 3.14.
- 결론: **실제 실행/의존성 설치는 사용자가 PyCharm(Windows) venv에서 수행.** 코드 작성은 여기서, 순수 계산 로직(rating, math 전용)은 WSL의 stdlib python3로 직접 검증 가능.

### 설계 결정
- 스펙 §2 디렉토리 구조를 그대로 따르되, 프로젝트 루트가 이미 `vf_performance/`이므로 최상위에 `app/`를 둔다 (스펙의 `scrim-tracker/`는 개념적 루트).
- 계산 로직은 순수 함수로 유지하고 DB 의존성과 분리 (스펙 §12).
- 모든 튜닝 파라미터는 `app/config.py` 한 곳, `PARAMS_VERSION`으로 버전 관리.
- 초기 파라미터 값은 가정값이며 Phase 3 재적합 대상임을 주석에 명시.

### 진행
- [x] `design/` 폴더 생성, 스펙 이동, devlog 작성 시작
- [x] 프로젝트 스캐폴딩 (app 패키지, requirements/pyproject, docker, alembic, .env.example, .gitignore)
- [x] Phase 1a: config + rating core (+ 테스트)
- [x] Phase 1a: DB 모델 + 마이그레이션
- [x] Phase 1b: 인제스트 파이프라인
- [x] Phase 1c: 웹 라우트 + 템플릿
- [x] Phase 2/3: Henrik + 캘리브레이션

### 구현 상세 노트
- **rating core (§4)**: `app/rating/` — tier.py(티어 수치화·유효티어 2-패스), tacr.py(기대ACS 팀분리·TACR 두 공식·표시점수·implied tier), openskill_engine.py, leaderboard.py. 전부 순수 함수, DB 무의존.
- **핵심 불변식 검증(WSL stdlib python3로 직접 실행)**: `sum(expected_acs)==sum(acs)` (팀분리 공식 총합 보존), `display_score(100)==500`, 캡 [0.3,2.0] 준수, Phase1/Phase2 공식 분기, EB 수축. 스펙 §10 픽스처로 Perik r_acs≈1.28, TACR≈120.8, 표시점수≈697 확인.
- **DB(§3)**: SQLAlchemy 2.x 모델 + 손수 작성한 alembic 0001_initial 마이그레이션(모델과 1:1). matches 에 `status`(pending/confirmed) 추가(검토 플로우용).
- **인제스트(§5)**: extractor(Claude vision, temp0, 코드펜스 금지, 파싱 실패 1회 재시도), schemas(Pydantic, team A/B 해석 필드 추가), validator(6개 sanity rule, 경고만·차단 안 함), matcher(alias 정확일치 자동 / rapidfuzz 유사 후보 top3 / 신규).
- **오케스트레이션**: `app/services.py` 추가(스펙 디렉토리엔 없음). 확정→MatchPlayer 저장→tier_eff 2-패스→compute_match→MatchRating/OpenSkill/implied 갱신을 한곳에 모음. DB결합 로직과 순수함수 분리 유지.
- **웹(§6)**: FastAPI+Jinja2 다크테마, 7개 페이지 전부 + Chart.js 선수 추이 + `/api/*` JSON. 검토 화면은 필수 단계(무검토 저장 없음).
- **Phase 2**: henrik/client.py — httpx, 429 지수백오프. 커스텀게임 필터/ v4 필드경로/ free tier 한도는 실응답 검증 TODO로 주석 명시.
- **Phase 3**: calibrate.py(K_TIER OLS, ROLE_COEF 역수정규화, 승률검증 리포트 — 자동적용 금지) + recompute.py(전경기 재계산 CLI, 원시값만 사용해 시간순 리플레이).

## 2026-07-08 — 스펙 개정 반영 (§5.0 스크린샷 수집 경로 추가)

새 스펙(§5.0)만 기존과 차이. 추가된 요구사항 3가지를 반영:
- **다중 파일 업로드**: `/upload` 가 `screenshots: list[UploadFile]` 수신. 1장이면 검토로 직행, 여러 장이면 각각 pending 생성 후 홈의 "검토 대기" 목록으로. (여러 장일 때 맵/스코어/날짜 공통 입력은 무시 — 검토에서 개별 입력)
- **인박스 폴더** `data/inbox/`: 홈에 "처리 대기 N건" 배지(파일 개수) + `POST /inbox/process` 로 일괄 전환. 처리된 파일은 screenshots 로 이동 후 인박스에서 제거.
- **원본 보관 경로**: `screenshots/{match_id}.png` 로 통일. pending 생성 시 match_id 확보(flush) 후 저장. (스펙 문구는 "처리 완료 시 이동"이지만, pending 생성 시점에 이동해도 최종 위치가 동일하므로 그 시점에 이동)
- 공유 헬퍼 `_create_pending_match()` 로 업로드/인박스 경로 통합. `home()` 에 pending 목록 + inbox_count 전달, index.html 에 배지/목록 추가, upload.html `multiple` 속성.
- 부수 수정: 확장자와 실제 포맷 불일치 대비해 extractor 의 media_type 을 **바이트 매직넘버로 판별**(png/jpeg/webp)하도록 변경. `data/inbox/` gitignore 추가, startup 에서 inbox 디렉토리 생성.
- 검증: 전체 `py_compile` 통과. (런타임은 여전히 venv 필요)

### 환경/검증 한계 (다음 세션 유의)
- 이 WSL 셸에는 pip/uv/서드파티 미설치 → FastAPI 기동·pytest·alembic 실행은 **PyCharm(Windows) venv에서** 해야 함.
- 여기서 검증한 것: 전체 `py_compile` 통과 + rating 순수math 불변식 직접 실행 통과.
- **아직 실행으로 검증 못 한 것**: 실제 스크린샷 업로드→추출→검토→확정 관통(§Phase1b DoD), FastAPI 라우트 런타임, alembic upgrade, openskill/rapidfuzz/pydantic 임포트 동작. venv에서 `pytest app/tests` 먼저 돌려볼 것.

## 2026-07-09 — 첫 런타임 기동 + Starlette TemplateResponse 버그 수정

### 환경 변화
- `.venv` 가 이제 **Linux venv(CPython 3.14.4)** 로 재구성되어 모든 서드파티 의존성이 설치됨 → WSL 셸에서 직접 실행 가능해짐 (위 "환경/검증 한계"의 pip/uv 미설치 전제는 더 이상 유효하지 않음). 서드파티 임포트 전수 확인(fastapi/sqlalchemy/alembic/pydantic/anthropic/openskill/rapidfuzz/httpx/jinja2) 통과.
- 실행 방식: `uv run uvicorn app.main:app --reload` (PyCharm·WSL 양쪽에서 동작 확인).

### 버그: 홈 진입 즉시 500 (Internal Server Error)
- 증상: 서버 기동은 정상, `GET /` 에서 500. 트레이스백 말단이 Jinja2 LRUCache 에서 `TypeError: cannot use 'tuple' as a dict key (unhashable type: 'dict')` 라는 엉뚱한 메시지.
- 근본 원인: 설치된 **starlette 1.3.1** 의 `TemplateResponse` 시그니처가 **`(request, name, context)`** 로 변경됨(구 `(name, context)` positional 지원 제거). 코드는 구 방식 `TemplateResponse("index.html", {"request": request, ...})` 으로 호출 → `"index.html"` 이 `request` 로, **context 딕셔너리가 `name`(템플릿명)** 으로 해석됨 → 그 dict 가 Jinja2 캐시 키 튜플에 들어가 unhashable 에러. (Python 3.14 의 개선된 에러 메시지 포맷 때문에 원인이 더 헷갈리게 보였음.)
- 수정: `app/web/routes.py` 의 `TemplateResponse` 호출 **7곳 전부**를 `TemplateResponse(request, "name.html", {...})` 새 시그니처로 변경. context 안의 중복 `"request": request` 키는 starlette 이 setdefault 로 채워주므로 제거.
- 검증(WSL 실기동): `/`, `/upload`, `/leaderboard`, `/players` 전부 **200**, 서버 로그 500/Traceback 0건, 홈 타이틀 정상 렌더.

### 여전히 미검증 (다음 세션)
- `pytest app/tests` 실행, `alembic upgrade head`, 그리고 실제 스크린샷 업로드→추출→검토→확정 관통(§Phase1b DoD).

## 2026-07-09 — 명칭 변경 · 유저 수정 기능 · 추출 방식 전환(Claude vision → 로컬 OCR)

### 명칭 변경
- 브랜드명 "발로란트 내전 트래커" → **"모여봐요 발로의 숲 내전 트래커"** (base.html brand, main.py FastAPI title).
- UI의 "선수" → **"유저"** 전면 교체 (내전 참가자는 프로 선수가 아니므로). base.html 네비, players/index/leaderboard/match/review 템플릿, routes.py 404 문구. (요원명 등 게임 용어는 유지.)

### 유저 수정 기능
- 유저 관리(`/players`)에 display_name 인라인 수정 폼 추가. `POST /players/{id}/edit` (routes.py) — 아이디 변경 즉시 반영. 티어 변경은 기존 수동 티어 폼으로 이미 가능.

### 추출 방식 전환: Claude vision → 로컬 RapidOCR
- **동기**: 스코어보드의 글자·숫자 몇 개 읽는 데 LLM vision API는 과투자(비용). 로컬 OCR로 충분.
- **의존성**: `uv add rapidocr-onnxruntime` (onnxruntime 기반, 시스템 바이너리·API 키 불필요). `anthropic` 패키지는 이제 미사용(후속 제거 가능).
- **`app/ingest/extractor.py` 재작성**: 인터페이스(`extract_scoreboard(path) -> ExtractionResult`)는 동일 유지, 내부만 OCR로 교체.
  - 알고리즘: K/D/A 문자열("26/7/5")을 정규식으로 잡아 **행 앵커**로 사용 → y좌표로 행, x좌표로 열(닉네임/ACS/효율) 매핑. 해상도가 제각각(1920×1080, 1779×790)이라 픽셀 하드코딩 대신 상대 위치 사용.
  - **팀 판별**: 행 배경 스트립의 평균 R vs G 비교(초록 G>R=A / 빨강 R>G=B). 흰 글자는 R·G 동등 기여라 색조만 가름.
  - **가짜 행 필터**: 상단 계정 진행바("0/4 0 0/3")가 KDA로 오인식돼 11행이 되는 문제 → "KDA 왼쪽에 ACS 정수가 없으면 실제 행 아님"으로 스킵.
- **OCR 품질(실측 8장)**: 숫자(ACS·K/D/A·효율)와 팀 판별은 매우 정확(8경기 전부 10행·5:5). **한글 닉네임은 기본 모델이 중/영이라 깨짐** → 검토에서 수동 입력(첫 입력 후 별칭+퍼지매칭으로 이후 자동). 요원은 아이콘이라 OCR 불가 → 검토에서 선택(agent_kr="").

### 백필: 기존 screenshots 일괄 적재
- `app/ingest/backfill.py` 신규. 파일명 `{YYYYMMDD}_{session}_{game}.png` 파싱 → played_at(날짜+세션시+판분으로 시간순 보장), **map 자동(판01=스플릿, 판02=프랙처)**, OCR 추출 → **pending** 경기 생성(스펙 "무검토 저장 없음" 준수, 확정/레이팅은 검토에서). screenshot_path는 원본 파일명 유지, 중복 적재 스킵(idempotent).
- 실행: `uv run python -m app.ingest.backfill`. 20260709 8경기(4세션×2판) 적재 완료. 홈 검토 대기 노출·`/review/{id}` 렌더·재실행 8건 스킵 확인.

### 문서 드리프트 (미반영)
- README.md, valorant_scrim_tracker_spec.md §5.2 는 아직 "VLM(Claude API) 추출"로 서술 → 실제는 로컬 OCR. 다음에 정리 필요.

### 다음 할 일
- 8경기 각각 `/review/{id}` 에서 닉네임→유저 매칭·요원 선택·스코어 입력 후 확정 → 레이팅 산출.
- (선택) 미사용 `anthropic` 의존성 제거, README/스펙 문구 정리.

## 2026-07-09 (2) — 한글 OCR(2-패스) · 요원 자동입력 · 팀태그 제거 · 믹스 추가

검토 화면 피드백 대응: (1) 신규 요원 "믹스" 누락, (2) 한글을 하나도 못 읽음, (3) 요원명이 이미지 안 텍스트인데(닉네임 바로 아래) 비워둠, (4) 프리미어 팀태그("주모|")가 닉네임에 섞임, (5) "매번 채워야 하나?"라는 근본 질문.

### 핵심 답: 매번 채우는 구조 아님 — 한글 OCR 하나가 병목이었음
- **닉네임 자동매칭**: matcher의 별칭(alias) 시스템이 자기교정형. 한 번 확정하면 `register_alias`로 저장돼 다음 경기부터 같은 OCR 텍스트로 자동 매칭. **단 OCR이 닉네임을 일관되게 읽어야** 성립 → 한글 OCR이 열쇠.
- **요원은 "기억"이 아니라 매 경기 이미지에서 직접 읽음**(플레이어가 판마다 요원을 바꾸므로 기억은 부적합). 요원명은 아이콘이 아니라 **닉네임 바로 아래 한글 텍스트**였음 — 이전 devlog의 "요원=아이콘" 서술은 오류.

### 2-패스 OCR (`app/ingest/extractor.py` 재작성)
- 기본 모델(ch_PP-OCRv4)은 숫자/`/`엔 정확하나 한글 불가. 한글 rec 모델은 한글엔 되나 KDA `/`를 `}`로 깨뜨림 → **역할 분담**.
  - 패스 A(기본): 숫자 열(ACS/KDA/효율)·행 앵커·팀 판별.
  - 패스 B(한글 `models/korean_rec.onnx`, 사전은 onnx 메타데이터 내장): 닉네임·요원 텍스트.
- **이름 열 경계**: ACS 열 좌측(`name_bound`) 왼쪽만 이름 열로 봄 → 중앙 KDA 깨진 텍스트 오염 차단.
- **닉네임/요원 2줄 분리**: KDA 앵커 cy 기준 offset(닉네임 ≈ -8, 요원 ≈ +12)으로 가름 → 한 줄 미검출돼도 오분류 안 함. 같은 줄 후보는 한글 포함 우선(한글=한글패스, 영문=기본패스)으로 선택.
- **요원 fuzzy 매칭**: OCR 요원명을 `valid_agents()`에 rapidfuzz WRatio 매칭(≥65만 채택). 폐이드→페이드, 체임터→체임버는 잡고 대스(60·오검출)는 버림.
- **팀태그 제거**: `주모|aziin` → `aziin` ('|' 뒤만 취함).
- **한글 rec 모델**: HuggingFace `SWHL/RapidOCR` PP-OCRv1 korean, 프로젝트 `models/korean_rec.onnx`(3.3MB). `RapidOCR(rec_model_path=...)`로 로드.

### 실측(8장): 숫자·팀 완벽, 닉네임 7-9/10, 요원 2-7/10
- OCR 정확도는 완벽하지 않지만 문제 안 됨 — 닉네임은 별칭으로 자기교정, 요원은 fuzzy+검토 보정. 검토는 "확인만" 수준으로 경량화.
- 검토 드롭다운은 `sorted(valid_agents())`(믹스 포함, 27개)에서 생성, OCR 요원값 자동 선택.

### config / DB
- `AGENT_ROLE`에 `"믹스": "controller"` 추가(사용자 확인, ROLE_COEF 0.92).
- 기존 8개 pending 경기(확정 전)의 `extraction_raw`를 새 추출로 **삭제 없이 in-place 갱신**.

## 2026-07-09 (3) — 검토 UX 4건: 닉네임 자동제안·요원→역할 자동·표 정렬·첫킬/설치/해체 추출

### 1. 닉네임 유사도 자동제안 (`config.NICKNAME_AUTOMATCH_MIN=80`)
- OCR 닉네임이 정확 일치 별칭은 없지만 기존 유저명과 유사도 ≥80이면 검토에서 그 유저를 **기본 선택**(예: OCR "따따그르릉" ↔ 기존 "딱따그르릉" = 80점). routes.py review 에서 `suggested_player_id` 계산, review.html 에서 `≈` 표시+selected. 사람이 바꿀 수 있음. (5자 1글자差 한글은 WRatio 정확히 80이라 임계값 80으로 설정.)

### 2. 요원 선택 시 역할 자동 (review.html JS)
- 역할은 요원에 종속(고정 규칙)이므로 요원 select 변경 시 역할 select가 자동으로 따라오게 JS 추가. `agent_role`(AGENT_ROLE) 를 `tojson` 으로 주입. 서버도 confirm 에서 role 비면 AGENT_ROLE 로 폴백(기존).

### 3. 검토 표 가로 오버플로 (style.css)
- 12개 열이 컨테이너(1100px)를 넘어 페이지를 뚫던 문제 → `.table-scroll{overflow-x:auto}` 래퍼로 가둠 + 밀집 표용 패딩/입력폭 축소(narrow 46px). (브라우저 육안 확인은 미실시.)

### 4. 첫킬/설치/해체 추출 (`extractor._right_stats`)
- **원인**: 이 열들은 econ 오른쪽의 작은 한 자리 숫자(대개 0~3)라 기본 해상도에선 det가 놓침.
- **방법**: econ 열 오른쪽 영역만 3배 확대 재-OCR → x-간격으로 열 클러스터링.
- **안전장치**: 열이 3개 모두 확실히(각 ≥4행 지지) 검출될 때만 [첫킬,설치,해체]로 배정. 그 미만이면 **전부 공란**(열 오배정 시 r_obj 오염 위험 — 부분/오배정 데이터는 안 하느니만 못함). 검출 안 된 칸도 None(검토 입력). persist 에서 None→0.
- **실측 8장**: 목표 활동 많은 02_01·02_02 는 30/30 완전 자동, 나머지 6장은 신호 부족(대부분 0)이라 공란. 값이 중요한 경기일수록 잘 잡히고, 안 잡히는 경기는 어차피 r_obj 중립이라 실용적으로 무난.

---

## 2026-07-10 — 디스코드 닉 표시 · 요원 로스터 보강

### 디스코드 서버 닉 표시
- `Player.discord_name` 컬럼 추가 (마이그레이션 `0002_player_discord_name`). 유저관리에서 발로닉+디코닉 함께 편집.
- 페이지 표기는 **"디코닉 (발로닉)" 병기** 방식(사용자 선택). `Player.label` 프로퍼티로 통일, 리더보드는 `_leaderboard_rows`가 `label` 제공.
- **매칭은 여전히 발로닉(`display_name`) 기준**, 디코닉은 표시 전용.

### 요원 로스터 (2026-07 기준 29명 완비)
- `config.AGENT_ROLE`에 누락 3종 추가: 테호(initiator), 웨이레이(duelist), 비토(sentinel). (비토는 최초 "베토"로 잘못 넣었다가 07-12 정정 — 아래 참조.)

---

## 2026-07-12 — 오타 정정 · 홈/경기상세 개편 · 파일명 규칙 · 시즌 초기화 · 이탈 처리

### 요원명 오타 정정
- `AGENT_ROLE`의 `"베토"` → **`"비토"`** (07-10 음역 추정 오류, 인게임 표기 확인).

### 유저관리 티어 드롭다운 고정
- 티어 설정 `<select>`가 항상 첫 옵션(아이언)으로 리셋되던 문제 → 이미 설정된 수동 티어(`it.tier.tier_value == rng[1]`)에 `selected` 부여. 미설정 유저는 아이언 기본 유지.

### 홈(index.html) 재배치 + 지표 설명
- 상단 grid2 = 좌 "지표 읽는 법"(표시점수/TACR/OpenSkill 일반인용 설명) · 우 리더보드 요약 → 최근 경기 → 검토 대기+인박스 알림(맨 밑) 순으로 변경.

### 경기 상세(match.html)
- "팀"(A/B) 컬럼 → **"랭크"**: 유저관리 수동 티어를 티어명으로 표시. `config.tier_name(value)` 신설(수치→티어명, 밴드 포함/근접). 미설정은 `-`.
- 스코어줄에 팀 색상 네모(A=초록/B=빨강, `.team-dot` CSS). 행 왼쪽 색 라벨과 매칭.

### 파일명 규칙 변경(backfill.py)
- `{YYYYMMDD}_{맵}_{세션}_{판}.png` 로 확장. 맵 생략형 `{YYYYMMDD}_{세션}_{판}.png` 도 허용 → **map_name=None**(OCR 폴백 제거). `_parse()`가 두 패턴 처리. (구 `_GAME_MAP` 01=스플릿/02=프랙처 추론 폐기.)
- 홈의 최근 경기·검토 대기에서 맵명 **인라인 편집** (`POST /match/{id}/map`, `next`로 리다이렉트).

### 시즌 초기화 · 서버 이탈 처리
- **시즌 초기화**(`POST /players/reset-season`, 유저관리 하단): 경기/MatchPlayer/MatchRating/SkillRating/비-manual PlayerTier 삭제 + **departed 유저 완전 제거**. 선수·별칭·수동 티어는 보존.
- **서버 이탈 소프트 처리**: 하드 삭제 폐기, `Player.departed` 불리언(마이그레이션 `0003_player_departed`). 나감 처리 시 기록·랭크·점수 유지하되 표시 이름만 **`[나간 유저]`**(모두 동일 표기 — 이탈 사유 유추 방지, 사용자 요청). `Player.label` 및 `_leaderboard_rows` 양쪽에 분기. 유저관리 버튼은 나감처리↔복구 토글(`POST /players/{id}/depart`).

### 중복 유저 정리 + 재발 방지
- `빵긋빵긋빵빠레`가 2행(player 7·11) 등록돼 있던 문제. 이름은 바이트 동일(정상), 원인은 (1) `get_or_create_player`가 이름 중복을 확인 않고 항상 새로 생성, (2) 자동 매칭이 별칭 완전 일치라 OCR 변동 시 빗나감. 빈 껍데기 player 11 삭제(player 7 유지).
- **`get_or_create_player` 수정**: 같은 별칭 또는 같은 발로닉이 이미 있으면 그 유저를 재사용 → 이름 중복 생성 방지.

### 닉네임 OCR 복구 — det-free rec 폴백
- 증상: 숫자는 완벽하나 일부 닉네임 완전 누락(예 `김타나`, `빵긋빵긋빵빠레`). 라벨 이미지(`20260709_스플릿_01_01_labeling`)로 진단.
- **원인은 rec(인식)가 아니라 det(검출)**: 두 패스가 공유하는 `ch_PP-OCRv4_det`이 글자 간격이 벌어진 흰 글자(`김 타 나`)나 어두운 마룬(B팀) 배경 위 흰 글자 박스를 못 만듦. 박스가 없으니 rec 모델을 바꿔도 무의미. 격리 크롭에 `use_det=False`로 rec만 강제하니 기존 한글 모델이 그대로 읽어냄을 확인.
- **해결(모델 교체 없이)**: KDA 앵커로 닉네임 셀 좌표를 이미 알므로, det가 닉네임을 못 만든 행만 셀을 잘라 2배 확대 후 rec-only 폴백(`_rec_name_line`). 크롭 좌측=닉네임 텍스트 열 좌경계(`name_left`, 선수 행 세로범위 내 박스의 min x0 — 좌상단 UI 텍스트 오염 배제), 우측=`name_left+0.62*(name_bound-name_left)`(해상도 무관 비율). 폴백은 **det 닉네임이 빈 행에서만** 작동 → 기존에 잘 잡히던 행은 불변.
- 결과: 라벨 이미지 10/10 닉네임 값 산출. `김타나` 정확 복구, `빵긋빵긋빵빠레`→`빵궁방x빵빠레`(근접, 별칭 1회 확정 후 자동매칭). 새 의존성/모델 다운로드 없음. pytest 17통과, 타 스크린샷 4종 빈 닉네임 0.

### 스코어 자동추출 · 업로드 파일명 파싱 · 경기 상세 수정 버튼
- **경기 상세 맵·스코어 수정**(`match.html` `<details>` + `POST /match/{id}/score` → `edit_match`): 확정 후 스코어를 잊고 넘긴 경우 여기서 수정. 스코어는 TACR 정규화·OpenSkill(누적)에 영향 → 저장 시 `recompute_all()`로 **전 경기 재계산**. map만 바꿔도 무해(레이팅 무관). confirm 시 재확인 다이얼로그.
- **업로드 파일명 파싱**(`upload_submit`): 기존엔 파일명의 맵/날짜를 무시하고 폼값(빈값)+`_now()`만 써서 `20260709_스플릿_01_01.png` 업로드해도 맵·날짜가 안 들어감. 이제 `backfill._parse`/`_played_at` 재사용해 파일명에서 map·played_at 채움(단일 업로드는 폼값 우선, 다중은 파일명 기준). 
- **스코어 OCR 자동추출**(`extractor._extract_score`): 상단 "N 승리 M" 배너를 색 분리(**초록=A / 빨강=B**, 행 팀 판별과 동일 규칙)해 각 숫자만 max-channel 이진화 후 `use_det=False` rec-only(base 모델)로 읽음. det는 스타일라이즈드 색 숫자를 못 잡음. `ExtractionResult`에 `team_a_rounds/team_b_rounds` 추가, `_create_pending_match`가 pending 경기에 미리 저장 → **검토 화면이 스코어를 미리 채워 "미기입" 방지**. 방어적: 확신 없으면 None(0~30 범위 검증, 승리/패배 박스 못 찾으면 스킵)→검토/수정에서 수동. 라벨 이미지 13:4 정확, 다수 이미지 A·B 산출(일부 빨강 숫자 저채도는 None 폴백).
- **첫킬/설치/해체(작은 숫자)**: 셀 좌표를 알아도 고립 소형 숫자(특히 0)·색 배경이라 rec가 잡음만 냄 → 이 OCR 스택으론 신뢰 불가 확인. 사용자 "안 잡히면 넘어가" 지시로 **수동 유지**(기존 보류 판단과 동일). pytest 17통과, 전 페이지 200, recompute 정상.

### 요원 통일 · 수정 버튼 가시화 · 유저 레이더 차트
- **KAY/O → 케이오 통일**: `config.AGENT_ROLE`에 `"KAY/O"`(영문)와 `"케이오"`가 중복 등록돼 검토 드롭다운 맨 위에 영문 KAY/O가 뜸(한글은 가나다 정렬, 영문은 코드포인트상 앞). 영문 키 제거(29종)하고 `extractor._clean_agent`에 `KAYO`(구두점 제거·대문자) → `"케이오"` 정규화 추가해 OCR 유입도 통일. 드롭다운은 이미 `sorted(valid_agents())`로 가나다 정렬됨(사용자 확인).
- **경기 상세 수정 버튼 가시화**(`style.css` `.edit-box`): 기존 `<details>` summary가 평범한 텍스트라 버튼으로 안 보임 → summary를 accent 배경 버튼으로 스타일(연필 아이콘, open 시 ✕·회색). 접힌 상태에서도 명확히 클릭 가능.
- **유저 개인창 레이더 차트**(`player.html`): `tier_eff/티어 이력` 표 제거(스펙상 표시 오해 소지였던 블록) → **맵별·요원별 평균 표시점수를 Chart.js radar(다각형)**로. `player_profile`에 `map_stats` 집계 추가(`Match.map_name or "미지정"` 키, 평균 display_score), `tiers` 컨텍스트/쿼리 삭제. 축 3종 미만이면 다각형이 안 되므로 맵은 표 폴백·요원은 안내문. r축 0~1000. pytest 17통과, 전 페이지 200.

### 경기 상세 전체 수정 (요원·ACS·K/D/A)
- 기존 `/match/{id}/score`는 맵·스코어만 수정 → 사용자가 "요원이나 숫자같은것도 전부 수정가능하게" 요청. 라우트를 폼 전체 파싱으로 바꿔 각 `MatchPlayer` 원시값(팀·요원·ACS·K/D/A)을 `mp.id` 키로 갱신. 요원 변경 시 `role`도 `AGENT_ROLE`로 동기화. 방어적: 팀은 A/B, 요원은 `valid_agents()` 내값만 반영, 숫자 무입력=0.
- `match.html` `<details class="edit-box">`(수정 버튼)을 맵/스코어 인라인 + 선수별 편집표(팀 select / 요원 select / ACS·K/D/A number)로 확장. `match_detail` 컨텍스트에 `agents=sorted(valid_agents())` 추가.
- 원시값이 바뀌면 TACR·표시점수·OpenSkill 전부 영향 → 저장 시 `recompute_all()` 유지(누적이라 부분 갱신 불가). 편집 왕복 검증(ACS 457→490, K 26→28 반영·원복 확인), pytest 17통과.

### 닉네임 재확인 정규화 — 교정 시 중복 유저 생성 방지
- 문제: 깨진 OCR 닉을 사람이 검토에서 올바르게 고쳐 입력해도 저장된 발로닉과 **완전 일치**가 아니면(끝 공백·대소문자·유니코드 조합 차이) '신규'로 빠져 같은 사람의 중복 Player가 생김.
- `services.get_or_create_player`: 정확 일치(별칭→display_name) 실패 시 `_norm_nick`(NFC 정규화·`\s+`→단일공백·trim·casefold) 기준으로 별칭·발로닉을 한 번 더 훑어 매칭되면 재사용, 그래도 없을 때만 신규 생성. 저장값 원문은 불변(비교용 정규화만).
- 검증: `  LIEBE ` 등 공백·대소문자 변형이 모두 기존 유저로 귀속, 진짜 새 닉만 신규 생성. pytest 17통과. (이미 만들어진 중복 정리용 병합은 별도 안건으로 보류.)

### 유저 병합 기능 — 중복 Player 정리
- 깨진 OCR 닉을 '신규'로 잘못 확정해 같은 사람의 Player 가 둘 생긴 경우 정리용. `services.merge_players(source→target)`: (1) source `MatchPlayer` 를 target 으로 재지정하되 `(match_id, player_id)` 유니크 충돌 시(동일 경기 이중입력) source 행 폐기, (2) source 별칭 이전 + **source 발로닉을 target 별칭으로 등록**(향후 자동매칭), (3) implied 티어 폐기·manual/henrik 은 target 에 없을 때만 이전(수동설정 유실 방지), (4) source SkillRating·Player 삭제. 파생값은 이후 `recompute_all()` 로 재생성.
- 라우트 `POST /players/{id}/merge`(target_id 폼) + `players.html` '병합' 컬럼(대상 유저 select, confirm 다이얼로그, 되돌리기 불가 경고). 라우트가 커밋 후 `recompute_all()`.
- 검증: 격리 DB 왕복으로 기록 이전·충돌 폐기·별칭 등록·수동티어 이전·source 완전 삭제 확인. pytest 17통과, /players 200. 예방책(정규화 재확인)과 짝으로 재발·기존 중복 모두 커버.

### 유저 관리에서 나간 유저 숨김
- 사용자 요청: 나감 처리된 유저가 유저관리 메인 목록에 계속 보임 → 제거. `players_admin`이 `departed` 여부로 active/departed 분리해 컨텍스트로 전달.
- `players.html`: 메인 표는 활성 유저만. 나간 유저는 하단 접힌 `<details>` "나간 유저 N명"(발로닉·별칭·**복구** 버튼)으로 이동 — 완전 삭제 대신 숨김이라 복구 경로 유지(복구는 기존 `/players/{id}/depart` 토글 재사용). 시즌 초기화 때 완전 제거되는 건 그대로.
- 검증: 나간 유저 링크가 메인 표에서 사라지고 접힌 섹션에만 표시됨 확인. pytest 17통과, /players 200.

### 검토창 — 엔터 오확정 방지 · 닉 매칭 미리보기
- 문제: 검토창에서 닉을 고치고 Enter 치면 폼이 제출돼 '확정 및 저장'이 눌리며 저장돼버림. `review.html` 폼에 keydown 리스너로 input 의 Enter 를 `preventDefault`(확정은 클릭만). 닉네임 input 의 Enter 는 대신 매칭 미리보기 트리거, blur 에도 갱신.
- 신규 조회 API `GET /api/resolve-nick?name=`: `services.resolve_existing_player`(별칭·발로닉 완전→정규화 일치, 생성 없음)로 어떤 유저로 매칭될지 JSON 반환. `get_or_create_player` 도 이 함수를 재사용하도록 리팩터(동작 동일, 미리보기와 확정 로직 일치 보장).
- 미리보기: 매칭되면 res-select 에 해당 유저 옵션을 넣고 선택 + "✓ …로 매칭됨"(초록), 없으면 '신규 유저 생성' 선택 + 안내. 자동매칭 표시도 이름 노출로 개선("✓ 자동 매칭: {이름}", 최초 렌더 시 preview 에도 표기).
- 검증: `/api/resolve-nick` 완전·정규화·미매칭·빈값 케이스 확인, 검토 렌더에 요소 존재. pytest 17통과.

## 2026-07-13 — 업로드 파일명 원본 유지 · 레이팅 문서화 · 기대ACS 팀분리 재설계(v2) · 유저관리 UX · 개인창 승패 · 리더보드 티어필터

### 업로드 스크린샷을 원본 파일명으로 보관 (중복 스킵)
- 문제: 업로드하면 `screenshots/{match_id}.png`(1.png, 2.png…)로 저장돼 원본 파일명이 버려짐. 사용자가 원본 이름 유지를 원함.
- `_create_pending_match`에 `filename` 인자 추가(미지정 시 `{match_id}.png` 폴백). 업로드/인박스 모두 `Path(f.filename).name`(경로요소 제거=트래버설 방지)으로 원본명 저장, 확장자는 `IMAGE_EXTENSIONS`만 허용.
- **중복 정책(사용자 선택)**: 같은 파일명이 이미 있으면 그 파일은 **건너뛰고 경기 레코드도 만들지 않음**. 건너뛴 이름을 모아 홈으로 `?dup=`(urlencode) 전달 → `index.html` 상단 노란 배너 "이미 같은 파일명이 있어 N개 건너뜀". 인박스도 동일(중복이면 인박스에 그대로 남겨 수동 판단). `home()`에 `dup` 쿼리파라미터 추가, `.notice-dup` CSS.
- 검증: pytest 17통과. 브라우저 육안(중복 배너 실렌더)은 미확인 — 사용자 확인 필요.

### 레이팅 설명 문서 추가
- `app/rating/RATING.md` 신설(계산 수행 폴더 내). 표시점수·TACR·OpenSkill을 수식+config 실제값(K_TIER 0.06, 가중치, DISPLAY_SCALE 25, EB m=3 등)과 예시표로 설명. 티어수치화·tier_eff·기대ACS·EB수축까지 전 파이프라인 포함. 시그모이드/수축 예시 수치는 직접 계산해 검증.

### 기대 ACS "팀 분리" 재설계 (v1→v2, PARAMS_VERSION=v2-team-separation)
- **발견**: 기존 §4.3 공식 `lobby_avg × team_factor × (5·weight_i/S_own)` 에서 `team_factor=2S_own/S` 와 `1/S_own` 이 **약분** → 항상 `10·weight_i/S`(로비 전체 정규화)로 붕괴. 즉 "팀 분리"가 수학적으로 **완전 무효**였음(팀 구성 무관). 코드는 스펙을 충실히 구현 → 스펙 공식 자체의 결함. 수학적으로 팀총합·팀내지분이 같은 가중치에 선형이면 항상 약분됨.
- **사용자 결정**: 재설계(팀 격차가 실제로 ACS 기대치에 영향 주도록).
- **새 모델**: 로비 총 ACS `T`를 팀 강도 비 `share_A = S_A^γ/(S_A^γ+S_B^γ)`로 배분 → `team_total_A = T·share_A` → 팀내 `expected_i = team_total_A · weight_i/S_A`. γ=`TEAM_STRENGTH_EXP`(신규, 초기 2.0). **총합 보존**(Σexpected=T) 유지. γ=1이면 다시 약분→붕괴하므로 γ>1 필수. γ>1이면 강팀 기대↑(약팀 상대 farming 할인)·약팀 기대↓(강팀 상대 수행 가산).
- 검증: 초월5 vs 실버5 로비(전원 ACS200)에서 γ=1→269/131(팀효과 없음, weight 차이만), γ=2→323/77(팀 격차 반영), 합 2000 보존 확인. pytest 17통과(스펙 §10 픽스처는 팀 밸런스가 거의 맞아 결과 사실상 불변). `test_expected_acs_matches_lobby_normalization_when_balanced`는 밸런스 시 여전히 정확 일치(이제 주석대로 '밸런스일 때만' 성립).
- 문서 동기화: 스펙 §4.3·RATING.md §1.2 새 공식+폐기 이유로 갱신.
- **재계산 완료(2026-07-13)**: 공식 변경으로 기존 확정경기가 v1 값이라 `python -m app.calibration.recompute` 실행 → **20개 경기 v2 재계산 완료**. 검증: MatchRating 199행 전부 `v2-team-separation`, 표시점수 83~977, 평균 TACR 99.6(기준선 100 근처, 정상). γ=2.0은 초기 가정값, Phase 3 캘리브레이션 대상.

### 유저 관리창 — 저장 시 스크롤 유지(AJAX) + 필터/정렬
- 문제: `players.html`의 편집/티어/별칭 저장이 모두 `/players`로 303 리다이렉트 → 전체 리로드 → 스크롤이 맨 위로 튐. 유저가 많아 찾기 어려운데다 리로드가 클라이언트 필터도 초기화.
- **저장 AJAX화**(라우트 무변경, 점진적 향상): 편집/티어/별칭 폼에 `data-ajax data-kind` 부여. JS가 submit 가로채 `fetch` POST 후 **해당 행 DOM만 제자리 갱신**(라벨 "디코닉 (발로닉)"·수동티어 셀·별칭 셀 + `data-*` 속성) + "저장됨" 플래시. JS 꺼져도 기존 폼 제출로 동작. 병합/나감/삭제/시즌초기화는 목록 구조가 바뀌므로 리로드 유지.
- **필터/정렬 툴바**(클라이언트, 서버 왕복 없음): 검색(닉·별칭 substring), **"디코닉 없는 유저만"** 체크박스, 정렬(이름 가나다 / 수동티어 높은순·낮은순), "N명 표시" 카운트. 각 `<tr>`에 `data-name/discord/aliases/tier`. 저장 후 `applyView()` 재적용이라 디코닉 채우면 "없는 유저만" 필터에서 즉시 사라짐.
- `.toolbar`/`.chk` CSS 추가. 검증: TestClient `/players` 200 + 모든 요소 존재, pytest 17통과. 브라우저 실동작(제자리 갱신·필터)은 육안 미확인.

### 개인창 요원별 성과 — 판수·승패·승률 추가
- 사용자 요청: `/player/{id}` 요원별 표에 판수만 있고 승패가 없음. `player_profile`의 `history` 쿼리에 이미 `mp.team`·`match.team_a/b_rounds`가 있어 **추가 조회 없이** 집계 가능.
- `_match_result(match, team)` 헬퍼 신설(팀 관점 win/loss/draw, 스코어 미입력이면 None→집계 제외). `agent_stats`에 `win/loss/draw` 추가. `player.html` 요원표에 "전적 (승-패[-무])"·"승률"(=win/(win+loss)) 컬럼 추가. 무승부는 승률 분모에서 제외.
- 검증: 최다판 유저(고장난 장난감, id42) 집계가 판수=승+패+무로 정확 일치 확인. TestClient `/player/{id}` 200, pytest 17통과.

### 리더보드 티어 필터
- 사용자 요청: 리더보드를 티어별로 보고 싶음. `_leaderboard_rows`에 유저별 **수동 티어**(source=manual 최신) 조회 추가 → `tier_value`·`tier_name`(=`config.tier_name(value)`) 필드. 홈이 쓰는 `_leaderboard_rows`는 그대로(필터는 route에서만).
- `/leaderboard`에 `tier` 쿼리파라미터(서버사이드, min_games와 동일 폼 패턴). 티어 선택 시 `tier_name==tier`만 남김 → **수동 티어 없는 유저(None)는 자동 제외**(사용자 지시). 순위(loop.index)는 필터된 집합 안에서 재부여. `leaderboard.html`에 티어 드롭다운(전체+티어명 9종)·"티어" 컬럼·필터 안내문 추가.
- 검증: dev 데이터 69명 중 티어 미설정 14명은 필터 시 제외, "불멸" 선택→16명 전원 불멸 확인. `/leaderboard` 및 `?tier=` 200, pytest 17통과.

## 2026-07-14 — 로그인/권한 시스템 · 조정점수 마스킹 · 내 정보 개편 · 나이순 정렬 · 서버장 고정 · 조정점수 분포 곡선

### 로그인·계정·권한 (stdlib 자작, 신규 의존성 0)
- 목적: 일반 유저에게 **조정점수를 숨겨** '줄세우기' 부담을 줄이고(못하는 사람 참여율 방어), 어드민만 전체를 봄. 대문(로그인)·계정 사전발급·첫 로그인 비번 변경·비번 초기화 요구.
- **인증 구현**: `app/auth.py` — `hashlib.pbkdf2_hmac(sha256)`+per-user salt 비번 해시, `hmac` 서명 쿠키(user_id 서명). `itsdangerous`/bcrypt 미도입. `app/config.py`에 `SECRET_KEY`/`DEFAULT_PASSWORD`/`PBKDF2_ITERATIONS`.
- **User 테이블**(alembic 0004): username=디코닉(unique)·player_id FK(unique)·password_hash·must_change_password(기본 True). is_admin 컬럼 없음 — 권한은 저장하지 않는다.
- **어드민 판정 = env 라이브**: `config.admin_usernames()`가 `ADMIN_USERNAMES`(콤마 구분)을 매 요청 파싱, `load_auth_user`에서 `is_admin` 실시간 계산. env에서 닉을 빼면 다음 요청부터 즉시 권한 박탈, 넣으면 재로그인 없이 복귀. `.env` 변경은 프로세스 재시작 필요(load_dotenv는 기동 시 1회, --reload는 .py만 감시).
- **미들웨어/예외**: `main.py` `resolve_user`가 요청당 `request.state.user` 1회 설정. `NotAuthenticated`→303 /login, `PasswordChangeRequired`→303 /account/password(정적·로그아웃·비번변경 경로는 우회), `AdminRequired`→403. 기동 시 `init_db()`+`ensure_accounts()`(디코닉 보유·미이탈 참가자에 default 비번 계정 사전발급).
- **템플릿 주입**: `Jinja2Templates(context_processors=[_user_ctx])`로 `user`/`is_admin`을 전 템플릿에 자동 주입(라우트별 컨텍스트 수정 회피).
- **마스킹 범위(사용자 결정=조정점수만)**: 리더보드·홈에서 남의 `adj_score`는 `•••`, 본인 것만 노출. 정책 일관성 위해 경기상세의 기대ACS/TACR/표시점수 컬럼·수정박스·삭제, 남의 개인창 점수도 비어드민에게 숨김. ACS/K/D/A 등 원시 스탯과 리더보드 TACR/OpenSkill은 유지.
- 대문 `landing.html`(로그인 폼 + 참가자 티어 도넛), `set_password.html`. 검증: TestClient 스모크로 어드민 전체노출/일반유저 마스킹·자기것만·403 확인.

### .env.example / config 잔재 정리
- Claude vision 옛 설계 잔재 `ANTHROPIC_API_KEY`·`EXTRACTION_MODEL` 완전 미사용 확인 → `.env.example`·`config.py`에서 제거. `HENRIK_API_KEY`는 미연결 Phase 2 스켈레톤 전용이라 주석으로만 보존.

### nav 재정렬 · 비밀번호 링크 이동
- nav의 `비밀번호` 링크 제거 → **내 정보 페이지 상단 "비밀번호 변경" 링크**(기존 `/account/password` 재사용). 순서: 일반유저 [닉네임, 내 정보, 리더보드, 로그아웃], 어드민은 사이에 [유저 관리, 업로드]. `<title>`도 역할별(유저 관리/내 정보)로.

### 내 정보 화면 개편 (일반 유저)
- 문제: 일반 유저 "내 정보"가 관리자용 표를 한 행만 재사용해 어색(관리 컬럼 껍데기·관리 헤더). → 관리 표는 `{% if is_admin %}`로 감싸고, 일반 유저는 **정보 카드**(`dl.info` 2열: 발로란트 닉/디스코드 닉/OCR 인식 닉네임/수동 티어)로 분기. "내 경기 기록 보기 →" 링크 포함.
- 라벨 조정(사용자 요청): 카드의 "별칭"→**"OCR 인식 닉네임"**, 수동 티어는 값만이 아니라 **티어명 병기**("골드 (3.0)", `players_admin`이 `config.tier_name` 계산해 entry에 `tier_name` 추가).

### 로그인 폼 레이아웃 깨짐 = CSS 캐시
- 증상: 로그인 입력칸이 2열로 꾸겨짐. 원인은 `.stack` 규칙이 추가된 뒤에도 브라우저가 **옛 style.css 캐시**를 사용(링크에 캐시버스터 없음). `style.css?v=2`로 강제 재요청 + `form.stack input{width:100%}` 명시.

### 리더보드 — 일반 유저 나이순 정렬 · 서버장(Vice) 고정
- 사용자 요청: 일반 유저에겐 조정점수순이 무의미(숨김) → **나이순 정렬**. 디코닉 앞 두 자리를 출생연도로 해석(`_age_key`: 30 이상=19XX, 미만=20XX)해 오래된 해 먼저(9X→0X), 동률은 이름 가나다. 어드민은 조정점수순 유지. `_leaderboard_rows(by_age=not is_admin)`, 안내문구 역할별.
- **서버장 Vice 최상단 고정**: `SERVER_OWNER="Vice"`, 정렬 후 안정 정렬로 discord=="Vice" 행을 맨 앞으로(나머지 순서 보존). 검증: dev 69명에서 Vice가 1번, 나이순 88→92→99→00 순.

### 홈 "리더보드 요약" → 조정점수 분포 곡선
- 문제: 요약 패널이 옛 조정점수순 top-5를 노출(줄세우기·정렬 불일치). 제거하고 **조정점수 분포 곡선**으로 교체(사용자가 곡선안 선택).
- `_score_distribution`: 전체 adj_score를 12구간 히스토그램 카운트 + 본인 위치 비율(0~1)만 계산. **개별 수치·축 눈금·툴팁 없음**(남의 점수 비노출). Chart.js line(tension 0.45, fill)로 곡선, 커스텀 `afterDraw` 플러그인이 내 위치에 점선+▲+"나" 마커. 경기 없으면 화살표 생략, 3명 미만이면 곡선 대신 안내.
- 검증: dist 있음/본인없음/없음 3상태 렌더 확인, 분포 12빈·my_frac 계산 확인.

---

## 2026-07-17 — 유저 관리 필터/기본 숨김 · 리더보드 정렬 토글 · 분포 곡선 오버슈트 · 숫자 입력 정리 · 보안 점검

### 유저 관리창 — "티어 없는 유저만" 필터 · 기본 숨김 · 계정 관리 검색
- 사용자 요청: (1) 기존 "디코닉 없는 유저만" 옆에 **"티어 없는 유저만"** 필터 추가. (2) 기본으로 전 유저가 보이던 것을 **검색어·필터 없으면 아무도 안 뜨게** 바꿈(닉·별칭 검색해야 표시). (3) 계정 관리(비번 초기화) 표도 동일하게 검색해야 표시.
- `players.html`: 툴바에 체크박스 `pf-notier`, JS `applyView`에 `matchT = !onlyT || r.dataset.tier===''` 조건. **기본 숨김 게이팅**: `active = q!=='' || onlyD || onlyT` 이 false면 전 행 hidden + `pf-hint` 안내문. 활성 시에만 표시·카운트.
- 계정 관리: 검색창 `ac-search` + `accounts-tbody` + 행별 `data-search="{username} {label}"`, 같은 is_admin `<script>`에 `acView()`(기본 전부 숨김, 검색 매칭만 노출).
- 검증: pytest 17통과, Jinja 로드 OK. 브라우저 육안은 사용자 확인.

### 리더보드 — 어드민 정렬 토글 (@/! 버튼)
- 이전: 어드민=조정점수순 고정 / 일반=나이순. 사용자 요청으로 **기본을 모두 나이순**으로 바꾸고, 어드민에게만 정렬 전환 버튼 노출.
- `routes.leaderboard`에 `sort` 쿼리파라미터(`age`|`score`), `by_score = user.is_admin and sort=="score"`. **일반 유저는 URL로 `?sort=score`를 붙여도 무시**(점수 마스킹돼 무의미+누출 방지). `_leaderboard_rows(by_age=not by_score)`.
- `leaderboard.html`: "정렬:" 텍스트·본문 프리픽스 제거. 제목 우측 상단에 아이콘 버튼 배치(`.lb-head` flex space-between, `.sort-toggle`) — **@ = 나이순, ! = 조정점수순**(`title` 툴팁, `.active`는 발로 레드). `.btn-sort` 여백 있는 버튼 스타일(min-width 44px, padding 10px 18px, font 18px). 링크에 tier·min_games를 `urlencode`로 보존.
- **CSS 캐시 재발**: 처음엔 style.css 버전을 안 올려 사용자에게 "파란 밑줄 링크·좌측 정렬"(=스타일 미적용)로 보임. 원인은 `style.css?v=2` 유지 → **`v=3`으로 올려 해결**(base.html). CSS 손대면 버전도 같이 올릴 것.

### 서버장(Vice) 고정 = 나이순 한정
- 사용자 지적: 조정점수순인데도 Vice가 맨 위. `_leaderboard_rows`의 SERVER_OWNER 맨위 고정 sort를 `if by_age` 블록 안으로 이동 → **나이순일 때만 고정**, 조정점수순에선 Vice도 실제 순위대로.

### 홈 조정점수 분포 곡선 — 오버슈트(그래프 뚫림) 수정
- 증상: 인원이 많아지자 `tension:0.45` 스플라인 정점이 차트 위로 뚫고 나감. 원인은 y축이 데이터 max에 자동 맞춰져 bezier 오버슈트가 잘림(음수 없이 상단만). `index.html`에서 `yMax = Math.max(...counts)*1.35`를 `scales.y.max`로 지정해 상단 여유 확보.

### 업로드/검토 숫자 입력 — 스피너 제거 + 숫자 전용
- 사용자 불편: 검토 화면 스탯칸의 number 스피너(↑↓)를 실수로 눌러 값이 ±1 되던 것.
- `review.html`(스코어·ACS·K/D/A·효율·FK·설치·해제)·`upload.html`(스코어) 전 stat input을 `type="number"` → `type="text" inputmode="numeric"` + `.numonly` 클래스. `input` 이벤트로 `replace(/[^0-9]/g,'')` 숫자만 남김. 빈칸 허용·서버 파싱 불변. 리더보드 min_games는 추출영역 아니라 그대로.

### 보안 점검 + 조치 (사용자 승인 2건)
- 전체 인증 흐름(`auth.py`/`main.py`/쿠키/최근 변경) 점검.
- 🔴 **[치명적] SECRET_KEY가 기본값 `dev-insecure-change-me` 그대로였음** — 세션 쿠키는 `user_id.HMAC(SECRET_KEY, user_id)`라 서명 키가 공개 기본값이면 **누구나 아무 user_id(어드민 포함) 쿠키를 위조** 가능한 인증 우회. → `.env`를 `secrets.token_hex(32)` 랜덤값으로 교체.
- 🟢 **세션 지속** — 로그인 쿠키가 `max_age=30일` 지속 쿠키라 창 닫아도 유지됐음. 사용자 선택 "창 끄면 로그아웃" → `routes.login_submit`에서 `max_age` 제거해 **세션 쿠키화**(브라우저 종료 시 삭제).
- **주의: 둘 다 서버 완전 재시작 필요**(.env는 기동 시 1회 로드). 재시작 시 SECRET_KEY 변경으로 기존 쿠키 전부 무효 → 전원 재로그인(정상).
- 미조치(권장만): HTTPS 배포 시 쿠키 `secure=True` 추가, 기본 비번(valorant) 선점 리스크(디코닉 공개+must_change라 진짜 유저보다 먼저 로그인하면 계정 선점 가능 — 지인 규모라 저위험).
- 양호: pbkdf2+salt·`compare_digest` 상수시간 비교·httponly·samesite=lax·require_user/admin 이중 게이팅·sort/tier 파라미터 검증(새 취약점 없음).

---

## 2026-07-20 — Henrik(발로란트 비공식 API) 연동 · Riot ID 백필 · 업로드 시 자동 스탯 보정

### 커스텀 게임 조회 검증 (Phase 2 착수)
- 이전엔 `app/henrik/client.py`가 미연결 스켈레톤. 실제 응답으로 아래 사실 검증:
  - **커스텀 게임이 매치 히스토리에 뜸**(KR). `/v4/matches/{region}/pc/{name}/{tag}`가 각 매치의 **10명 전체 로스터(puuid·name·tag·agent·KDA·score→ACS)를 인라인 포함** → 지문매칭·enrichment에 필요한 데이터 다 있음.
  - `?mode=custom`은 **오래된** 매치, 필터 없는 조회는 **최근** 매치 → 커버리지 위해 둘 다 조회.
  - 매치 상세는 지역 필요: `/v4/match/{region}/{id}`(옛 `/v4/match/{id}`는 404).
  - **Rate limit 무료티어 30 req/60s.** reset-헤더 백오프는 과다수면 버그 유발 → **호출 간 고정 2.2s 페이싱**이 정답(`client.py` `_pace`).
- `client.py`: `_MIN_INTERVAL=2.2`, `_pace`/429 reset 대기(상한 60s), `get_matches(region,name,tag,mode=None)`, `get_match`에 지역경로 추가.

### Riot ID 백필 (스노우볼)
- 씨앗=Étoile(id30). 매칭된 유저를 다시 시드로 재귀 확장(라운드4서 수렴) → **81명 중 76명** riot_name/riot_tag 확보(34명은 puuid도). **미해결 5명**(수동): id37 김진효(#123/#127 충돌), id78 발로란트잼민이(2계정), id38 멍멍아 야옹해봐(ツ/シ 유니코드차), id18 MIN5E0 O(OCR뭉갬), id35 '대기'(플레이스홀더).
- **멀티계정 Riot ID:** `player_riot_accounts` 테이블(모델 `PlayerRiotAccount`, `Player.riot_accounts`). 유저관리 UI에 Riot ID 칼럼(칩+추가/삭제 AJAX `data-kind=riot-add|riot-del`)·내정보 표시. 라우트 `POST /players/{id}/riot`·`/riot/delete`.

### 업로드 시 자동 Henrik 보정 (핵심 기능)
- 동기(사용자): OCR이 불완전해 스탯 손보정이 번거로움 → 업로드 시 매칭된 등록유저의 Riot ID로 매치 히스토리 조회 → 그 경기를 지문매칭 → 10명 권위 스탯으로 덮어쓰기.
- 사용자 결정(AskUserQuestion): **항상 자동** 보정(단일+다중), **자동 덮어쓰기+변경 표시** 배너.
- `app/henrik/enrich.py` `Enricher`: `_create_pending_match`가 OCR 직후 호출(best-effort, 실패는 업로드 무해). 시드별 최근+커스텀 조회 → 지문매칭 → K/D/A·ACS·요원·닉 덮어쓰고 `ExtractionResult.corrections`에 변경목록 → `review.html` 배너 표시. 배치(다중 업로드) 동안 클라이언트·매치리스트 캐시 재사용, `upload_submit`/`inbox_process`가 try/finally로 `close()`.
- **지문매칭 핵심 교훈(비자명):** 같은 멤버가 여러 판을 하므로 **로스터 닉 겹침만으론 '어느 판'인지 못 고름**(모든 판이 10/10 닉일치). 또 닉·요원은 OCR이 깨지는(보정 대상) 필드라 **판별에 쓰면 안 됨**. → 오직 **숫자(KDA+ACS)로만** 판별: `_cost`=|ΔK|+|ΔD|+|ΔA|+0.5·|ΔACS|, `_assign`=그리디 1:1, `_best_match`가 임계(`_PAIR_TOL=6.0`)내 인원 최대·총거리 최소 선택(`_MIN_MATCHED=6`). 실데이터 match#42=`cfec67c3`로 matched=10 vs 오답 ≤2 큰 마진. 17개 항목 보정(깨진 닉 복구: '1 층* 토람명'→'토랑멍', 'Etoile'→'Étoile' 등).

### 후속 3건 수정 (사용자 지적)
- **① 요원 미보정(신규유저 Mona Lisa):** 비토를 했는데 게코 그대로. 원인=`config.AGENT_EN_TO_KR`에 `veto`가 없어 `agent_kr_from_en('Veto')`→None→스킵. → `"veto":"비토"` 추가. (미매핑 남은 것: `믹스` 영문명 미상.)
- **② 날짜 보조조건 추가:** `metadata.started_at` vs `Match.played_at` 일수차를 `_best_match`의 `key=(matched, date_bonus, -total)` **중간 tiebreak**로만 사용. 하드필터는 타임존/OCR 날짜 오차로 정답을 걸러냈던 이력 있어 **소프트**로.
- **③ FK/설치/해제 비어있음:** 원인=매치 **리스트** 응답엔 이 필드 없음(KDA·score만). → **상세**(`/v4/match/kr/{id}`) 추가 조회해서 계산. **FK**=`kills[]`의 라운드별 최소 time_in_round의 killer.puuid, **설치/해제**=`rounds[].plant/defuse.player.puuid`. `_detail_stats`가 puuid별 집계(로스터 전원 0 초기화 → '안 함'도 권위값 0), `_apply`가 puuid 매핑해 `ExtractedRow.first_kills/plants/defuses` 채움. match#42 검증: 20라운드→FK합 20, 10명 전원 채워짐.
- 검증 후 match#42 `extraction_raw` 재보정·저장(사용자가 /review/42 새로고침해 확인). 팀(A/B)은 OCR 색판정 유지(미보정).

### 남은 작업
- 미해결 5명 수동 처리 · 42명 puuid 보강(예산 회복 후) · headshot%/ADR/first_deaths enrichment(현재 미보정) · `믹스` 영문명 확보 후 매핑. (검증용 임시 스크립트 6개는 정리 완료 — 루트엔 진입점 `main.py`만.) DB 백업: `data/scrim.db.bak-20260720-193637`.

## 2026-07-21 — 헤드투헤드(라이벌/도전자/천적/사냥감) 기능

### 개요
킬 단위 유저 구도 기능. Henrik 매치 상세 `kills[]`(킬마다 killer/victim puuid)로 10×10 킬 매트릭스를 복원해 등록 유저간 "누가 누구를 몇 번 잡았나"를 저장하고, 개인 페이지에 라이벌·도전자·천적·사냥감 4개 관계를 표시.

### 저장 구조 · 매핑
- `HeadToHeadKill(match_id, killer_id, victim_id, kills)` — **경기 단위** 저장이라 재확정/재백필 시 그 경기 행만 delete+insert → 멱등. 스키마는 이 프로젝트 관례대로 alembic 아닌 `create_all`(신규 테이블 마이그레이션 안 씀).
- **puuid→player 매핑을 OCR 행 배정이 아니라 `PlayerRiotAccount`(puuid 우선, 없으면 name#tag)로** — enrich 코드와 결합 안 되고 로스터에 puuid+name#tag 다 있어 브릿지 가능, 더 정확.
- 실시간 훅: `routes.confirm()`이 `save_and_rate` 뒤 best-effort `populate_match`(상세 1콜/확정). Henrik 실패는 확정을 막지 않음.
- 백필 CLI: `uv run python -m app.henrik.backfill_h2h [--recover] [--force]`. 이미 채운 경기 스킵. `--recover`는 henrik id 없는 옛 경기를 확정 MatchPlayer 스탯으로 지문매칭(`Enricher(max_seeds=10)`, 참가자 전원 시드·첫 성공서 중단)해 역추적.

### 판정 규칙(사용자 확정, 방향 기준)
티어=최신 `PlayerTier(source=="manual")`의 밴드 인덱스(아이언0…레디언트8). `tier_diff = 내밴드 − 상대밴드`. 함께 `MIN_ENCOUNTERS=3`판+. 티어 미설정 유저는 판정 제외. **티어는 차이 크기가 아니라 방향만** 본다. 한 쌍은 서로의 페이지에 다른 라벨로 뜨는 상호 관계:
- **라이벌**: 상대 동티어~상위(`tier_diff≤0`) & 킬차 ≤`RIVAL_MARGIN(2)`. 위 티어에 대등하게 맞섬.
- **도전자**: 상대 하위(`tier_diff≥1`) & 킬차 ≤2. 라이벌의 반대편(아래인데 감히 대등).
- **천적**: 상대 동티어~하위(`tier_diff≥0`) & (상대킬−내킬)≥`NEMESIS_MARGIN(3)`.
- **사냥감**: 상대 상위(`tier_diff≤-1`) & (내킬−상대킬)≥3. 천적의 반대편(업셋·고양감).
- 동티어 천적은 위/아래 방향이 없어 **패자 쪽에만** 뜸(사냥감 반대편 안 생김) — 규칙상 정상.

### 티어 판정 변천(비자명)
① 초기 `abs(tier_diff)` → 상위에게 잡히는 당연한 걸 천적 오탐(플래 Étoile↔불멸 아가라구요 14:6). ② 부호+차이크기(천적 tier_diff≥2 등)로 수정 → 그 규칙에선 천적 0건(2티어+ 아래 업셋이 없어서). ③ **최종: 차이 크기 버리고 방향만**(위 규칙) + 상호 라벨(도전자·사냥감) 추가.

### 데이터 커버리지(역추적)
확정 50판 중 `--recover`로 **40판 enrich(1522행)**. 못 찾은 10판은 전원 시드로도 실패 → **원인은 날짜·시드부족이 아니라**(같은 날 경기도 성패 혼재, 실패 경기도 9~10/10 시드 있었음) Henrik 무료티어 매치리스트가 얕아 각 플레이어 타임라인 양 끝(recent/custom)만 덮고 중간이 빔 + 기존 라이브 시드 3명 상한. 백필에서 `max_seeds=10`으로 25→40판까지 확대(라이브 업로드는 rate limit 절약차 기본 3시드 유지). 관계 유저 17명, 라이벌 16·도전자 8·천적 8·사냥감 1쌍.

### UI(임시)
개인 페이지 `player.html`에 4패널(라이벌/천적 항상, 도전자/사냥감은 있을 때만 행 렌더). 킬 데이터라 `can_see_scores` 게이트 없이 전원 노출. 열: 상대·`킬 : 데스`(천적만 `데스 : 킬`)·**교전 N판**("함께"에서 변경). UI는 최소 테이블 — 사용자가 "재밌게" 재디자인 예정.

### 남은 작업
- UI 재디자인(방향 열림: 천적 랭킹, 상대별 승패 등) · VM 업로드 방법 별도 킵 · 영구 미복구 10판.

## 2026-07-22 — 승패 대규모 오류 발견·교정 · Henrik 팀/결과 권위화

### 증상 · 원인(체계적)
사용자 지적: "실제 승패와 기록 승패가 다른 경기가 있다." 전수 조사 결과 **산발이 아니라 체계적 버그**. 확정 56경기 중 **28경기가 오답**.
- **근본 원인:** 검토 화면 스코어 입력 시 배너의 **큰 숫자(승리팀 13)를 거의 항상 A칸**에 넣음. 팀 A는 OCR 규칙상 "초록=업로더 팀"이라, **업로더 팀이 진 경기**에서는 A칸에 상대(승자) 점수가 들어가 `_match_result`가 그 경기 **10명 전원의 승패를 반전**. OCR이 스코어 배너를 대부분 못 읽어(None) 사람이 손입력 → 이 실수가 절반 가까이 누적. (개인 TACR·표시점수는 개인 ACS 기반이라 무관, **OpenSkill은 승자 기준이라 오염** → 재계산 필수.)
- **부차 원인:** 행 배경색 판정(`_team_of_row`)이 **저채도·어두운 행**(초록·빨강 픽셀 둘 다 ~0%)에서 동전던지기 → 한 명이 반대팀으로 오배정돼 4:6 불균형. 반복 피해자 **Étoile**(어둡게 렌더). match 17·30·51 Étoile, 28 aziiiiiiin.

### 검증 방법(권위)
- **Henrik `teams[].won` + player `team_id`(Red/Blue)** 가 팀·결과의 절대 정답. 46경기(henrik_match_id 보유)를 전수 대조: 저장 MatchPlayer를 puuid/name#tag로 로스터에 매핑 → 각자 실제 소속팀·승패 vs 저장 비교.
- 초기에 head_to_head 킬그래프로 이분할했으나 **A/B 라벨을 저장값에 정렬**시켜 승패 방향이 틀림(Étoile를 승리팀으로 오판) → **Henrik teams[].won로 재검증**해 바로잡음. 교훈: 킬그래프는 "그룹핑"만, **승패 방향은 반드시 teams[].won**.
- henrik 없는 10경기: 스크린샷 있는 22·23·30·32는 배너 육안 확인(22/23/32 정상, 30 "9 패배 13"→오답). 5·8·9·10·13·14는 스크린샷·henrik 둘 다 없어 확증 불가.

### 교정 범위(적용)
- **라운드칸 스왑(A↔B 숫자 교환)** — henrik 확정 24경기: 3·11·12·19·21·24·29·31·34·35·36·37·38·40·43·44·45·47·50·52·53·55·56·57.
- **스왑 + 팀 재배정:** 17(Étoile B→A), 28(aziiiiiiin B→A), 30(Étoile B→A, 스크린샷 기준).
- **팀 재배정만:** 51(Étoile B→A, 라운드는 이미 맞음).
- **확증 불가 처리(사용자 결정):** 5·8·9·10·13은 동일 패턴(A=13/B<13, 5:5)이라 **패턴 신뢰로 스왑**. 14(A4/B6)는 팀 배정이 깨졌으나 근거 없어 **보류**.
- 적용 후 `python -m app.calibration.recompute`로 전 경기 match_ratings·OpenSkill 재계산. DB 백업 선행.

### 재발 방지 — Henrik enrich에 팀·라운드 권위화(#2, 사용자 요청)
`app/henrik/enrich.py`가 지금은 K/D/A·ACS·요원·닉만 덮어씀. **팀(team_id→A/B)·팀별 라운드**도 권위값으로 가져오도록 확장 → 사람이 스코어칸을 잘못 넣어도 자동 교정. `_parse_roster`에 `team_id` 추가, `teams[].rounds.won` 파싱, `_apply`에서 `row.team`·`ExtractionResult.team_a_rounds/team_b_rounds` 설정(A/B 라벨은 기존 저장 다수결에 맞춰 매핑). henrik 매칭 성공 시에만 적용(실패는 기존 OCR 색판정 유지).

### 적용 완료 (2026-07-22)
- **승패 교정 실행:** 36건 변경(라운드 스왑 30 + 팀 재배정 4 + 스크린샷/패턴). `recompute` 완료(56경기). 스팟체크: m17 A8/B13·Étoile=A(패), m30 A9/B13·Étoile=A(패, 스크린샷 일치), m51 Étoile=A(승) 정상. m14는 보류대로 미변경. DB 백업 `data/scrim.db.bak-*` 다수 보존.
- **#2 코드 반영:** `enrich.py` `_team_rounds()` 신설, `_apply`에 팀/라운드 권위화 블록. 또한 `routes.py:_store_pending_match`에서 **henrik 매칭 성공 시 팀별 라운드를 폼/OCR 손입력보다 우선**(라운드 보정은 `Correction(field="라운드")`, 팀 보정은 `field="팀"`으로 검토 배너에 표시). 합성 테스트로 A라벨 유지·라운드 스왑·보정기록 검증. 기존 17테스트 통과.

### Riot ID 백필 (2026-07-22)
riot 닉 없던 유저 13명 중 12명을 **확정경기 Henrik 로스터에서 KDA 정확일치로 신원 복구**. 방법: 각 미등록 유저의 MatchPlayer (k,d,a)를 그 경기 henrik 로스터와 정확 대조 → 기존 등록 신원(puuid·name#tag) 제외 → **같은 puuid가 2판 이상** 나온 것만 채택(1판 우연충돌 배제). puuid까지 저장해 이후 enrich 매칭이 name#tag보다 정확해짐. **id78은 2계정**(발로란트 잼민이들 과추 자르기#마렵네 / 코리안 전용 변기 빅나티#넣고 내려) 모두 추가. 결과 **88/89명** 보유. 남은 id35 '대기'는 henrik 확정경기가 없어 복구 불가(플레이스홀더 추정).

### 다음 세션(내일) 할 일 — 배포(A안) 마무리
승패·Riot 백필은 종료. 다음은 GCP 배포(아키텍처 A안, 코드·로컬검증·GitHub push는 완료 상태):
1. `deploy/vf.service`(systemd)·`deploy/backup.sh` 작성
2. GCP e2-micro VM 생성(us-central1/west1/east1) · `git clone` · `uv sync`
3. `data/scrim.db`만 scp 시딩(스크린샷 dir 불필요)
4. VM `.env` 세팅(실 시크릿, `ENABLE_LOCAL_UPLOAD=0`)
5. systemd 기동 + `tailscale funnel 8000`(HTTPS)
6. 로컬 `.env`에 `CLOUD_BASE_URL`·`INGEST_API_KEY` 넣고 `python -m app.ingest.push`로 실 스크린샷 최종 왕복 검증
7. 백업 cron 등록

부차 백로그: head_to_head UI 재디자인, 나머지 유저 puuid 보강(현재 46명), `믹스` 영문 요원명 매핑.
