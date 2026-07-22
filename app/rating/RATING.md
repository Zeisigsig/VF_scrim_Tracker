# 점수 계산 설명 (표시 점수 · TACR · OpenSkill)

이 문서는 리더보드에 나오는 세 가지 값 — **표시 점수(0~1000)**, **TACR(티어 보정 퍼포먼스)**,
**OpenSkill(μ ± σ)** — 이 실제로 어떻게 계산되는지 수식과 함께 설명합니다.

- 계산 코드는 모두 이 폴더(`app/rating/`)에 있는 **순수 함수**입니다(DB 의존성 없음).
  - `tier.py` — 티어 수치화 및 유효 티어(`tier_eff`)
  - `tacr.py` — 기대 ACS · TACR · 표시 점수
  - `openskill_engine.py` — OpenSkill μ/σ 업데이트
  - `leaderboard.py` — 리더보드 수축(shrinkage)
- 모든 상수는 `app/config.py`에 있습니다(코드에 매직 넘버 금지). 아래 수식의 숫자값은
  **`PARAMS_VERSION = "v2-team-separation"` 기준의 초기 가정값**이며, Phase 3 캘리브레이션에서
  데이터로 재적합할 대상입니다. 값이 바뀌면 `config.py`가 최종 기준입니다.

전체 파이프라인 요약:

```
티어 → tier_eff → 기대 ACS → TACR → 표시 점수
                                  └→ (승패) → OpenSkill μ/σ → 다음 경기의 tier_eff
리더보드 = 여러 경기 TACR 평균에 Empirical Bayes 수축 적용 → 표시 점수로 환산
```

---

## 0. 티어 수치화와 유효 티어 `tier_eff`

모든 보정의 기준이 되는 값입니다. 티어를 하나의 실수로 바꾼 뒤(`tier_to_value`),
경기마다 그 선수의 **유효 티어 `tier_eff`**를 정합니다(`effective_tier`).

### 0.1 티어 → 수치 (`tier.py: tier_to_value`)

Iron 1 = 0, 디비전당 +1로 매깁니다. 디비전 미상이면 티어 중간값을 씁니다.

| 티어 | (하한, 중간, 상한) | 티어 | (하한, 중간, 상한) |
|---|---|---|---|
| 아이언 | (0, 1, 2) | 다이아 | (15, 16, 17) |
| 브론즈 | (3, 4, 5) | 초월 | (18, 19, 20) |
| 실버 | (6, 7, 8) | 불멸 | (21, 22, 23) |
| 골드 | (9, 10, 11) | 레디언트 | (24, 24, 24) |
| 플래티넘 | (12, 13, 14) | | |

### 0.2 유효 티어 `tier_eff` (`tier.py: effective_tier`)

우선순위:

1. OpenSkill μ가 있고 내전 판수 `games_counted ≥ 3`(`OPENSKILL_MIN_GAMES`) → **`tier_eff = μ`**
2. 아니면 랭크 기반 prior(`rank_based_tier`):

현재 랭크 `current`(판수 `n`), 최고 랭크 `peak`가 있을 때:

```
c            = n / (n + 10)                         # RANK_CONFIDENCE_DENOM = 10
peak_decayed = peak - 0.5 * (peak 이후 경과 액트 수)  # PEAK_DECAY_PER_ACT = 0.5, 하한 = current
tier_eff     = c * current + (1 - c) * peak_decayed
```

- 현재 랭크만 있으면 `peak_decayed = current`.
- peak만 있으면 `tier_eff = peak_decayed`.
- 아무 지표도 없으면(언랭) 그 경기 로비의 `tier_eff` **중앙값**(없으면 실버 중간값 7).

이 과정에서 나온 **신뢰도 라벨**(`ranked_confident` / `peak_only` / `unranked`)은
OpenSkill 초기 σ를 고르는 데 쓰입니다(§3).

---

## 1. 기대 ACS (Expected ACS)

"이 티어·역할·팀 구성이면 ACS가 이 정도는 나온다"는 **기준선**입니다. TACR의 핵심 분모예요.
(`tacr.py: weight`, `expected_acs`)

### 1.1 선수 가중치 (`weight`)

```
weight_i = exp(K_TIER * tier_eff_i) * ROLE_COEF[role_i]
```

- `K_TIER = 0.06` — 티어가 높을수록 기대치를 지수적으로 키웁니다.
- 역할 계수 `ROLE_COEF`: 듀얼리스트 1.10, 이니시에이터 1.00, 컨트롤러 0.92, 센티넬 0.90
  (딜량이 많은 역할일수록 기대 ACS ↑).

### 1.2 팀 분리 기대 ACS (`expected_acs`)

로비 총 ACS를 **팀 강도 비로 두 팀에 배분**한 뒤, 각 팀 내부에서 가중치 지분으로 분배합니다.

기호: 팀 A 가중치 합 `S_A = Σ_{i∈A} weight_i`, 팀 B 합 `S_B`,
로비 총 ACS `T = Σ ACS_i`, 팀 강도 지수 `γ = TEAM_STRENGTH_EXP = 2.0`.

```
share_A      = S_A^γ / (S_A^γ + S_B^γ)     # 팀 A가 로비 총 ACS에서 차지할 몫
team_total_A = T * share_A                 # 팀 A에 배분된 기대 ACS 총량
expected_ACS_i = team_total_A * (weight_i / S_A)   # 팀 A 선수. 팀 B는 대칭.
```

- **총합 보존**: `Σ expected_ACS = T` (팀별 배분 합 = T, 팀 내 지분 합 = 1).
- **γ의 역할**: γ=1이면 `team_total_A ∝ S_A`와 팀 내 `1/S_A`가 **약분**돼
  `expected_ACS_i = T·weight_i/S`(로비 전체 정규화)로 붕괴 → 팀 구성이 무의미해집니다.
  그래서 **γ>1**을 씁니다. γ>1이면 강한 팀일수록 기대 ACS가 더 높아져(약팀 상대 farming 할인),
  약팀은 낮아집니다(강팀 상대 수행에 가산). 예: 초월5 vs 실버5 로비(전원 ACS 200)에서
  γ=2면 강팀 기대 ≈323, 약팀 ≈77로 벌어집니다(γ=1이면 269 vs 131, 팀 효과 없음).

> **버전 주의**: 이전 `v1`은 `lobby_avg × team_factor × (5·weight_i/S_own)` 형태였는데,
> `team_factor = 2S_own/S`와 `1/S_own`이 약분돼 팀 분리가 수학적으로 무효였습니다.
> `v2-team-separation`에서 위 γ 모델로 교체했습니다.

---

## 2. TACR (티어 보정 퍼포먼스)

한 경기에서 그 선수가 **자기 티어 기대치 대비** 얼마나 잘했는지를 100 기준으로 매긴 값입니다.
**TACR = 100**이면 "기대치를 정확히 수행"(표시 점수 500점)을 뜻합니다. (`tacr.py: compute_match`)

여러 지표의 **비율(관측/기대)**을 구해 가중 합산합니다. 각 비율은 극단값을 막기 위해
`RATIO_CAP = (0.3, 2.0)`으로 클램프합니다.

한 경기 안에서는 **하나의 공식만** 사용합니다: 10명 전원이 enrichment 필드
(KAST·ADR·first_deaths)를 가지면 Phase 2, 아니면 Phase 1.

공통 사전값:

```
rel_i         = weight_i / mean(weights)     # 로비 평균 대비 이 선수의 상대 가중치
r_acs_i       = clamp( ACS_i / expected_ACS_i )
```

### 2.1 Phase 1 — 스크린샷만 있을 때 (기본)

```
kd_num  = (K_i + 0.30 * A_i) / max(D_i, 1)          # KD_ASSIST_WEIGHT = 0.30
kd_den  = 1.15 * rel_i * lobby_KD                    # KD_LOBBY_FACTOR = 1.15
r_kd    = clamp( kd_num / kd_den )                   # lobby_KD = ΣK / max(ΣD,1)

r_econ  = clamp( econ_i / (rel_i * mean(econ)) )
r_obj   = clamp( (FK_i + plants_i + defuses_i + 1) / (mean(FK+plants+defuses) + 1) )

TACR = 100 * ( 0.50*r_acs + 0.30*r_kd + 0.10*r_econ + 0.10*r_obj )
```

가중치 `TACR_WEIGHTS_P1 = {acs:0.50, kd:0.30, econ:0.10, obj:0.10}`.

> 참고: 첫킬(FK)·설치(plants)·해제(defuses)는 OCR이 불안정해 값이 없으면 0으로 들어가며,
> `r_obj`의 +1 스무딩이 그 영향을 완화합니다.

### 2.2 Phase 2 — enrichment 필드가 모두 있을 때

```
expected_KAST = 0.60 + 0.02 * (tier_eff_i - mean(tier_eff))   # KAST_BASE, KAST_TIER_SLOPE
r_kast = clamp( KAST_i / expected_KAST )
r_fkfd = clamp( 1 + (FK_i - FD_i) / max(rounds,1) * 3.0 )      # FKFD_ROUND_GAIN = 3.0
r_adr  = clamp( ADR_i / (rel_i * mean(ADR)) )

TACR = 100 * ( 0.45*r_acs + 0.25*r_kast + 0.15*r_fkfd + 0.15*r_adr )
```

가중치 `TACR_WEIGHTS_P2 = {acs:0.45, kast:0.25, fkfd:0.15, adr:0.15}`.
(현재 파이프라인은 Phase 2 enrichment가 스켈레톤이라 대부분 Phase 1으로 계산됩니다.)

---

## 3. 표시 점수 (Display Score, 0~1000)

TACR를 **일반인이 읽기 쉬운 0~1000 척도**로 바꾼 값입니다. 시그모이드라 양 끝이 부드럽게
포화합니다. (`tacr.py: display_score`)

```
display_score = 1000 / ( 1 + exp( -(TACR - 100) / 25 ) )
```

- `DISPLAY_MIDPOINT = 100` → **TACR 100 = 정확히 500점**.
- `DISPLAY_SCALE = 25` → TACR가 25 오를 때마다 시그모이드 중심 부근에서 가파르게 변합니다.

감각을 위한 대략값:

| TACR | 50 | 75 | 100 | 125 | 150 |
|---|---|---|---|---|---|
| 표시 점수 | ≈119 | ≈269 | 500 | ≈731 | ≈881 |

---

## 4. OpenSkill (μ ± σ)

한 경기 퍼포먼스(TACR)와 달리, **여러 경기의 승패를 누적한 장기 실력 레이팅**입니다
(체스 레이팅과 비슷). `openskill` 패키지의 **PlackettLuce** 모델을 씁니다.
(`openskill_engine.py`)

- **μ (mu)**: 추정 실력. **σ (sigma)**: 불확실성. 경기를 할수록 σ가 줄어 값이 안정됩니다.
- μ는 §0.2에 따라 판수 3 이상이면 다음 경기의 `tier_eff`로 되먹임됩니다(피드백 루프).

### 4.1 초기화 (`initial_state`)

```
μ_0 = tier prior (§0.2의 랭크 기반 tier_eff)
σ_0 = SIGMA_INIT[신뢰도 라벨]
```

초기 σ 표(`SIGMA_INIT`): `ranked_confident = 1.5`, `peak_only = 2.5`, `unranked = 4.0`.
랭크 지표가 확실할수록 처음부터 불확실성이 작습니다.

### 4.2 경기 후 업데이트 (`update_match`)

팀 A/B의 현재 (μ, σ)와 승패를 넣으면 PlackettLuce가 전원의 (μ, σ)를 갱신합니다.
낮은 rank 값이 승리(`ranks = [0,1]` = A 승). **무승부는 호출부에서 스킵**합니다.

> TACR/표시 점수는 개인 스탯 기반이고, OpenSkill은 팀 승패 기반이라 **서로 독립적인 축**입니다.
> 한 경기 잘했어도 지면 μ는 내려갈 수 있고, 그 반대도 가능합니다.

---

## 5. 리더보드 순위 (Empirical Bayes 수축)

리더보드 정렬 기준은 개별 경기 표시 점수의 단순 평균이 아니라, **판수가 적은 사람을
평균 쪽으로 끌어당긴(shrink)** 조정 점수입니다. 1~2판만 하고 운 좋게 높은 사람이
상위를 차지하는 것을 막습니다. (`leaderboard.py`)

선수의 경기 수 `n`, 평균 TACR `mean_tacr`일 때:

```
adj_TACR   = (n * mean_tacr + m * 100) / (n + m)      # EB_SHRINK_M = 3, EB_PRIOR_TACR = 100
adj_score  = display_score(adj_TACR)                  # §3 시그모이드로 환산
```

- 사전 평균은 TACR 100(= "티어 기대치 정확 수행")이고, 가상의 `m = 3`판을 섞는 효과입니다.
- `n`이 커질수록 `adj_TACR → mean_tacr`로 수렴해 수축 효과가 사라집니다.
- 리더보드는 이 `adj_score`로 내림차순 정렬합니다.

예) 실제 평균 TACR 140을 기록했을 때 판수별 조정:

| n (판수) | adj_TACR | adj_score |
|---|---|---|
| 1 | (140 + 300)/4 = 110 | ≈599 |
| 3 | (420 + 300)/6 = 120 | ≈690 |
| 10 | (1400 + 300)/13 ≈ 131 | ≈774 |
| 30 | (4200 + 300)/33 ≈ 136 | ≈811 |

---

## 부록: 값을 바꾸고 싶다면

- 모든 상수는 `app/config.py`에 모여 있습니다. 수정하면 반드시 `PARAMS_VERSION`을 올리세요.
- 이 폴더의 함수들은 DB에 의존하지 않으므로, 파라미터를 바꾸며 단위 테스트로 바로 검증할 수 있습니다.
- 캘리브레이션(Phase 3) CLI로 실제 데이터에 맞춰 재적합하는 흐름이 설계되어 있습니다.
