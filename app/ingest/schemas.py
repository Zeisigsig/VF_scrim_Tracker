"""VLM 추출 결과 Pydantic 스키마 (스펙 §5.2)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ExtractedRow(BaseModel):
    """스코어보드 한 행 (원시 추출값)."""
    nickname: str = Field(description="원문 그대로. 한자/특수문자 보존")
    agent_kr: str = Field(description="닉네임 아래 작은 텍스트 (한글 요원명)")
    team_color: str = Field(description="초록/빨강/노랑 행 배경색")
    team: str = Field(
        description="실제 소속 팀 'A'|'B'. 초록=A, 빨강=B, 노랑(촬영자)은 좌측 테두리 색으로 판별"
    )
    acs: int
    kills: int
    deaths: int
    assists: int
    econ: int | None = None
    first_kills: int | None = None
    plants: int | None = None
    defuses: int | None = None


class Correction(BaseModel):
    """Henrik 보정으로 바뀐 한 값 (검토 화면 배너 표시용)."""
    nickname: str  # 대상 행(보정 후 닉)
    field: str     # 'ACS' | 'K' | 'D' | 'A' | '요원' | '닉'
    old: str
    new: str


class ExtractionResult(BaseModel):
    """VLM 이 반환해야 하는 최상위 JSON."""
    rows: list[ExtractedRow]
    map_name: str | None = None
    team_a_rounds: int | None = None
    team_b_rounds: int | None = None
    # Henrik enrichment (선택). 업로드 시 자동 보정 결과를 함께 보관.
    henrik_match_id: str | None = None
    corrections: list[Correction] = Field(default_factory=list)
