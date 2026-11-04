"""2a-4 문서 질문 평가: eval/doc_questions.yaml → 에이전트 → 규칙 기반 채점 (LLM 채점 없음).

맞음 = must_include 묶음마다 하나 이상 + must_not_include 없음 + must_cite 중 하나를 실제로 받아 인용 + gold_sql 숫자가 답에 있음.
인용 문장 검증(quote_found)은 정답 여부와 따로 센다 (citations_verified).

사용법: DATABASE_URL=… TCN_USER_PASSWORD=… ANTHROPIC_API_KEY=… python -m inhouse.eval.docs_eval [--repeat 3] [--workers 4]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import yaml

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers(text: str) -> list[float]:
    return [float(m.group(0).replace(",", "")) for m in _NUM.finditer(text or "")]


def _has_number(nums: list[float], v: float, tol: float) -> bool:
    return any(abs(n - v) <= max(abs(v) * tol, 0.005) for n in nums)


def grade(q: dict, ans, gold: list[list] | None = None, tol: float = 0.0005) -> dict:
    text = ans.text or ""
    cites = ans.doc_citations or []
    verified = (sum(1 for c in cites if c["retrieved"] and c["quote_found"]), len(cites))
    out = {"passed": False, "why": "", "citations_verified": verified}
    for bad in q.get("must_not_include", []):
        if bad in text:
            out["why"] = f"leaked:{bad}"
            return out
    for group in q.get("must_include", []):
        if not any(w in text for w in group):
            out["why"] = f"missing:{group[0]}"
            return out
    if gold:
        nums = numbers(text)
        want = [float(v) for row in gold for v in row if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)]
        miss = [v for v in want if not _has_number(nums, v, tol)]
        if miss:
            out["why"] = f"number:{miss[0]}"
            return out
    if q.get("must_cite"):
        ok = {c["chunk_id"] for c in cites if c["retrieved"]}
        if not ok & set(q["must_cite"]):
            out["why"] = "no_citation"
            return out
    out["passed"] = True
    return out


def run(questions, dsn, password, client, model, repeat=1, workers=4, effort="medium"):
    import psycopg

    from ..agent import agent
    from ..agent.audit import MemoryAudit
    from ..agent.tools import Toolbox
    gold = {}
    with psycopg.connect(dsn) as conn:
        for q in questions:
            if q.get("gold_sql"):
                gold[q["id"]] = [list(r) for r in conn.execute(q["gold_sql"]).fetchall()]

    def one(job):
        q, i = job
        a = agent.ask(client, Toolbox(dsn, password, q["db_user"], MemoryAudit(), max_rows=100), q["question"],
                      model=model, effort=effort)
        g = grade(q, a, gold.get(q["id"]), tol=q.get("tol", 0.0005))
        return {"id": q["id"], "run": i, "db_user": q["db_user"], "traps": q.get("traps", []), **g,
                "cost_usd": a.cost_usd, "seconds": a.seconds, "turns": a.turns,
                "tools": [c.name for c in a.calls], "answer": a.text, "doc_citations": a.doc_citations}

    jobs = [(q, i) for q in questions for i in range(1, repeat + 1)]
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(one, jobs))


def summarize(rows, model) -> str:
    n, ok = len(rows), sum(r["passed"] for r in rows)
    ver = sum(r["citations_verified"][0] for r in rows), sum(r["citations_verified"][1] for r in rows)
    ids = sorted({r["id"] for r in rows})
    always = sum(all(r["passed"] for r in rows if r["id"] == i) for i in ids)
    cost = sum(r["cost_usd"] for r in rows)
    lines = [f"# 문서 질문 평가 — {model}", "",
             f"- 정확도: **{ok}/{n} ({100 * ok / n:.0f}%)**, 매번 맞힌 문항 {always}/{len(ids)}",
             f"- 인용 문장 검증 통과: {ver[0]}/{ver[1]} ({100 * ver[0] / max(ver[1], 1):.0f}%)",
             f"- 비용: 합계 ${cost:.4f}, 문항당 ${cost / n:.4f} · 시간: 문항당 {sum(r['seconds'] for r in rows) / n:.1f}초",
             "", "| 함정 | 정확도 |", "|---|---|"]
    traps = sorted({t for r in rows for t in r["traps"]}, key=lambda t: int(t[2:]))
    for t in traps:
        rs = [r for r in rows if t in r["traps"]]
        lines.append(f"| {t} | {sum(r['passed'] for r in rs)}/{len(rs)} |")
    bad = [r for r in rows if not r["passed"]]
    if bad:
        lines += ["", "## 틀린 실행", ""]
        lines += [f"- **{r['id']}** #{r['run']} ({r['db_user']}) — {r['why']}" for r in bad]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="eval/doc_questions.yaml")
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
    out = a.out or f"eval/results/docs-{a.model}-{dt.datetime.now():%Y%m%d-%H%M}.jsonl"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    print(summarize(rows, a.model))
    print(f"\n기록: {out}")


if __name__ == "__main__":
    main()
