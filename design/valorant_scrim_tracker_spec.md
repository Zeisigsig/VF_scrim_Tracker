# 발로란트 내전 퍼포먼스 트래커 — 프로젝트 스펙

> 이 문서는 Claude Code가 읽고 구현하기 위한 스펙이다.
> Phase 순서대로 구현하며, 각 Phase의 완료 기준(DoD)을 만족한 뒤 다음 Phase로 넘어간다.

---

## 0. 프로젝트 개요

발로란트 내전(커스텀 게임) 결과를 수집·저장하고, **티어 격차를 보정한 개인 퍼포먼스 점수(TACR)**를 계산해 웹페이지로 보여주는 시스템.

- 사용자: 소규모 내전 커뮤니티 (동일 멤버 풀이 반복 참여)
- 핵심 문제: 내전은 티어가 섞여서 절대 스탯(ACS 등)만으로는 공정한 비교가 불가능. 티어 기대치 대비 성과로 평가해야 함
- 데이터 입력: **내전 종료 직후 결과 스코어보드 스크린샷**을 업로드 → VLM(Claude API)으로 구조화 추출 (Phase 1의 유일한 입력 경로)
- 보조 데이터: HenrikDev 비공식 API로 PUUID·랭크 조회 (Phase 2)
- 출력: 웹 대시보드 (경기 목록, 경기 상세, 선수 프로필, 리더보드)

### 명시적 비목표 (구현하지 말 것)
- Discord 봇 (추후 별도 프로젝트)
- 실시간 자동 수집 / 스케줄러 (스크린샷은 사용자가 내전 직후 수동 업로드)
- 로그인/멀티테넌시 (단일 운영자, 로컬 또는 개인 서버)

---

## 1. 기술 스택

| 영역 | 선택 | 이유 |
|---|---|---|
| 언어 | Python 3.11+ | 사용자 주력 언어 |
| 웹 | FastAPI + Jinja2 템플릿 + Chart.js (CDN) | 가볍고 단일 컨테이너로 충분. SPA 불필요 |
| DB | **SQLite** (파일: `data/scrim.db`) | 무료, 서버 불필요, PyCharm Database 툴로 바로 열람 가능 |
| ORM | SQLAlchemy 2.x + Alembic (마이그레이션) | 스키마 변경이 잦을 예정 |
| 검증 | Pydantic v2 | 추출 JSON 검증 |
| VLM 추출 | Anthropic Python SDK (`anthropic`), Messages API vision 입력 | 스크린샷 → JSON |
| 레이팅 | `openskill` 패키지 | 누적 실력 추정 |
| 패키징 | Docker + docker-compose | 배포용. 로컬 개발은 PyCharm venv 직접 실행 |
| 테스트 | pytest | |

환경 변수 (`.env`, `python-dotenv`):
```
ANTHROPIC_API_KEY=...
HENRIK_API_KEY=...        # Phase 2
DB_PATH=data/scrim.db
```

Anthropic API 참고 문서: https://docs.claude.com/en/api/overview (vision 입력, Messages API).
모델은 설정값으로 분리한다: `EXTRACTION_MODEL=claude-sonnet-4-6` (기본값. 구현 시점의 최신 문서에서 사용 가능한 모델명 확인 후 필요하면 조정).

---

## 2. 디렉토리 구조

```
scrim-tracker/
├── app/
│   ├── main.py                 # FastAPI 엔트리
│   ├── config.py               # 설정/env 로드, 튜닝 파라미터 상수
│   ├── db/
│   │   ├── models.py           # SQLAlchemy 모델
│   │   └── session.py
│   ├── ingest/
│   │   ├── extractor.py        # 이미지 → Claude API → JSON
│   │   ├── schemas.py          # Pydantic 추출 스키마
│   │   ├── validator.py        # sanity check
│   │   └── matcher.py          # 닉네임 → player 매칭 (별칭 처리)
│   ├── rating/
│   │   ├── tier.py             # 유효 티어 계산
│   │   ├── tacr.py             # 기대 ACS, TACR, 표시점수
│   │   └── openskill_engine.py # μ/σ 업데이트
│   ├── henrik/                 # Phase 2
│   │   └── client.py
│   ├── calibration/            # Phase 3
│   │   └── calibrate.py
│   ├── web/
│   │   ├── routes.py
│   │   ├── templates/
│   │   └── static/
│   └── tests/
├── alembic/
├── data/                       # .gitignore (db, 업로드 이미지 보관)
│   └── screenshots/
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── README.md
```

---

## 3. 데이터 모델 (SQLite 스키마)

원칙: **원시값을 저장하고 파생값은 조회 시 계산하거나 재계산 가능한 형태로 저장**한다. 파라미터 튜닝 후 과거 경기 전체 재계산이 가능해야 한다.

```sql
-- 선수 (불변 식별자 중심)
CREATE TABLE players (
    id INTEGER PRIMARY KEY,
    puuid TEXT UNIQUE,                -- Phase 2에서 채움. Phase 1에서는 NULL 가능
    display_name TEXT NOT NULL,       -- 대표 표기명
    riot_name TEXT, riot_tag TEXT,    -- name#tag (Phase 2)
    created_at TEXT NOT NULL
);

-- 닉네임 별칭 (스크린샷 닉네임 → player 매칭용)
CREATE TABLE player_aliases (
    id INTEGER PRIMARY KEY,
    player_id INTEGER NOT NULL REFERENCES players(id),
    alias TEXT NOT NULL,              -- 스코어보드에 표시된 원문 닉네임
    UNIQUE(alias)
);

-- 티어 정보 이력
CREATE TABLE player_tiers (
    id INTEGER PRIMARY KEY,
    player_id INTEGER NOT NULL REFERENCES players(id),
    source TEXT NOT NULL,             -- 'manual' | 'henrik_current' | 'henrik_peak' | 'implied'
    tier_value REAL NOT NULL,         -- 4절 매핑 기준 수치
    ranked_games_in_act INTEGER,      -- source=henrik_current일 때
    recorded_at TEXT NOT NULL
);

-- 경기
CREATE TABLE matches (
    id INTEGER PRIMARY KEY,
    external_match_id TEXT UNIQUE,    -- HenrikDev match id (Phase 2, nullable)
    played_at TEXT NOT NULL,          -- 사용자가 입로드 시 입력 (기본값: 업로드 시각)
    map_name TEXT,
    team_a_rounds INTEGER, team_b_rounds INTEGER,   -- 스크린샷에 없으면 업로드 폼에서 수동 입력
    screenshot_path TEXT,
    extraction_raw JSON,              -- VLM 원본 응답 (디버깅/재처리용)
    created_at TEXT NOT NULL
);

-- 경기별 개인 기록 (스크린샷에서 나오는 원시값 전부)
CREATE TABLE match_players (
    id INTEGER PRIMARY KEY,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    player_id INTEGER NOT NULL REFERENCES players(id),
    team TEXT NOT NULL,               -- 'A' | 'B'
    agent TEXT NOT NULL,
    role TEXT NOT NULL,               -- duelist|initiator|controller|sentinel (요원→역할 매핑 상수)
    acs INTEGER NOT NULL,
    kills INTEGER NOT NULL, deaths INTEGER NOT NULL, assists INTEGER NOT NULL,
    econ_rating INTEGER,              -- "효율" 컬럼
    first_kills INTEGER, plants INTEGER, defuses INTEGER,
    -- Phase 2 enrichment (nullable)
    kast REAL, adr REAL, first_deaths INTEGER, headshot_pct REAL,
    UNIQUE(match_id, player_id)
);

-- 경기별 계산 결과 (재계산 가능하지만 이력 추적을 위해 저장)
CREATE TABLE match_ratings (
    id INTEGER PRIMARY KEY,
    match_player_id INTEGER NOT NULL REFERENCES match_players(id),
    params_version TEXT NOT NULL,     -- 어떤 파라미터 세트로 계산했는지
    tier_eff_used REAL NOT NULL,      -- 계산에 사용된 유효 티어
    expected_acs REAL NOT NULL,
    tacr REAL NOT NULL,
    display_score REAL NOT NULL,      -- 0–1000
    computed_at TEXT NOT NULL
);

-- OpenSkill 누적 레이팅
CREATE TABLE skill_ratings (
    player_id INTEGER PRIMARY KEY REFERENCES players(id),
    mu REAL NOT NULL, sigma REAL NOT NULL,
    games_counted INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
```

요원→역할 매핑은 `config.py`에 dict 상수로 둔다 (예: 제트/레이나/레이즈/피닉스/네온/요루/아이소=duelist, 소바/스카이/브리치/KAY/O/페이드/게코=initiator, 오멘/브림스톤/바이퍼/아스트라/하버/클로브=controller, 사이퍼/킬조이/세이지/체임버/데드록/바이스=sentinel). 신규 요원 추가를 고려해 미지 요원은 업로드 검토 화면에서 수동 지정.

---

## 4. 계산 로직 (핵심)

모든 수치 파라미터는 `config.py`에 상수로 모으고 `PARAMS_VERSION` 문자열로 버전 관리한다. 아래 초기값은 **가정값이며 Phase 3에서 데이터로 재적합 대상**임을 코드 주석에 명시할 것.

### 4.1 티어 수치화

Iron 1 = 0, 디비전당 +1. 디비전 미상이면 티어 중간값.

| 티어 | 범위 | 중간값 |
|---|---|---|
| 아이언 | 0–2 | 1 |
| 브론즈 | 3–5 | 4 |
| 실버 | 6–8 | 7 |
| 골드 | 9–11 | 10 |
| 플래티넘 | 12–14 | 13 |
| 다이아 | 15–17 | 16 |
| 초월 | 18–20 | 19 |
| 불멸 | 21–23 | 22 |
| 레디언트 | 24 | 24 |

### 4.2 유효 티어 (tier_eff)

선수마다 계산. 우선순위:

1. **OpenSkill μ가 존재하고 games_counted ≥ 3** → `tier_eff = μ` (내전 데이터가 랭크보다 우선)
2. 아니면 랭크 기반 prior:
   - 현재 액트 랭크 있음: `c = n_games / (n_games + 10)` (판수 신뢰도)
     `tier_eff = c × current + (1−c) × peak_decayed`
     `peak_decayed = peak_tier − 0.5 × (peak 달성 후 경과 액트 수)`, 하한 = current
   - 현재 랭크 없고 최고 랭크만 있음: `tier_eff = peak_decayed`
   - 아무 지표 없음(언랭): `tier_eff = 해당 경기 로비의 tier_eff 중앙값` (그 경기 한정), σ 크게

### 4.3 기대 ACS — 팀 분리 공식

ACS는 적팀 상대로 버는 값이므로 팀 간 격차와 팀 내 지분을 분리한다.
로비 총 ACS 를 팀 강도 비로 두 팀에 배분한 뒤 팀 내부에서 가중치 지분으로 나눈다.

```
weight_i = exp(K_TIER × tier_eff_i) × ROLE_COEF[role_i]
    K_TIER = 0.06 (초기값)
    ROLE_COEF = {duelist: 1.10, initiator: 1.00, controller: 0.92, sentinel: 0.90} (초기값)

S_A = Σ weight (팀 A), S_B = Σ weight (팀 B)
total_ACS = Σ ACS (10명)
share_A = S_A^γ / (S_A^γ + S_B^γ)            # 팀 B는 대칭, γ = TEAM_STRENGTH_EXP
team_total_A = total_ACS × share_A

expected_ACS_i = team_total_A × (weight_i / S_A)   # 팀 B는 대칭
```

- **총합 보존**: Σ expected_ACS = total_ACS.
- **γ (TEAM_STRENGTH_EXP, 초기값 2.0)**: γ=1 이면 팀 항이 약분돼 로비 전체 정규화로
  붕괴(팀 분리 무효)한다. γ>1 이어야 팀 격차가 실제로 반영된다 — 강팀일수록 기대 ACS 상향
  (약팀 상대 farming 할인), 약팀은 하향(강팀 상대 수행 가산). Phase 3 재적합 대상.
- **이전 공식 폐기 이유(v1→v2)**: 기존 `lobby_avg × team_factor × (5·weight_i/S_own)` 는
  `team_factor = 2S_own/S` 와 `1/S_own` 이 약분돼 항상 `10·weight_i/S`(로비 전체 정규화)로
  붕괴, "팀 분리"가 수학적으로 무효였다.

### 4.4 TACR (경기별 퍼포먼스)

각 비율은 `[0.3, 2.0]`으로 캡. 분모 0 방지 스무딩 포함.

**스크린샷 데이터만 있을 때 (Phase 1):**
```
r_acs  = ACS / expected_ACS
r_kd   = ((K + 0.3·A) / max(D,1)) / (1.15 × rel_i × lobby_KD)
         rel_i = weight_i / lobby_mean_weight,  lobby_KD = ΣK / ΣD
r_econ = econ / (rel_i × lobby_avg_econ)
r_obj  = (FK + plants + defuses + 1) / (lobby_avg_obj + 1)

TACR = 100 × (0.50·r_acs + 0.30·r_kd + 0.10·r_econ + 0.10·r_obj)
```

**Phase 2 enrichment 필드가 있을 때 (kast, adr, first_deaths not null):**
```
r_kast = KAST / (0.60 + 0.02 × (tier_eff_i − lobby_mean_tier))   # 기대 KAST 근사, 캘리브레이션 대상
r_fkfd = 1 + (FK − FD) / max(rounds, 1) × 3                       # 라운드당 개폐 기여
r_adr  = ADR / (rel_i × lobby_avg_ADR)

TACR = 100 × (0.45·r_acs + 0.25·r_kast + 0.15·r_fkfd + 0.15·r_adr)
```

동일 경기 내에서는 반드시 하나의 공식만 사용 (10명 전원 enrichment가 있을 때만 두 번째 공식).

### 4.5 표시 점수 (0–1000)

```
display_score = 1000 / (1 + exp(−(TACR − 100) / 25))
```
TACR 100(티어 기대치 정확 수행) = 500점. DB에는 TACR과 display_score 모두 저장하되, 진실 원천은 TACR.

### 4.6 OpenSkill 업데이트

경기 저장 시:
- 미등록 μ/σ 초기화: `μ = tier_eff prior`, `σ = 랭크 신뢰도 역수` (현재랭크+판수충분: 1.5 / peak만: 2.5 / 지표없음: 4.0 — 초기값)
- 팀 승패로 표준 업데이트. `openskill` 패키지의 PlackettLuce 모델 사용
- 무승부(라운드 수 미입력) 시 스킵하고 로그

추가로 **implied tier EMA**를 병행 기록(검증용, tier_eff에는 미사용):
```
implied_tier = 관측 ACS 지분을 4.3 공식에 역대입해 풀어낸 tier
player_tiers에 source='implied'로 append
```

### 4.7 누적 리더보드

- 개인 평균 TACR에 empirical Bayes 수축: `adj = (n×mean_TACR + m×100) / (n+m)`, m=3
- 리더보드 정렬 기준: 수축 조정된 평균 display_score
- OpenSkill μ±σ도 병기 표시

---

## 5. 인제스트 파이프라인 (Phase 1 핵심)

### 5.0 스크린샷 수집 경로

스크린샷 수급은 운영자가 시스템 외부에서 직접 처리한다 (본인 캡처 또는 전달받음). 시스템 입장에서 진입 경로는 두 가지, 둘 다 동일한 검토 플로우로 이어진다:

1. **웹 업로드 폼** (`/upload`): 기본 경로. 다중 파일 업로드 지원 (여러 경기 한꺼번에)
2. **인박스 폴더** (`data/inbox/`): 파일을 폴더에 직접 복사해도 됨. 웹 첫 화면에 "처리 대기 N건" 배지를 띄우고, 클릭 시 해당 이미지로 검토 플로우 시작

처리 완료된 원본은 `data/screenshots/{match_id}.png`로 이동해 보관.

### 5.1 흐름

```
웹 업로드 폼 (이미지 + 선택입력: 맵, 스코어 13-x, 날짜)
 → extractor: Claude API vision 호출, JSON만 반환하도록 프롬프트
 → Pydantic 파싱 (실패 시 1회 재시도: 오류 메시지를 포함해 재요청)
 → validator: sanity check
 → matcher: 닉네임 → player 매칭 (자동 or 검토 필요 플래그)
 → 검토 화면: 추출 결과 테이블을 보여주고 수정/확정
 → 확정 시 DB 저장 → 레이팅 계산 → 경기 상세 페이지로 리다이렉트
```

**검토 화면은 필수 단계다.** VLM 추출을 무검토로 자동 저장하지 않는다. 확정 전까지 matches에 `status='pending'` 같은 상태가 필요하면 컬럼 추가.

### 5.2 추출 프롬프트 요구사항 (extractor.py)

- system: "발로란트 스코어보드 이미지에서 데이터를 추출한다. 반드시 아래 JSON 스키마로만 응답. 마크다운 코드펜스 금지."
- 요구 필드(행당): `nickname`(원문 그대로, 한자/특수문자 보존), `agent_kr`(닉네임 아래 작은 텍스트), `team_color`(초록/빨강/노랑 행 배경 — 노랑은 스크린샷 촬영자 본인이며 좌측 테두리 색으로 실제 팀 판별), `acs, kills, deaths, assists, econ, first_kills, plants, defuses`
- 이미지 base64 인코딩, media_type 자동 감지 (png/jpeg)
- temperature 0

### 5.3 Sanity check (validator.py)

전부 통과 못 하면 검토 화면에 경고 배지 표시 (저장 차단은 아님, 사용자가 수정 후 확정):
- 행 정확히 10개
- |ΣK − ΣD| ≤ 3
- ACS ∈ [0, 500], K/D/A ∈ [0, 60]
- 팀 구성 5:5
- agent_kr이 유효 요원 목록에 존재
- 닉네임 중복 없음

### 5.4 닉네임 매칭 (matcher.py)

- `player_aliases.alias` 정확 일치 → 자동 매칭
- 불일치 시: 기존 선수 목록과의 유사도(normalized similarity, `rapidfuzz`) 상위 3개 후보 + "신규 선수 생성" 옵션을 검토 화면에 표시
- 확정 시 새 alias를 player_aliases에 저장 → 다음부터 자동

---

## 6. 웹 페이지 (Phase 1)

서버 렌더링(Jinja2) + Chart.js. 디자인은 심플하게, 다크 테마.

| 경로 | 내용 |
|---|---|
| `/` | 최근 경기 목록 + 리더보드 요약 |
| `/upload` | 스크린샷 업로드 폼 |
| `/review/{match_id}` | 추출 결과 검토/수정/확정 |
| `/match/{id}` | 경기 상세: 원시 스탯 테이블 + expected_ACS 대비 막대 + TACR/표시점수 정렬 |
| `/player/{id}` | 선수 프로필: 경기별 display_score 추이 차트, 요원별 성과, μ±σ, tier_eff 이력 |
| `/leaderboard` | 수축 조정 평균 점수 랭킹 (최소 경기 수 필터) |
| `/players` | 선수 관리: 티어 수동 입력/수정 (source='manual'), 별칭 관리 |

JSON API도 같은 데이터로 제공 (`/api/...`) — 추후 위젯/봇에서 재사용.

---

## 7. Phase 2 — HenrikDev 연동 (선택 확장)

목적: PUUID 확보, 랭크 자동 조회, KAST/ADR/FD enrichment. **Phase 1은 이것 없이 완결 동작해야 한다.**

- `henrik/client.py`: rate limit 대응(free tier 기준 백오프), 엔드포인트 — account(name#tag→puuid), MMR(현재/최고 랭크), 매치 상세
- 선수 관리 페이지에서 name#tag 입력 → puuid/랭크 자동 채움
- 경기 업로드 시 external_match_id를 알면 매치 상세로 enrichment 필드 채움
- **구현 첫 단계에서 검증할 것 (코드 주석에 TODO로 명시):**
  - 커스텀 게임이 매치 히스토리 응답에 나오는지, 필터 방법 (`mode` 필드)
  - v4 매치 응답의 정확한 필드 경로 (kast/adr 등이 어디에 있는지)
  - free tier의 과거 매치 조회 한도

## 8. Phase 3 — 캘리브레이션 (경기 20+ 누적 후)

`calibration/calibrate.py`, CLI 스크립트로 실행. 결과는 리포트 출력 + 새 파라미터 제안(자동 적용 금지):

1. **K_TIER**: `log(ACS_i / lobby_avg_ACS)` ~ `(tier_eff_i − lobby_mean_tier)` 선형회귀 기울기
2. **ROLE_COEF**: 역할별 mean(r_acs)의 역수로 정규화 재산출
3. **TACR 가중치**: 성분별 팀 합 차이 → 승패 로지스틱 회귀 계수
4. 검증 리포트: "TACR 합 우세 팀의 실제 승률" (목표 ≥ 70%)

새 파라미터 적용 시 `PARAMS_VERSION` 증가 + 전 경기 match_ratings 재계산 커맨드 제공 (`python -m app.calibration.recompute`).

---

## 9. 실행/배포

- 로컬 개발: PyCharm에서 venv + `uvicorn app.main:app --reload`
- 배포: `docker compose up -d` — 단일 서비스, `./data` 볼륨 마운트 (DB·스크린샷 영속)
- Alembic 마이그레이션은 컨테이너 기동 시 자동 실행

## 10. 테스트 (pytest)

- rating 모듈: 아래 고정 픽스처로 회귀 테스트 — 팀 밸런스가 맞는 경기에서 팀분리 공식이 로비 전체 정규화와 근사 일치하는지, 캡 동작, 시그모이드 경계값(TACR 100→500)
- validator: 각 sanity rule 위반 케이스
- matcher: 정확 일치 / 유사 후보 / 신규
- 픽스처 (실제 경기, 티어는 불멸=22/초월=19/레디=24/플래=13):

```
team,nick,agent,tier_eff,acs,k,d,a,econ,fk,plant,defuse
A,Perik,레이나,22,319,19,14,4,79,4,0,0
B,이구로,제트,19,259,13,15,3,66,9,0,0
B,황석영,스카이,22,257,18,8,3,83,1,7,1
B,横幅,레이나,24,244,16,12,5,57,0,0,0
B,아가라구요,클로브,22,229,17,9,6,63,0,1,0
A,죄송한데죽빵한대만갈겨도될까요,페이드,22,201,13,13,2,44,0,1,0
A,미움받을 용기,사이퍼,22,156,9,14,6,51,1,0,0
B,마리골드,세이지,13,146,9,13,10,36,1,0,0
A,Étoile,오멘,13,143,8,15,7,34,0,1,0
A,겁먹지않아,레이즈,22,141,6,17,3,42,2,0,0
```

## 11. 구현 순서와 완료 기준

**Phase 1a — 코어**: 스키마/마이그레이션 → rating 모듈(테스트 포함) → config 파라미터화
DoD: 픽스처로 TACR 계산 테스트 통과

**Phase 1b — 인제스트**: extractor → validator → matcher → 업로드/검토 플로우
DoD: 실제 스크린샷 1장을 업로드→검토→확정→경기 상세 페이지까지 관통

**Phase 1c — 웹 뷰**: 나머지 페이지 + 차트
DoD: 3경기 입력 후 리더보드/프로필이 의미 있게 표시

**Phase 2 — Henrik**: client + 검증 스크립트 + enrichment
**Phase 3 — 캘리브레이션**

## 12. 코드 컨벤션

- 타입 힌트 필수, 계산 로직은 순수 함수로 (DB 의존 분리)
- 모든 튜닝 파라미터는 config.py에만 존재, 매직 넘버 금지
- 추출 원본(JSON)과 스크린샷은 삭제하지 않고 보관 (재처리 가능성)
