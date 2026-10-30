"""에이전트가 실행한 쿼리 결과가 정답 SQL 결과와 같은지 비교한다 (실행 정확도).

- 정답이 값 하나(1×1)면: 에이전트 결과의 어느 칸이든 그 값과 같으면 맞음 (열 이름·행 모양 무관)
- 정답이 표면: 행 수가 같고, 정답의 열마다 값 묶음이 같은 열이 에이전트 결과에 있으면 맞음 (열 이름·열 순서·추가 열 무관)
  order_matters면 행 순서도 같아야 함
- 숫자는 상대 오차 tol 안이면 같다고 본다 (반올림 차이). rate면 0.0283과 2.83(%)을 같은 값으로 본다
"""
from __future__ import annotations

import re
from typing import Any

DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(",", ""))
        except ValueError:
            return None
    return None


def same(a: Any, b: Any, tol: float, rate: bool = False) -> bool:
    na, nb = _num(a), _num(b)
    if na is not None and nb is not None:
        cands = [nb, nb * 100, nb / 100] if rate else [nb]
        return any(abs(na - c) <= max(tol * abs(na), 0.005) for c in cands)  # 0.005: 소수 둘째 자리 반올림
    if a is None or b is None:
        return a is None and b is None
    sa, sb = str(a).strip().lower(), str(b).strip().lower()
    if DATE.match(sa) and DATE.match(sb) and min(len(sa), len(sb)) == 10:  # 날짜 vs 날짜+자정 시각
        return sa[:10] == sb[:10]
    return sa == sb


def _col_match(g: list[Any], c: list[Any], tol: float, rate: bool, ordered: bool) -> bool:
    if len(g) != len(c):
        return False
    if ordered:
        return all(same(x, y, tol, rate) for x, y in zip(g, c))
    pool = list(c)
    for x in sorted(g, key=lambda v: (v is None, str(v))):
        hit = next((i for i, y in enumerate(pool) if same(x, y, tol, rate)), None)
        if hit is None:
            return False
        pool.pop(hit)
    return True


def matches(gold_rows: list[list[Any]], cand_rows: list[list[Any]], tol: float = 0.0005, rate: bool = False,
            order_matters: bool = False) -> bool:
    if len(gold_rows) == 1 and len(gold_rows[0]) == 1:
        target = gold_rows[0][0]
        return any(same(target, v, tol, rate) for row in cand_rows for v in row)
    if len(gold_rows) != len(cand_rows):
        return False
    if not gold_rows:
        return True
    gcols = list(zip(*gold_rows))
    ccols = list(zip(*cand_rows))
    return all(any(_col_match(list(g), list(c), tol, rate, order_matters) for c in ccols) for g in gcols)


REFUSAL_MARKERS = ("볼 수 없", "볼 수가 없", "조회할 수 없", "제공할 수 없", "제공해 드릴 수 없", "할 수 없", "권한이 없",
                   "권한 밖", "권한으로는", "도와드릴 수 없", "드릴 수 없", "접근할 수 없", "변경할 수 없", "수정할 수 없", "삭제할 수 없")


def refused(text: str) -> bool:
    return any(m in text for m in REFUSAL_MARKERS)

