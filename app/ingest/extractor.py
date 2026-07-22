"""스크린샷 → 로컬 OCR(RapidOCR) → 구조화 JSON 추출 (스펙 §5.2).

Claude vision 대신 로컬 RapidOCR 로 스코어보드를 읽는다.

**2-패스 OCR** (기본 모델 하나로는 숫자/한글을 동시에 못 읽기 때문):
- 패스 A(기본 ch_PP-OCRv4): 숫자 열(ACS, K/D/A, 효율)을 정확히 읽는다. 행 앵커도 여기서.
- 패스 B(korean rec): 닉네임·요원(요원명은 닉네임 바로 아래 한글 텍스트)을 읽는다.
  한글 모델은 KDA의 '/'를 '}'로 깨뜨리는 등 숫자엔 약하므로 텍스트에만 쓴다.

한글 OCR은 완벽하지 않지만(예: '고운구등어'→'고윤구등어') 문제되지 않는다:
- 닉네임은 원문 그대로 두면 별칭 시스템이 자기교정형 — 한 번 사람이 확정하면
  다음 경기부터 같은 OCR 텍스트로 자동 매칭된다([[matcher]]).
- 요원은 config.valid_agents() 에 fuzzy 매칭해 정식 명칭으로 채운다.
- 프리미어 팀 태그("주모|aziin")는 접두어라 '|' 뒤만 취해 제거한다.
- 팀은 행 배경색(초록=A / 빨강=B)으로 판별한다.

인터페이스는 기존과 동일: extract_scoreboard(path) -> ExtractionResult.
해상도가 제각각이므로 픽셀 좌표를 하드코딩하지 않고, OCR 박스의 상대 위치로 열을 매핑한다.
"""
from __future__ import annotations

import re
from pathlib import Path

from rapidfuzz import fuzz, process

from app.config import valid_agents
from app.ingest.schemas import ExtractedRow, ExtractionResult

_KDA_RE = re.compile(r"^(\d+)/(\d+)/(\d+)$")
_INT_RE = re.compile(r"^\d+$")
_HANGUL_RE = re.compile(r"[가-힣]")
_LETTER_RE = re.compile(r"[A-Za-z가-힣]")

# 한글 인식 모델(사전은 onnx 메타데이터에 내장). 프로젝트 루트/models/ 에 위치.
_KOREAN_REC_MODEL = Path(__file__).resolve().parents[2] / "models" / "korean_rec.onnx"

_ocr = None
_ocr_ko = None


def _get_ocr():
    """숫자용 기본 RapidOCR 지연 초기화."""
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR

        _ocr = RapidOCR()
    return _ocr


def _get_ocr_ko():
    """한글 텍스트용 RapidOCR(한글 rec 모델) 지연 초기화."""
    global _ocr_ko
    if _ocr_ko is None:
        from rapidocr_onnxruntime import RapidOCR

        _ocr_ko = RapidOCR(rec_model_path=str(_KOREAN_REC_MODEL))
    return _ocr_ko


def _boxes(image_path: Path, ocr) -> list[dict]:
    result, _elapse = ocr(str(image_path))
    items = []
    for box, text, _score in result or []:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        items.append({
            "text": text.strip(),
            "cx": sum(xs) / len(xs), "cy": sum(ys) / len(ys),
            "x0": min(xs), "x1": max(xs), "y0": min(ys), "y1": max(ys),
        })
    return items


def _as_int(text: str) -> str:
    return text.replace(" ", "")


def _strip_team_tag(nickname: str) -> str:
    """프리미어 팀 태그 접두어 제거: '주모|aziin' -> 'aziin'."""
    if "|" in nickname:
        return nickname.rsplit("|", 1)[-1].strip()
    return nickname.strip()


def _clean_agent(text: str) -> str:
    """요원 OCR 텍스트를 정식 명칭에 fuzzy 매칭. 확신 없으면 '' (검토에서 선택)."""
    t = re.sub(r"[^0-9A-Za-z가-힣/]", "", text)
    if not t:
        return ""
    # 인게임 표기가 영문인 KAY/O 는 한글 정식명 '케이오'로 통일.
    if re.sub(r"[^A-Za-z]", "", t).upper() == "KAYO":
        return "케이오"
    match = process.extractOne(t, valid_agents(), scorer=fuzz.WRatio)
    if match and match[1] >= 65:
        return match[0]
    return ""


def _team_of_row(img, anchor: dict, same_row: list[dict]) -> tuple[str, str]:
    """행 배경색으로 팀 판별. 텍스트 영역 스트립의 평균 R vs G 비교.

    흰 글자는 R·G 에 동등하게 기여하므로 배경 색조(초록 G>R / 빨강 R>G)를 가른다.
    """
    if img is None:
        return "A", "초록"
    h, w = img.shape[:2]
    x0 = max(0, int(min(it["x0"] for it in same_row)))
    x1 = min(w, int(max(it["x1"] for it in same_row)))
    y0 = max(0, int(anchor["y0"]))
    y1 = min(h, int(anchor["y1"]))
    if x1 <= x0 or y1 <= y0:
        return "A", "초록"
    strip = img[y0:y1, x0:x1]  # BGR
    mean_g = float(strip[:, :, 1].mean())
    mean_r = float(strip[:, :, 2].mean())
    if mean_g >= mean_r:
        return "A", "초록"
    return "B", "빨강"


def _best_text(group: list[dict]) -> str:
    """같은 줄의 후보 박스들 중 최선의 텍스트 선택.

    한글 닉네임은 한글패스가, 영문 닉네임은 기본패스가 잘 읽으므로
    한글 포함 후보를 우선하고, 없으면 알파벳이 가장 많은 것을 고른다.
    """
    if not group:
        return ""
    hangul = [g for g in group if _HANGUL_RE.search(g["text"])]
    pool = hangul or group
    return max(pool, key=lambda g: len(_LETTER_RE.findall(g["text"])))["text"]


def _right_stats(
    img, econ_col_x: float, anchors: list[dict], row_tol: float
) -> dict[int, tuple[int | None, int | None, int | None]]:
    """econ 오른쪽의 첫킬/설치/해체(작은 한 자리 숫자) 열을 추출.

    이 숫자들은 기본 해상도에선 너무 작아 det가 놓치므로 해당 영역만 3배 확대해
    다시 OCR한다. **열이 3개 모두 확실히 검출될 때만** 채운다 — 열이 일부만 잡히면
    남은 열이 다른 스탯으로 오배정돼 rating(r_obj)을 오염시키기 때문. 각 칸도 검출된
    것만 값, 나머지는 None(검토에서 입력).
    """
    import cv2

    empty = {id(a): (None, None, None) for a in anchors}
    h, w = img.shape[:2]
    y0 = max(0, int(min(a["y0"] for a in anchors)) - 8)
    y1 = min(h, int(max(a["y1"] for a in anchors)) + 8)
    x0 = min(w, int(econ_col_x) + 45)
    if x0 >= w or y1 <= y0:
        return empty
    sc = 3
    up = cv2.resize(img[y0:y1, x0:w], None, fx=sc, fy=sc, interpolation=cv2.INTER_CUBIC)
    result, _elapse = _get_ocr()(up)
    digs = []
    for box, text, _score in result or []:
        t = text.strip()
        if t.isdigit():
            cx = sum(p[0] for p in box) / len(box) / sc + x0
            cy = sum(p[1] for p in box) / len(box) / sc + y0
            digs.append((cx, cy, int(t)))
    if not digs:
        return empty

    # x-간격으로 열 클러스터링 → 지지(행 수) 충분한 열만, 좌→우 3개.
    xs = sorted(d[0] for d in digs)
    cols: list[list[float]] = [[xs[0]]]
    for x in xs[1:]:
        if x - cols[-1][-1] <= 55:
            cols[-1].append(x)
        else:
            cols.append([x])
    centers = [sum(c) / len(c) for c in cols]
    strong = [c for c in centers if sum(1 for d in digs if abs(d[0] - c) < 35) >= 4]
    strong.sort()
    if len(strong) < 3:
        return empty  # 3개 열이 확실치 않으면 오배정 방지 위해 채우지 않음
    fk_x, pl_x, df_x = strong[:3]

    out: dict[int, tuple[int | None, int | None, int | None]] = {}
    for a in anchors:
        vals = []
        for cx in (fk_x, pl_x, df_x):
            cand = [
                d for d in digs
                if abs(d[0] - cx) < 35 and abs(d[1] - a["cy"]) <= row_tol
            ]
            vals.append(cand[0][2] if cand else None)
        out[id(a)] = (vals[0], vals[1], vals[2])
    return out


def _clean_nickname(text: str) -> str:
    """rec 원문에서 앞뒤 잡음(공백·구두점·낱개 기호) 제거. 내부는 그대로(별칭이 교정)."""
    return re.sub(r"^[^0-9A-Za-z가-힣]+|[^0-9A-Za-z가-힣]+$", "", text.strip())


def _rec_name_line(img, cy: float, nh: float, x_left: int, x_right: int) -> str:
    """det를 건너뛰고 알려진 닉네임 셀 영역만 잘라 rec만 강제한다.

    det(검출) 모델이 놓치는 닉네임(글자 간격이 벌어졌거나 어두운 배경 위 흰 글자)을
    KDA 앵커로 계산한 셀 좌표에서 직접 읽어낸다. 2배 확대 후 rec-only.
    """
    import cv2

    h, w = img.shape[:2]
    y0 = max(0, int(cy - 0.95 * nh))
    y1 = min(h, int(cy + 0.5 * nh))
    x0 = max(0, x_left - 4)
    x1 = min(w, x_right)
    if x1 <= x0 or y1 <= y0:
        return ""
    crop = cv2.resize(img[y0:y1, x0:x1], None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    result, _elapse = _get_ocr_ko()(crop, use_det=False, use_cls=False)
    if not result:
        return ""
    return _strip_team_tag(_clean_nickname(result[0][0]))


_SCORE_CHARS = set("승리패배")


def _extract_score(img, ko_items: list[dict]) -> tuple[int | None, int | None]:
    """상단 스코어 배너("N 승리 M")에서 팀 라운드 수를 색으로 읽는다.

    det는 이 스타일라이즈드 색 숫자를 못 잡으므로, 한글패스가 검출한 '승리/패배'
    박스 영역을 색으로 분리(초록=A / 빨강=B)해 각 숫자만 이진화 후 rec-only.
    확신 없으면 None(검토에서 입력). 색 규칙은 행 팀 판별([[_team_of_row]])과 동일.
    """
    if img is None:
        return None, None
    import cv2
    import numpy as np

    h, w = img.shape[:2]
    cands = [
        it for it in ko_items
        if it["cy"] < h * 0.22 and w * 0.28 < it["cx"] < w * 0.72
        and _SCORE_CHARS & set(it["text"]) and any(c.isdigit() for c in it["text"])
    ]
    if not cands:
        return None, None
    box = min(cands, key=lambda it: it["cy"])
    y0, y1 = max(0, int(box["y0"]) - 4), min(h, int(box["y1"]) + 4)
    x0, x1 = max(0, int(box["x0"]) - 12), min(w, int(box["x1"]) + 12)
    reg = img[y0:y1, x0:x1].astype(int)
    b, g, r = reg[:, :, 0], reg[:, :, 1], reg[:, :, 2]
    bright = reg.max(axis=2) > 110
    green = (g > r + 15) & (g > b + 15) & bright
    red = (r > g + 15) & (r > b + 15) & bright

    def read(mask) -> int | None:
        cols = np.where(mask.sum(axis=0) > 1)[0]
        if len(cols) < 3:
            return None
        crop = reg[:, cols.min():cols.max() + 1].astype("uint8")
        _thr, binimg = cv2.threshold(crop.max(axis=2), 110, 255, cv2.THRESH_BINARY)
        up = cv2.resize(binimg, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
        result, _elapse = _get_ocr()(
            cv2.cvtColor(up, cv2.COLOR_GRAY2BGR), use_det=False, use_cls=False
        )
        if not result:
            return None
        digits = "".join(c for c in result[0][0] if c.isdigit())
        val = int(digits) if digits else None
        return val if val is not None and 0 <= val <= 30 else None

    return read(green), read(red)


def _name_and_agent(name_items: list[dict], cy: float, band: float) -> tuple[str, str]:
    """이름 열(ACS 왼쪽)에서 닉네임(윗줄)/요원(아랫줄)을 뽑는다.

    닉네임/요원은 두 줄로 쌓여 있고 위치가 KDA 앵커(cy) 기준으로 일정하다
    (닉네임 offset ≈ -8, 요원 ≈ +12). 그래서 검출된 박스끼리의 상대 위치가 아니라
    **앵커 기준 offset**으로 두 줄을 가른다 — 한 줄이 미검출돼도 나머지를 오분류하지 않는다.
    각 줄에서 최선의 텍스트를 고른다([[_best_text]]).
    """
    split = band * 0.25  # 이 위로는 닉네임 줄, 아래로는 요원 줄
    upper, lower = [], []
    for it in name_items:
        off = it["cy"] - cy
        if abs(off) > band or _INT_RE.match(_as_int(it["text"])):
            continue
        (lower if off >= split else upper).append(it)
    nickname = _strip_team_tag(_best_text(upper))
    agent = _clean_agent(_best_text(lower))
    return nickname, agent


def extract_scoreboard(image_path: str | Path) -> ExtractionResult:
    """스코어보드 이미지에서 행별 스탯을 추출.

    숫자는 기본 모델(패스 A), 닉네임/요원은 한글 모델(패스 B)로 읽는다.
    """
    path = Path(image_path)
    items = _boxes(path, _get_ocr())
    ko_items = _boxes(path, _get_ocr_ko())

    # K/D/A 문자열("26/7/5")을 행 앵커로 사용 — 한 플레이어당 정확히 하나.
    anchors = []
    for it in items:
        m = _KDA_RE.match(_as_int(it["text"]))
        if m:
            it["kda"] = tuple(int(g) for g in m.groups())
            anchors.append(it)
    anchors.sort(key=lambda it: it["cy"])

    import cv2

    img = cv2.imread(str(path))
    score_a, score_b = _extract_score(img, ko_items)

    if not anchors:
        return ExtractionResult(rows=[], map_name=None,
                                team_a_rounds=score_a, team_b_rounds=score_b)

    kda_x = sorted(a["cx"] for a in anchors)[len(anchors) // 2]  # 열 중앙 x

    # 1차: 유효한 플레이어 행과 그 ACS 박스를 모아 이름 열의 우측 경계를 정한다.
    valid: list[tuple[dict, dict]] = []  # (anchor, acs_box)
    for a in anchors:
        tol = max(a["y1"] - a["y0"], 12) * 0.7
        left = [
            it for it in items
            if abs(it["cy"] - a["cy"]) <= tol and it["cx"] < kda_x
            and _INT_RE.match(_as_int(it["text"]))
        ]
        # ACS 정수가 없으면 실제 플레이어 행이 아님(상단 계정 진행바 등) → 스킵.
        if not left:
            continue
        acs_box = max(left, key=lambda it: it["cx"])  # KDA에 가장 가까운(=ACS)
        valid.append((a, acs_box))

    if not valid:
        return ExtractionResult(rows=[], map_name=None,
                                team_a_rounds=score_a, team_b_rounds=score_b)

    # 이름 열은 ACS 열보다 왼쪽. ACS 열 좌측 경계 위로 KDA 깨진 텍스트 오염을 막는다.
    name_bound = min(acs["x0"] for _a, acs in valid)
    name_items = [
        it for it in (items + ko_items)
        if it["x1"] <= name_bound and _LETTER_RE.search(it["text"])
    ]
    # 닉네임 텍스트 열의 좌측 경계(플레이어 아이콘 오른쪽). det-free rec 폴백의 크롭 시작점.
    # 좌상단 UI 텍스트 오염을 막기 위해 선수 행의 세로 범위 안에 있는 박스만 본다.
    row_top = min(a["cy"] for a, _ in valid) - 30
    row_bot = max(a["cy"] for a, _ in valid) + 30
    row_names = [it for it in name_items if row_top <= it["cy"] <= row_bot]
    name_left = int(min((it["x0"] for it in row_names), default=name_bound - 150))
    name_rec_right = int(name_left + 0.62 * (name_bound - name_left))

    # econ 열 위치(중앙값) — 그 오른쪽에서 첫킬/설치/해체를 별도 확대 패스로 추출.
    econ_boxes = []
    for a, _acs in valid:
        tol = max(a["y1"] - a["y0"], 12) * 0.7
        rr = [
            it for it in items
            if abs(it["cy"] - a["cy"]) <= tol and it["cx"] > kda_x
            and _INT_RE.match(_as_int(it["text"]))
        ]
        if rr:
            econ_boxes.append((a, min(rr, key=lambda it: it["cx"])))
    econ_of = {id(a): b for a, b in econ_boxes}

    obj_stats: dict[int, tuple[int | None, int | None, int | None]] = {}
    if econ_boxes:
        econ_col_x = sorted(b["cx"] for _a, b in econ_boxes)[len(econ_boxes) // 2]
        row_tol = max(max(a["y1"] - a["y0"], 12) * 0.7 for a, _ in valid)
        obj_stats = _right_stats(img, econ_col_x, [a for a, _ in valid], row_tol)

    rows: list[ExtractedRow] = []
    for a, acs_box in valid:
        tol = max(a["y1"] - a["y0"], 12) * 0.7
        same = [it for it in items if abs(it["cy"] - a["cy"]) <= tol]
        acs = int(_as_int(acs_box["text"]))

        econ_box = econ_of.get(id(a))
        econ = int(_as_int(econ_box["text"])) if econ_box else None

        nickname, agent_kr = _name_and_agent(name_items, a["cy"], band=tol + 14)
        # det가 닉네임 박스를 못 만든 행은 셀을 직접 잘라 rec만 강제(글자 간격/저대비 대응).
        if not nickname:
            nickname = _rec_name_line(
                img, a["cy"], a["y1"] - a["y0"], name_left, name_rec_right
            )

        first_kills, plants, defuses = obj_stats.get(id(a), (None, None, None))
        kills, deaths, assists = a["kda"]
        team, color = _team_of_row(img, a, same)
        rows.append(ExtractedRow(
            nickname=nickname, agent_kr=agent_kr, team_color=color, team=team,
            acs=acs, kills=kills, deaths=deaths, assists=assists, econ=econ,
            first_kills=first_kills, plants=plants, defuses=defuses,
        ))

    return ExtractionResult(rows=rows, map_name=None,
                            team_a_rounds=score_a, team_b_rounds=score_b)
