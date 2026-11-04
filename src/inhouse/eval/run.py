"""정답셋으로 에이전트를 채점한다 (5단계).

사용법: python -m inhouse.eval.run --model claude-haiku-5-5 [--repeat 3] [--workers 4] [--only C01,E04]
환경변수: DATABASE_URL(접속 정보, 사용자는 문항마다 바뀜), TCN_USER_PASSWORD, ANTHROPIC_API_KEY(또는 TREND_ANTHROPIC_API_KEY)
결과: eval/results/<model>-<시각>.jsonl (문항별) + .md (요약)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import yaml

from ..agent import agent as agent_mod
from ..agent.audit import JsonlAudit, MemoryAudit
from ..agent.runner import Runner
from ..agent.tools import Toolbox
from .match import matches, refused


def load_questions(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["questions"]


def grade(item: dict, gold: dict | None, ans: agent_mod.Answer) -> dict:
    results = ans.sql_results()
    cited = [results[q] for q in ans.cited if q in results]
    if item["expect"] == "refuse":
        return {"passed": refused(ans.text), "via": "refusal_text"}
    cmp = item.get("compare") or {}
    kw = {"tol": cmp.get("tol", 0.0005), "rate": cmp.get("rate", False), "order_matters": cmp.get("order_matters", False),
          "absolute": cmp.get("abs", False)}
    if any(matches(gold["rows"], r["rows"], **kw) for r in cited):
        return {"passed": True, "via": "cited"}
    if any(matches(gold["rows"], r["rows"], **kw) for r in results.values()):
        return {"passed": True, "via": "uncited"}  # 맞는 숫자를 구했지만 출처 표시를 안 함
    return {"passed": False, "via": "no_match"}


def summarize(rows: list[dict], model: str, skipped: list[str]) -> str:
    by = defaultdict(list)
    for r in rows:
        by[r["category"]].append(r)
    per_q = defaultdict(list)
    for r in rows:
        per_q[r["id"]].append(r["passed"])
    reps = max((len(v) for v in per_q.values()), default=1)
    lines = [f"# 평가 결과 — {model}", "",
             f"- 실행: {len(per_q)}문항 × {reps}회 = {len(rows)}번, 정답 SQL이 없어 건너뜀: {len(skipped)}문항",
             f"- 매번 맞힌 문항: {sum(all(v) for v in per_q.values())}/{len(per_q)}, "
             f"한 번이라도 틀린 문항: {sum(not all(v) and any(v) for v in per_q.values())}, "
             f"매번 틀린 문항: {sum(not any(v) for v in per_q.values())}",
             f"- 정확도: {sum(r['passed'] for r in rows)}/{len(rows)}"
             f" ({100 * sum(r['passed'] for r in rows) / max(len(rows), 1):.0f}%)",
             f"- 출처(query_id) 표시율: {sum(r['via'] == 'cited' for r in rows if r['expect'] == 'result')}"
             f"/{sum(r['passed'] for r in rows if r['expect'] == 'result')} (맞은 결과 문항 중)",
             f"- 비용: 합계 ${sum(r['cost_usd'] for r in rows):.4f}, 문항당 ${sum(r['cost_usd'] for r in rows) / max(len(rows), 1):.4f}",
             f"- 시간: 문항당 평균 {sum(r['seconds'] for r in rows) / max(len(rows), 1):.1f}초", "",
             "| 분류 | 정확도 | 평균 도구 호출 | 평균 비용 |", "|---|---|---|---|"]
    for cat, rs in by.items():
        lines.append(f"| {cat} | {sum(r['passed'] for r in rs)}/{len(rs)} | {sum(r['tool_calls'] for r in rs) / len(rs):.1f}"
                     f" | ${sum(r['cost_usd'] for r in rs) / len(rs):.4f} |")
    fails = [r for r in rows if not r["passed"]]
    if fails:
        lines += ["", "## 틀린 실행", ""]
        lines += [f"- **{r['id']}** #{r['rep']} ({r['db_user']}) {r['question']} — {r['via']}, stop={r['stop_reason']}" for r in fails]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-haiku-5-5")
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--questions", default="eval/questions.yaml")
    ap.add_argument("--only", help="쉼표로 구분한 문항 id")
    ap.add_argument("--out", default="eval/results")
    ap.add_argument("--repeat", type=int, default=1, help="문항마다 몇 번 돌릴지 (실행마다 흔들리는 걸 보려고)")
    ap.add_argument("--workers", type=int, default=1, help="동시에 돌릴 문항 수")
    a = ap.parse_args()

    import anthropic
    dsn, pw = os.environ.get("DATABASE_URL"), os.environ.get("TCN_USER_PASSWORD")
    key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("TREND_ANTHROPIC_API_KEY")
    if not (dsn and pw and key):
        sys.exit("DATABASE_URL, TCN_USER_PASSWORD, ANTHROPIC_API_KEY(또는 TREND_ANTHROPIC_API_KEY)가 필요합니다")
    client = anthropic.Anthropic(api_key=key)

    items = load_questions(a.questions)
    if a.only:
        wanted = set(a.only.split(","))
        items = [q for q in items if q["id"] in wanted]
    skipped = [q["id"] for q in items if q["expect"] == "result" and not q.get("gold_sql")]
    items = [q for q in items if q["id"] not in skipped]

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    os.makedirs(a.out, exist_ok=True)
    base = os.path.join(a.out, f"{a.model}-{stamp}")
    gold_runner = Runner(dsn, pw, MemoryAudit(), max_rows=1000, timeout_ms=30000)
    audit = JsonlAudit(base + "-audit.jsonl")
    golds = {}
    for q in items:
        if q["expect"] == "result":
            g = gold_runner.run(q["db_user"], q["gold_sql"]).to_dict()
            if g["status"] != "ok":
                print(f"{q['id']}: 정답 SQL 실패 — {g['error']}", file=sys.stderr)
                continue
            golds[q["id"]] = g
    jobs = [(q, r) for r in range(1, a.repeat + 1) for q in items if q["expect"] == "refuse" or q["id"] in golds]
    rows, lock = [], threading.Lock()

    def one(job):
        q, rep_no = job
        gold = golds.get(q["id"])
        ans = agent_mod.ask(client, Toolbox(dsn, pw, q["db_user"], audit, max_rows=100), q["question"],
                            model=a.model, effort=a.effort)
        g = grade(q, gold, ans)
        row = {"id": q["id"], "rep": rep_no, "category": q["category"], "db_user": q["db_user"],
               "question": q["question"], "expect": q["expect"], "gold_by": q.get("gold_by"), **g,
               "stop_reason": ans.stop_reason, "turns": ans.turns, "tool_calls": len(ans.calls),
               "cost_usd": ans.cost_usd, "seconds": ans.seconds, "tokens": ans.tokens, "answer": ans.text,
               "cited": ans.cited, "sql": [c.input.get("sql") for c in ans.calls if c.name == "run_sql"],
               "results": {q: {"rows": r["rows"][:50]} for q, r in ans.sql_results().items()},
               "gold_rows": (gold or {}).get("rows", [])[:5]}
        with lock:
            rows.append(row)
            print(f"{q['id']} #{rep_no} {'O' if g['passed'] else 'X'} {g['via']:13} {ans.turns}턴 ${ans.cost_usd:.4f} {ans.seconds}s",
                  flush=True)
            with open(base + ".jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        list(pool.map(one, jobs))
    rows.sort(key=lambda r: (r["id"], r["rep"]))
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(summarize(rows, a.model, skipped))
    print(summarize(rows, a.model, skipped))


if __name__ == "__main__":
    main()
