"""지표 사전(metrics.yaml) 읽기와 찾기 (4단계)."""
from __future__ import annotations

import functools
import re
from importlib import resources
from typing import Any

import yaml


@functools.cache
def load() -> dict[str, Any]:
    text = resources.files("inhouse.agent").joinpath("metrics.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text)


def normalize(word: str) -> str:
    return re.sub(r"\s+", "", word).lower()


def fill(sql: str, start: str, end: str) -> str:
    """예시 SQL의 기간 자리({start}, {end}: 현지 날짜 YYYY-MM-DD, end는 포함 안 함)를 채운다."""
    for d in (start, end):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            raise ValueError(f"날짜 형식은 YYYY-MM-DD: {d!r}")
    return sql.replace("{start}", start).replace("{end}", end)


def _words(m: dict[str, Any]) -> list[str]:
    return [m["id"], m["name"], *m["aliases"]]


def lookup(query: str) -> dict[str, Any]:
    """정확히 같은 이름·별칭이 먼저, 없으면 질문 안에 들어 있는 가장 긴 이름·별칭."""
    k = load()
    q = normalize(query)
    best, best_len = None, 0
    for m in k["metrics"]:
        for w in _words(m):
            nw = normalize(w)
            if nw == q:
                best, best_len = m, 10**6
            elif nw and nw in q and len(nw) > best_len:
                best, best_len = m, len(nw)
    if best is None:
        return {
            "message": "지표 사전에 없는 지표입니다. 계산한다면 쓴 가정(포함·제외 기준, 날짜 기준)을 답에 밝히세요.",
            "available": [m["name"] for m in k["metrics"]],
            "common_rules": k["common_rules"],
        }
    out = {key: best[key] for key in ["id", "name", "aliases", "definition", "rules", "status", "sql"]}
    out["open_questions"] = best.get("open_questions", [])
    out["sql_note"] = "{start}/{end}에 현지 날짜(YYYY-MM-DD, end는 포함 안 함)를 넣어 쓰세요."
    return {"metric": out, "common_rules": k["common_rules"]}
