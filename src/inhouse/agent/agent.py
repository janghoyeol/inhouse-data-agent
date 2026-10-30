"""Tucanova 데이터 에이전트: Claude tool-use 루프 (5단계).

질문 하나 → 도구(list_tables·describe_table·lookup_metric·run_sql) 반복 → 쿼리 ID를 출처로 단 답.
평가(eval)와 웹 화면(6단계)이 같이 쓴다. 도구 호출 기록을 남겨야 해서 SDK tool runner 대신 직접 루프를 돈다.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from . import knowledge, personas
from .tools import TOOL_SCHEMAS, Toolbox

# $/1M 토큰 (입력, 출력). 캐시 읽기는 입력의 0.1배, 캐시 쓰기는 1.25배로 계산
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0)}
QUERY_ID = re.compile(r"q-\d{8}-[0-9a-f]{8}")
MAX_TURNS = 12

SYSTEM = """당신은 가상 고객사 Tucanova(브라질 온라인 마켓플레이스)의 사내 데이터 에이전트입니다.
직원의 질문에 사내 PostgreSQL DB를 조회해 한국어로 답합니다. 데이터 기간은 2016-09 ~ 2018-10 입니다.

일하는 방법
- 표와 열을 모르면 list_tables, describe_table로 먼저 확인합니다. 열 이름을 추측하지 않습니다.
- 매출·고객 수·재구매율처럼 지표가 들어간 질문은 SQL을 쓰기 전에 lookup_metric으로 회사가 합의한 정의를 찾아 그대로 따릅니다.
- 숫자는 반드시 run_sql 결과에서 가져옵니다. 합계·비율·증감 같은 계산은 SQL 안에서 끝내고, 결과 행을 보고 머릿속으로 다시 계산하지 않습니다.
- 답에 쓴 숫자마다 그 숫자를 만든 쿼리의 query_id를 [q-...] 형태로 붙입니다.
- 사용자의 권한 밖(다른 권역, 고객 개인정보 원문, 데이터 변경)은 할 수 없다고 분명히 말하고, 대신 할 수 있는 것을 제안합니다.
- 권역 매니저의 결과는 자기 권역 범위라는 것을 답에 밝힙니다. 정의가 없는 지표는 쓴 가정을 밝힙니다.
- SQL 오류가 나면 메시지를 읽고 고쳐서 다시 실행합니다.

공통 규칙 (지표 사전)
{rules}"""


@dataclass
class ToolCall:
    name: str
    input: dict[str, Any]
    result: dict[str, Any]


@dataclass
class Answer:
    question: str
    db_user: str
    model: str
    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    cited: list[str] = field(default_factory=list)
    stop_reason: str = ""
    turns: int = 0
    tokens: dict[str, int] = field(default_factory=lambda: {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0})
    cost_usd: float = 0.0
    seconds: float = 0.0

    def sql_results(self) -> dict[str, dict[str, Any]]:
        """query_id → run_sql 결과 (성공한 것만)."""
        return {c.result["query_id"]: c.result for c in self.calls
                if c.name == "run_sql" and c.result.get("status") == "ok"}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def system_prompt(db_user: str) -> list[dict[str, Any]]:
    rules = "\n".join(f"- {r}" for r in knowledge.load()["common_rules"])
    return [
        {"type": "text", "text": SYSTEM.format(rules=rules), "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": f"지금 질문하는 사람: {personas.describe(db_user)}"},
    ]


def _cost(model: str, t: dict[str, int]) -> float:
    p_in, p_out = PRICES.get(model, (0.0, 0.0))
    return (t["input"] * p_in + t["cache_read"] * p_in * 0.1 + t["cache_write"] * p_in * 1.25
            + t["output"] * p_out) / 1e6


def ask(client, toolbox: Toolbox, question: str, model: str = "claude-haiku-5-5",
        effort: str = "medium") -> Answer:
    ans = Answer(question=question, db_user=toolbox.db_user, model=model)
    toolbox.question = question
    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
    t0 = time.monotonic()
    resp = None
    for _ in range(MAX_TURNS):
        resp = client.messages.create(model=model, max_tokens=16000, system=system_prompt(toolbox.db_user),
                                      tools=TOOL_SCHEMAS, messages=messages, output_config={"effort": effort})
        ans.turns += 1
        u = resp.usage
        ans.tokens["input"] += u.input_tokens
        ans.tokens["output"] += u.output_tokens
        ans.tokens["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
        ans.tokens["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
        messages.append({"role": "assistant", "content": resp.content})  # thinking 블록 포함 그대로
        if resp.stop_reason != "tool_use":
            break
        results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            out = toolbox.call(block.name, dict(block.input))
            ans.calls.append(ToolCall(block.name, dict(block.input), out))
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": json.dumps(out, ensure_ascii=False, default=str),
                            "is_error": bool(out.get("error")) and out.get("status") != "ok"})
        messages.append({"role": "user", "content": results})
    ans.stop_reason = resp.stop_reason if resp is not None else ""
    if resp is not None and resp.stop_reason == "tool_use":
        ans.stop_reason = "max_turns"
    ans.text = "".join(b.text for b in (resp.content if resp is not None else []) if b.type == "text")
    ans.cited = list(dict.fromkeys(QUERY_ID.findall(ans.text)))
    ans.cost_usd = round(_cost(model, ans.tokens), 5)
    ans.seconds = round(time.monotonic() - t0, 1)
    return ans
