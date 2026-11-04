"""문서 검색 재현율 측정 (2a-4): eval/doc_retrieval.yaml의 질문을 search_docs로 돌려 정답 조각의 순위를 본다. LLM 없음.

사용법: DATABASE_URL=… TCN_USER_PASSWORD=… python -m inhouse.eval.retrieval [--k 5]
"""
from __future__ import annotations

import argparse
import os
import sys

import psycopg
import yaml

from ..agent.audit import MemoryAudit
from ..agent.tools import Toolbox


def evaluate(items, dsn: str, password: str, k: int = 5, depth: int = 10) -> list[dict]:
    with psycopg.connect(dsn) as admin:
        all_chunks = {r[0] for r in admin.execute("SELECT chunk_id FROM doc_chunk")}
    out = []
    for it in items:
        tb = Toolbox(dsn, password, it["db_user"], MemoryAudit())
        missing = [c for c in it["relevant"] if c not in all_chunks]
        if missing:
            raise ValueError(f"{it['id']}: 없는 조각 {missing}")
        with tb._connect() as conn:
            visible = {r[0] for r in conn.execute("SELECT chunk_id FROM doc_chunk")}
        hidden = [c for c in it["relevant"] if c not in visible]
        if hidden:
            raise ValueError(f"{it['id']}: {it['db_user']}에게 보이지 않는 정답 조각 {hidden}")
        got = [h["chunk_id"] for h in tb.search_docs(it["query"], k=depth).get("results", [])]
        rank = next((i for i, c in enumerate(got, 1) if c in it["relevant"]), None)
        out.append({**it, "top": got[:k], "rank": rank, "hit": rank is not None and rank <= k})
    return out


def report(rows, k: int) -> str:
    def rate(rs):
        return f"{sum(r['hit'] for r in rs)}/{len(rs)} ({100 * sum(r['hit'] for r in rs) / max(len(rs), 1):.0f}%)"
    mrr = sum(1 / r["rank"] for r in rows if r["rank"]) / len(rows)
    lines = [f"- 재현율@{k}: **{rate(rows)}**, MRR@10 {mrr:.2f}"]
    for kind in ("direct", "paraphrase"):
        rs = [r for r in rows if r["kind"] == kind]
        lines.append(f"  - {kind}: {rate(rs)}")
    misses = [r for r in rows if not r["hit"]]
    if misses:
        lines.append("\n| 놓친 질문 | 정답 조각 | 정답 순위 | 상위 3개 |\n|---|---|---|---|")
        for r in misses:
            lines.append(f"| {r['id']} {r['query']} | {', '.join(r['relevant'])} | {r['rank'] or '10위 밖'} | "
                         f"{', '.join(r['top'][:3])} |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="eval/doc_retrieval.yaml")
    ap.add_argument("--k", type=int, default=5)
    a = ap.parse_args()
    dsn, pw = os.environ.get("DATABASE_URL"), os.environ.get("TCN_USER_PASSWORD")
    if not dsn or not pw:
        sys.exit("DATABASE_URL, TCN_USER_PASSWORD가 필요합니다")
    items = yaml.safe_load(open(a.file, encoding="utf-8"))["items"]
    print(report(evaluate(items, dsn, pw, k=a.k), a.k))


if __name__ == "__main__":
    main()
