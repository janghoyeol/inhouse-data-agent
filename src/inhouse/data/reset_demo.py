"""공개 데모 초기화 (2b-4, 매일): 방문자가 만든 환불 제안과, 승인으로 생긴 환불 기록만 지운다. 원래 데이터는 건드리지 않는다.

사용법: DATABASE_URL=<관리자 연결> python -m inhouse.data.reset_demo
"""
from __future__ import annotations

import os
import sys

import psycopg


def reset(dsn: str) -> dict[str, int]:
    with psycopg.connect(dsn) as conn, conn.transaction():
        made = [r[0] for r in conn.execute("DELETE FROM rfnd_req RETURNING rfnd_no").fetchall()]
        ids = [n for n in made if n is not None]
        gone = conn.execute("DELETE FROM rfnd WHERE rfnd_no = ANY(%s)", (ids,)).rowcount if ids else 0
        conn.execute("SELECT setval('rfnd_no_seq', (SELECT coalesce(max(rfnd_no), 0) + 1 FROM rfnd), false)")
        return {"requests": len(made), "refunds": gone}


def main() -> None:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("DATABASE_URL이 필요합니다")
    r = reset(dsn)
    print(f"초기화: 제안 {r['requests']}건, 승인으로 생긴 환불 {r['refunds']}건 삭제")


if __name__ == "__main__":
    main()
