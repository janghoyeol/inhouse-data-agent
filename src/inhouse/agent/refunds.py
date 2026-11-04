"""환불 제안·승인 (2단계 2b, ADR 0004 결정 6): DB 함수 propose_refund·decide_refund를 직원 역할로 부르는 얇은 층.

규칙 검사는 전부 DB 함수가 한다. 여기서는 쓰기 트랜잭션을 열고(역할 기본값이 읽기 전용) DB가 돌려준 거절 이유를 그대로 전한다.
에이전트 도구(propose)와 웹 승인 화면(list_requests·decide)이 같이 쓴다. 에이전트에게 decide는 없다.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import psycopg
from psycopg.conninfo import make_conninfo


class RefundError(Exception):
    """DB 함수가 거절한 이유 (한국어 메시지)."""


def _connect(dsn: str, password: str, db_user: str):
    return psycopg.connect(make_conninfo(dsn, user=db_user, password=password, connect_timeout=5))


def _reason(e: psycopg.Error) -> str:
    diag = getattr(e, "diag", None)
    return (diag.message_primary if diag and diag.message_primary else str(e).splitlines()[0])


def _write(dsn: str, password: str, db_user: str, sql: str, params: tuple) -> Any:
    try:
        with _connect(dsn, password, db_user) as conn, conn.transaction():
            conn.execute("SET TRANSACTION READ WRITE")
            conn.execute("SET LOCAL statement_timeout = 5000")
            return conn.execute(sql, params).fetchone()[0]
    except psycopg.Error as e:
        raise RefundError(_reason(e)) from None


def propose(dsn: str, password: str, db_user: str, ord_no: str, rsn_cd: str, amt, basis: str) -> dict[str, Any]:
    req = _write(dsn, password, db_user, "SELECT propose_refund(%s, %s, %s, %s)",
                 (ord_no, rsn_cd, Decimal(str(amt)), basis))
    with _connect(dsn, password, db_user) as conn:
        exp = conn.execute("SELECT expires_at FROM rfnd_req WHERE req_no = %s", (req,)).fetchone()[0]
    return {"req_no": req, "status": "pending", "expires_at": exp.isoformat(timespec="minutes")}


def decide(dsn: str, password: str, db_user: str, req_no: int, approve: bool, note: str | None = None) -> dict[str, Any]:
    status = _write(dsn, password, db_user, "SELECT decide_refund(%s, %s, %s)", (req_no, approve, note))
    return {"req_no": req_no, "status": status}


def list_requests(dsn: str, password: str, db_user: str, limit: int = 50) -> list[dict[str, Any]]:
    """그 직원에게 보이는 제안 (RLS: 권역 매니저는 자기 권역 고객 주문만). 대기 중 먼저, 최근 순."""
    cols = ["req_no", "ord_no", "uf", "rsn_cd", "amt", "basis", "status", "proposed_by", "proposed_at", "expires_at",
            "decided_by", "decided_at", "decision_note", "rfnd_no", "order_total"]
    with _connect(dsn, password, db_user) as conn:
        conn.read_only = True
        rows = conn.execute("""
            SELECT r.req_no, r.ord_no, c.uf, r.rsn_cd, r.amt, r.basis, r.status, r.proposed_by, r.proposed_at,
                   r.expires_at, r.decided_by, r.decided_at, r.decision_note, r.rfnd_no,
                   (SELECT sum(i.sale_amt + i.dlv_fee) FROM ord_item i WHERE i.ord_no = r.ord_no)
            FROM rfnd_req r JOIN ord o USING (ord_no) JOIN cust c USING (cust_no)
            ORDER BY r.status <> 'pending', r.req_no DESC
            LIMIT %s""", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(zip(cols, r))
        for k in ("proposed_at", "expires_at", "decided_at"):
            d[k] = d[k].isoformat(timespec="minutes") if d[k] else None
        d["amt"], d["order_total"] = float(d["amt"]), float(d["order_total"] or 0)
        out.append(d)
    return out
