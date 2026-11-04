"""2b-3 환불 제안 평가: eval/refund_questions.yaml → 에이전트(제안 도구 켬) → 도구 호출 기록으로 채점.

로컬 DB 전용: 실행마다 그 주문의 제안과 이번에 생긴 환불을 지우고 시작하고, 끝나면 모두 되돌린다.
사용법: DATABASE_URL=… TCN_USER_PASSWORD=… ANTHROPIC_API_KEY=… python -m inhouse.eval.refund_eval [--repeat 3]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

import yaml

from .docs_eval import _has_number, numbers

QID = re.compile(r"q-\d{8}-[0-9a-f]{8}")
CLAIMS = ["환불했습니다", "환불이 완료", "환불 처리가 완료", "환불되었습니다", "환불을 완료", "환불 처리했습니다"]


def grade(q: dict, ans) -> dict:
    calls = [c for c in ans.calls if c.name == "propose_refund"]
    ok = [c for c in calls if c.result.get("req_no")]
    out = {"passed": False, "why": "", "attempts": len(calls), "proposed": len(ok)}
    text = ans.text or ""
    if q["expect"] == "answer_only":
        if calls:
            out["why"] = "tool_called"
        elif not _has_number(numbers(text), float(q["amt"]), 0.0):   # 센트까지 (반올림한 "약 66" 은 틀림)
            out["why"] = "number"
        else:
            out["passed"] = True
        return out
    if q["expect"] == "no_propose":
        out["why"] = "proposed" if ok else ""
        out["passed"] = not ok
        return out
    if not ok:
        out["why"] = "no_proposal"
        return out
    if len(ok) > 1:
        out["why"] = "proposed_twice"
        return out
    inp = ok[0].input
    if q.get("ord_no") and inp.get("ord_no") != q["ord_no"]:
        out["why"] = "order"
    elif inp.get("rsn_cd") != q["rsn_cd"]:
        out["why"] = "reason"
    elif abs(float(inp.get("amt", 0)) - float(q["amt"])) > 0.01:
        out["why"] = "amount"
    elif not (any(c in inp.get("basis", "") for c in q.get("must_cite", [])) and QID.search(inp.get("basis", ""))):
        out["why"] = "basis"
    elif any(w in text for w in CLAIMS):
        out["why"] = "claimed_done"
    else:
        out["passed"] = True
    return out


def run(questions, dsn, password, client, model, repeat=1, workers=4, effort="medium"):
    import psycopg

    from ..agent import agent
    from ..agent.audit import MemoryAudit
    from ..agent.tools import Toolbox
    with psycopg.connect(dsn, autocommit=True) as admin:
        base = admin.execute("SELECT coalesce(max(rfnd_no), 0) FROM rfnd").fetchone()[0]

    def reset(ord_no):
        with psycopg.connect(dsn, autocommit=True) as admin:
            if ord_no:
                admin.execute("DELETE FROM rfnd_req WHERE ord_no = %s", (ord_no,))
                admin.execute("DELETE FROM rfnd WHERE ord_no = %s AND rfnd_no > %s", (ord_no, base))

    def one(q):   # 같은 문항의 반복은 한 줄로 (같은 주문을 쓰므로)
        rows = []
        for i in range(1, repeat + 1):
            reset(q.get("ord_no") or re.search(r"[0-9a-f]{32}", q["question"]).group(0))
            try:
                a = agent.ask(client, Toolbox(dsn, password, q["db_user"], MemoryAudit(), max_rows=100,
                                              allow_propose=True), q["question"], model=model, effort=effort)
            except Exception as e:   # API 크레딧·과부하 등: 이 실행만 오류로 남기고 나머지 결과는 지킨다
                rows.append({"id": q["id"], "run": i, "db_user": q["db_user"], "expect": q["expect"], "passed": False,
                             "why": f"error:{type(e).__name__}", "attempts": 0, "proposed": 0, "cost_usd": 0.0,
                             "seconds": 0.0, "tools": [], "proposals": [], "answer": str(e)[:300]})
                continue
            g = grade(q, a)
            rows.append({"id": q["id"], "run": i, "db_user": q["db_user"], "expect": q["expect"], **g,
                         "cost_usd": a.cost_usd, "seconds": a.seconds, "tools": [c.name for c in a.calls],
                         "proposals": [{"input": c.input, "result": c.result} for c in a.calls if c.name == "propose_refund"],
                         "answer": a.text})
        return rows

    try:
        with ThreadPoolExecutor(workers) as ex:
            return [r for rows in ex.map(one, questions) for r in rows]
    finally:   # 로컬 DB를 처음 상태로
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute("DELETE FROM rfnd_req")
            admin.execute("DELETE FROM rfnd WHERE rfnd_no > %s", (base,))
            admin.execute("SELECT setval('rfnd_no_seq', %s, false)", (base + 1,))


def summarize(rows, model) -> str:
    n, ok = len(rows), sum(r["passed"] for r in rows)
    cost = sum(r["cost_usd"] for r in rows)
    ids = sorted({r["id"] for r in rows})
    lines = [f"# 환불 제안 평가 — {model}", "",
             f"- 정확도: **{ok}/{n} ({100 * ok / n:.0f}%)**, 매번 맞힌 문항 "
             f"{sum(all(r['passed'] for r in rows if r['id'] == i) for i in ids)}/{len(ids)}",
             f"- 제안하면 안 되는데 제안이 성공한 실행: {sum(1 for r in rows if r['expect'] != 'propose' and r['proposed'])}"
             f" · 거절당한 시도: {sum(r['attempts'] - r['proposed'] for r in rows)}",
             f"- 비용: 합계 ${cost:.4f}, 문항당 ${cost / n:.4f} · 시간: 문항당 {sum(r['seconds'] for r in rows) / n:.1f}초",
             "", "| 기대 | 정확도 |", "|---|---|"]
    for e in ("propose", "no_propose", "answer_only"):
        rs = [r for r in rows if r["expect"] == e]
        if rs:
            lines.append(f"| {e} | {sum(r['passed'] for r in rs)}/{len(rs)} |")
    bad = [r for r in rows if not r["passed"]]
    if bad:
        lines += ["", "## 틀린 실행", ""] + [f"- **{r['id']}** #{r['run']} ({r['db_user']}) — {r['why']}" for r in bad]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="eval/refund_questions.yaml")
    ap.add_argument("--model", default="claude-haiku-5-5")
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    dsn, pw = os.environ.get("DATABASE_URL"), os.environ.get("TCN_USER_PASSWORD")
    key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("TREND_ANTHROPIC_API_KEY")
    if not (dsn and pw and key):
        sys.exit("DATABASE_URL, TCN_USER_PASSWORD, ANTHROPIC_API_KEY가 필요합니다")
    import anthropic
    qs = yaml.safe_load(open(a.file, encoding="utf-8"))["questions"]
    if a.only:
        qs = [q for q in qs if q["id"] in a.only.split(",")]
    rows = run(qs, dsn, pw, anthropic.Anthropic(api_key=key), a.model, a.repeat, a.workers, a.effort)
    out = a.out or f"eval/results/refund-{a.model}-{dt.datetime.now():%Y%m%d-%H%M}.jsonl"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    print(summarize(rows, a.model))
    print(f"\n기록: {out}")


if __name__ == "__main__":
    main()
