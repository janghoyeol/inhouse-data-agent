"""채점 규칙이 바뀌었을 때 이미 돌린 결과를 다시 채점한다 (API를 다시 부르지 않는다).

틀렸던 실행만 다시 본다: 기록된 결과 행이 있으면 그걸로, 없으면(옛 기록) 에이전트가 쓴 SQL을 같은 직원 역할로 다시 실행해
새 규칙으로 맞춰 본다. 데이터가 바뀌지 않았으므로 같은 결과가 나온다. 규칙은 느슨해지기만 했으므로 맞았던 실행은 그대로 둔다.
사용법: DATABASE_URL=… TCN_USER_PASSWORD=… python -m inhouse.eval.regrade eval/results/<파일>.jsonl
"""
from __future__ import annotations

import json
import os
import sys

from ..agent.audit import MemoryAudit
from ..agent.runner import Runner
from .match import matches, refused
from .run import load_questions, summarize


def main() -> None:
    path = sys.argv[1]
    qs = {q["id"]: q for q in load_questions(os.environ.get("QUESTIONS", "eval/questions.yaml"))}
    runner = Runner(os.environ["DATABASE_URL"], os.environ["TCN_USER_PASSWORD"], MemoryAudit(), max_rows=1000,
                    timeout_ms=30000)
    rows, changed = [], []
    for line in open(path, encoding="utf-8"):
        r = json.loads(line)
        q = qs[r["id"]]
        if not r["passed"]:
            if q["expect"] == "refuse":
                ok = refused(r["answer"])
            else:
                gold = runner.run(q["db_user"], q["gold_sql"]).rows
                cmp = q.get("compare") or {}
                kw = {"tol": cmp.get("tol", 0.0005), "rate": cmp.get("rate", False),
                      "order_matters": cmp.get("order_matters", False), "absolute": cmp.get("abs", False)}
                if "results" in r:
                    cands = [v["rows"] for v in r["results"].values()]
                else:
                    cands = [x.rows for x in (runner.run(q["db_user"], s) for s in r["sql"] if s) if x.status == "ok"]
                ok = any(matches(gold, c, **kw) for c in cands)
            if ok:
                r["passed"], r["via"] = True, "regraded"
                changed.append(f"{r['id']} #{r['rep']}")
        rows.append(r)
    base = path[:-len(".jsonl")]
    with open(base + "-regraded.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    md = summarize(rows, rows[0].get("model", base.split("/")[-1]), [])
    md += "\n재채점으로 바뀐 실행: " + (", ".join(changed) or "없음") + "\n"
    with open(base + "-regraded.md", "w", encoding="utf-8") as f:
        f.write(md)
    print(md)


if __name__ == "__main__":
    main()
