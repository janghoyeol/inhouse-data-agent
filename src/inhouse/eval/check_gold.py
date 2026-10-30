"""정답 SQL을 문항의 직원 역할로 실행해 결과를 보여준다. 정답을 쓰면서 확인하는 용도.

사용법: python -m inhouse.eval.check_gold [A01,C03 …]   (없으면 정답이 있는 문항 전부)
환경변수: DATABASE_URL, TCN_USER_PASSWORD
"""
from __future__ import annotations

import os
import sys

from ..agent.audit import MemoryAudit
from ..agent.runner import Runner
from .run import load_questions


def main() -> None:
    dsn, pw = os.environ.get("DATABASE_URL"), os.environ.get("TCN_USER_PASSWORD")
    if not (dsn and pw):
        sys.exit("DATABASE_URL, TCN_USER_PASSWORD가 필요합니다")
    items = load_questions(os.environ.get("QUESTIONS", "eval/questions.yaml"))
    wanted = set(sys.argv[1].split(",")) if len(sys.argv) > 1 else None
    runner = Runner(dsn, pw, MemoryAudit(), max_rows=20, timeout_ms=30000)
    missing = []
    for q in items:
        if wanted and q["id"] not in wanted:
            continue
        if q["expect"] != "result":
            continue
        if not q.get("gold_sql"):
            missing.append(q["id"])
            continue
        r = runner.run(q["db_user"], q["gold_sql"])
        print(f"\n[{q['id']}] {q['question']}  ({q['db_user']})")
        if r.status != "ok":
            print(f"  {r.status}: {r.error}")
            continue
        print(f"  열: {r.columns}  행: {r.row_count}{' (잘림)' if r.truncated else ''}  {r.duration_ms}ms")
        for row in r.rows[:10]:
            print(f"  {row}")
    if missing:
        print(f"\n정답 SQL이 아직 없는 문항 {len(missing)}개: {', '.join(missing)}")


if __name__ == "__main__":
    main()
