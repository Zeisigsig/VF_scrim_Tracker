"""설정 및 튜닝 파라미터.

스펙 §4/§12: 모든 튜닝 파라미터는 이 파일에만 존재한다 (매직 넘버 금지).
아래 수치 파라미터의 초기값은 **가정값이며 Phase 3 캘리브레이션에서 데이터로
재적합할 대상**이다. 값을 바꿀 때는 PARAMS_VERSION 을 함께 올린다.
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv 미설치 환경(예: 순수 stdlib 테스트)에서도 임포트 가능하게
    pass

# --- 경로 ---------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = os.getenv("DB_PATH", "data/scrim.db")
DB_URL = f"sqlite:///{(BASE_DIR / DB_PATH).as_posix()}"
SCREENSHOT_DIR = BASE_DIR / "data" / "screenshots"
# 인박스: 파일을 직접 복사해 넣는 대체 수집 경로 (스펙 §5.0)
INBOX_DIR = BASE_DIR / "data" / "inbox"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

# --- 인증/계정 ----------------------------------------------------------
# 세션 쿠키 서명 키. 배포 시 반드시 .env 에서 무작위 값으로 지정.
SECRET_KEY = os.getenv("SECRET_KEY", "dev-insecure-change-me")
# 어드민 사전발급 계정의 공통 초기 비밀번호. 첫 로그인 시 강제 변경.
DEFAULT_PASSWORD = os.getenv("DEFAULT_PASSWORD", "valorant")
PBKDF2_ITERATIONS = 200_000


def admin_usernames() -> set[str]:
    """어드민 권한을 가진 디스코드 닉 집합(ADMIN_USERNAMES, 콤마 구분). env 가 권한의 유일 출처."""
    raw = os.getenv("ADMIN_USERNAMES", "")
    return {n.strip() for n in raw.split(",") if n.strip()}


# --- 외부 API -----------------------------------------------------------
# Phase 2 전적 보강용(app/henrik/client.py). 현재 파이프라인에 미연결.
HENRIK_API_KEY = os.getenv("HENRIK_API_KEY", "")

# --- 배포(로컬 OCR → 클라우드 웹) --------------------------------------
# 로컬 push.py 와 클라우드 /api/ingest 가 공유하는 인증 시크릿. 미설정 시 API 비활성.
INGEST_API_KEY = os.getenv("INGEST_API_KEY", "")
# 로컬 push.py 가 추출 결과를 보낼 클라우드 주소(예: https://xxx.ts.net). 로컬에서만 사용.
CLOUD_BASE_URL = os.getenv("CLOUD_BASE_URL", "")
# 로컬(OCR 가능 환경)에서만 1. 클라우드는 0 → /upload·/inbox 비활성(cv2/onnx 없음).
ENABLE_LOCAL_UPLOAD = os.getenv("ENABLE_LOCAL_UPLOAD", "0") == "1"

# --- 파라미터 버전 ------------------------------------------------------
# 계산에 쓰인 파라미터 세트 식별자. 값 변경 시 반드시 증가시킨다.
PARAMS_VERSION = "v2-team-separation"

# --- 4.1 티어 수치화 ----------------------------------------------------
# Iron 1 = 0, 디비전당 +1. 디비전 미상이면 티어 중간값.
# (한글 티어명 → (하한, 중간값, 상한))
TIER_TABLE: dict[str, tuple[int, int, int]] = {
    "아이언": (0, 1, 2),
    "브론즈": (3, 4, 5),
    "실버": (6, 7, 8),
    "골드": (9, 10, 11),
    "플래티넘": (12, 13, 14),
    "다이아": (15, 16, 17),
    "초월": (18, 19, 20),
    "불멸": (21, 22, 23),
    "레디언트": (24, 24, 24),
}
# 영문 별칭 (Henrik 응답 등에서 유입될 수 있음)
TIER_ALIASES: dict[str, str] = {
    "iron": "아이언",
    "bronze": "브론즈",
    "silver": "실버",
    "gold": "골드",
    "platinum": "플래티넘",
    "plat": "플래티넘",
    "diamond": "다이아",
    "dia": "다이아",
    "ascendant": "초월",
    "immortal": "불멸",
    "radiant": "레디언트",
}

def tier_name(value: float) -> str:
    """티어 수치값 → 티어명. 밴드를 포함하는 이름, 없으면 가장 가까운 밴드."""
    for name, (lo, _mid, hi) in TIER_TABLE.items():
        if lo <= value <= hi:
            return name
    return min(TIER_TABLE.items(), key=lambda kv: abs(kv[1][1] - value))[0]


# --- 4.2 유효 티어 (tier_eff) ------------------------------------------
OPENSKILL_MIN_GAMES = 3  # μ 를 tier_eff 로 신뢰하기 위한 최소 내전 판수
RANK_CONFIDENCE_DENOM = 10  # c = n / (n + 10)
PEAK_DECAY_PER_ACT = 0.5  # peak_decayed = peak - 0.5 * 경과 액트 수

# --- 4.3 기대 ACS -------------------------------------------------------
K_TIER = 0.06  # 초기 가정값 (Phase 3 재적합 대상)
ROLE_COEF: dict[str, float] = {  # 초기 가정값
    "duelist": 1.10,
    "initiator": 1.00,
    "controller": 0.92,
    "sentinel": 0.90,
}
TEAM_SIZE = 5
# 팀 강도 지수 γ. 로비 총 ACS 를 팀 강도 비 S^γ 로 두 팀에 배분한다.
# γ=1 이면 팀 항이 약분되어 로비 전체 정규화로 붕괴(팀 분리 무효)하므로,
# 팀 격차를 실제로 반영하려면 γ>1. (강팀 기대치↑ → 약팀 상대 farming 할인,
# 강팀 상대 수행 가산.) 초기 가정값이며 Phase 3 재적합 대상.
TEAM_STRENGTH_EXP = 2.0

# --- 4.4 TACR ----------------------------------------------------------
RATIO_CAP = (0.3, 2.0)  # 각 비율 클램프 범위
KD_ASSIST_WEIGHT = 0.3  # (K + 0.3A)
KD_LOBBY_FACTOR = 1.15  # 분모 1.15 * rel_i * lobby_KD

# Phase 1 (스크린샷만) TACR 가중치
TACR_WEIGHTS_P1 = {"acs": 0.50, "kd": 0.30, "econ": 0.10, "obj": 0.10}
# Phase 2 (enrichment 필드 존재) TACR 가중치
TACR_WEIGHTS_P2 = {"acs": 0.45, "kast": 0.25, "fkfd": 0.15, "adr": 0.15}
# 기대 KAST 근사: 0.60 + 0.02*(tier_eff_i - lobby_mean_tier) — 캘리브레이션 대상
KAST_BASE = 0.60
KAST_TIER_SLOPE = 0.02
FKFD_ROUND_GAIN = 3.0  # r_fkfd = 1 + (FK-FD)/rounds * 3

# --- 4.5 표시 점수 ------------------------------------------------------
DISPLAY_MIDPOINT = 100.0  # TACR 이 이 값일 때 500점
DISPLAY_SCALE = 25.0  # 시그모이드 스케일

# --- 4.6 OpenSkill 초기 σ ----------------------------------------------
# 랭크 신뢰도 역수 (초기 가정값)
SIGMA_INIT = {
    "ranked_confident": 1.5,  # 현재 랭크 + 판수 충분
    "peak_only": 2.5,  # 최고 랭크만
    "unranked": 4.0,  # 지표 없음
}

# --- 4.7 리더보드 -------------------------------------------------------
EB_SHRINK_M = 3  # empirical Bayes 수축 상수 m
EB_PRIOR_TACR = 100.0  # 사전 평균 (티어 기대치 정확 수행)

# --- 요원 → 역할 매핑 (스펙 §3) ----------------------------------------
AGENT_ROLE: dict[str, str] = {
    # duelist
    "제트": "duelist", "레이나": "duelist", "레이즈": "duelist",
    "피닉스": "duelist", "네온": "duelist", "요루": "duelist", "아이소": "duelist",
    "웨이레이": "duelist",
    # initiator
    "소바": "initiator", "스카이": "initiator", "브리치": "initiator",
    "케이오": "initiator", "페이드": "initiator", "게코": "initiator",
    "테호": "initiator",
    # controller
    "오멘": "controller", "브림스톤": "controller", "바이퍼": "controller",
    "아스트라": "controller", "하버": "controller", "클로브": "controller",
    "믹스": "controller",
    # sentinel
    "사이퍼": "sentinel", "킬조이": "sentinel", "세이지": "sentinel",
    "체임버": "sentinel", "데드록": "sentinel", "바이스": "sentinel",
    "비토": "sentinel",
}


# --- 닉네임 매칭 -------------------------------------------------------
# OCR 닉네임과 기존 유저명의 유사도가 이 값 이상이면 검토에서 그 유저를 기본 선택
# (OCR 오탈자 "따따그르릉" ↔ 기존 "딱따그르릉" 같은 케이스 자동 제안). 사람이 바꿀 수 있음.
NICKNAME_AUTOMATCH_MIN = 80.0


def valid_agents() -> set[str]:
    return set(AGENT_ROLE.keys())


# Henrik 리전 (이 커뮤니티는 전원 KR).
HENRIK_REGION = "kr"

# Henrik 응답의 영문 요원명 → OCR/DB 한글 요원명. 미등록 요원은 보정에서 제외.
AGENT_EN_TO_KR: dict[str, str] = {
    "jett": "제트", "raze": "레이즈", "reyna": "레이나", "phoenix": "피닉스",
    "yoru": "요루", "neon": "네온", "iso": "아이소", "waylay": "웨이레이",
    "sova": "소바", "breach": "브리치", "skye": "스카이", "kay/o": "케이오",
    "fade": "페이드", "gekko": "게코", "tejo": "테호",
    "brimstone": "브림스톤", "omen": "오멘", "viper": "바이퍼", "astra": "아스트라",
    "harbor": "하버", "clove": "클로브",
    "killjoy": "킬조이", "cypher": "사이퍼", "sage": "세이지", "chamber": "체임버",
    "deadlock": "데드록", "vyse": "바이스", "veto": "비토",
}


def agent_kr_from_en(name_en: str | None) -> str | None:
    """영문 요원명을 한글로. 매핑 없으면 None(보정 시 OCR 값 유지)."""
    if not name_en:
        return None
    return AGENT_EN_TO_KR.get(name_en.strip().lower())
