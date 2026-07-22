# 발로란트 내전 퍼포먼스 트래커

내전(커스텀 게임) 스코어보드 스크린샷을 업로드하면 VLM(Claude API)으로 스탯을 추출하고,
**티어 격차를 보정한 개인 퍼포먼스 점수(TACR)**를 계산해 웹 대시보드로 보여준다.

설계 문서와 개발 기록은 [`design/`](design/) 폴더에 있다.

## 로컬 개발 (PyCharm / venv)

```bash
pip install -r requirements.txt
cp .env.example .env        # ANTHROPIC_API_KEY 등 채우기
alembic upgrade head        # 스키마 생성 (또는 첫 실행 시 자동 생성)
uvicorn app.main:app --reload
```

`.env`:

```
ANTHROPIC_API_KEY=...
HENRIK_API_KEY=...          # Phase 2 (선택)
DB_PATH=data/scrim.db
EXTRACTION_MODEL=claude-sonnet-4-6
```

## 배포 (Docker)

```bash
docker compose up -d        # ./data 볼륨에 DB·스크린샷 영속
```

## 사용 흐름

스크린샷 진입 경로는 두 가지, 둘 다 같은 검토 플로우로 이어진다 (스펙 §5.0):

- **웹 업로드** `/upload` — 스코어보드 스크린샷 + 맵/스코어(선택). **여러 장 동시 업로드** 지원(여러 경기 일괄).
- **인박스 폴더** `data/inbox/` — 이미지를 폴더에 직접 복사. 홈에 "처리 대기 N건" 배지가 뜨고, 클릭하면 검토 대기 경기로 전환.

원본은 처리 시 `data/screenshots/{match_id}.png` 로 이동해 보관한다.

1. 업로드 또는 인박스 처리 → 검토 대기(pending) 경기 생성 (홈의 "검토 대기" 목록)
2. `/review/{id}` — 추출 결과 검토·수정, 닉네임→선수 매칭 확정 (**무검토 저장 없음**)
3. 확정 → 레이팅 계산 → `/match/{id}` 경기 상세로 이동
4. `/leaderboard`, `/player/{id}` 에서 누적 성과 확인
5. `/players` — 티어 수동 입력, 별칭 관리

JSON API: `/api/matches`, `/api/match/{id}`, `/api/leaderboard`

## 테스트

```bash
pytest app/tests
```

- `test_rating.py` — 기대 ACS 총합 불변식, 시그모이드 경계(TACR 100→500), 캡, 두 공식 경로
- `test_ingest.py` — validator sanity rule, matcher 정확/유사 매칭

## 파라미터 튜닝 (Phase 3)

초기 계산 파라미터는 **가정값**이며 `app/config.py` 한 곳에 모여 있다.
경기 20+ 누적 후 캘리브레이션:

```bash
python -m app.calibration.calibrate     # 재적합 제안 리포트 (자동 적용 안 함)
# config.py 수정 + PARAMS_VERSION 증가 후:
python -m app.calibration.recompute      # 전 경기 재계산
```

## 구현 상태

- **Phase 1a/1b/1c** (코어·인제스트·웹): 구현 완료
- **Phase 2** (HenrikDev enrichment): 클라이언트 골격 + 검증 TODO 포함
- **Phase 3** (캘리브레이션): 리포트/재계산 CLI 구현
