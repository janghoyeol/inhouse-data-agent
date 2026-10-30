"""run_sql: 모델이 쓴 SQL을 **묻는 직원의 DB 역할로** 실행한다 (3단계).

겹겹이:
1. sqlguard 검사 (거절 이유를 모델에게)
2. 직원 역할로 접속 → 권한·RLS는 DB가 (ADR 0002)
3. 읽기 전용 트랜잭션 + SET LOCAL statement_timeout
4. 서버 쪽 커서로 상한+1행만 가져옴 → SQL을 고치지 않고(LIMIT을 덧붙이지 않고) 결과 크기를 막는다
5. 거절·오류·타임아웃까지 전부 감사 로그, 결과마다 쿼리 ID
"""
from __future__ import annotations

import datetime as dt
import time
import uuid
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.conninfo import make_conninfo

from .sqlguard import check


@dataclass
class SqlResult:
    query_id: str
    status: str  # ok | rejected | error | timeout
    executed_at: str
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    duration_ms: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _json_value(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, (bytes, memoryview)):
        return bytes(v).hex()
    return v


class Runner:
    def __init__(self, dsn: str, user_password: str, audit, max_rows: int = 500, timeout_ms: int = 5000):
        self.dsn = dsn
        self.user_password = user_password
        self.audit = audit
        self.max_rows = max_rows
        self.timeout_ms = timeout_ms

    def run(self, db_user: str, sql: str, question: str | None = None) -> SqlResult:
        now = dt.datetime.now(dt.timezone.utc)
        result = SqlResult(query_id=f"q-{now:%Y%m%d}-{uuid.uuid4().hex[:8]}", status="ok",
                           executed_at=now.isoformat(timespec="seconds"))
        t0 = time.monotonic()
        checked = check(sql)
        if not checked.ok:
            result.status, result.error = "rejected", checked.reason
        else:
            self._execute(db_user, checked.sql, result)
        result.duration_ms = round((time.monotonic() - t0) * 1000)
        self.audit.write({
            "query_id": result.query_id, "executed_at": result.executed_at, "db_user": db_user,
            "question": question, "sql": sql, "status": result.status, "error": result.error,
            "row_count": result.row_count, "truncated": result.truncated, "duration_ms": result.duration_ms,
        })
        return result

    def _execute(self, db_user: str, sql: str, result: SqlResult) -> None:
        conninfo = make_conninfo(self.dsn, user=db_user, password=self.user_password, connect_timeout=5)
        try:
            with psycopg.connect(conninfo) as conn:
                conn.read_only = True
                with conn.transaction():
                    conn.execute(f"SET LOCAL statement_timeout = {int(self.timeout_ms)}")
                    with conn.cursor(name="run_sql") as cur:
                        cur.execute(sql)
                        rows = cur.fetchmany(self.max_rows + 1)
                        result.columns = [d.name for d in cur.description or []]
        except psycopg.errors.QueryCanceled:
            result.status, result.error = "timeout", f"{self.timeout_ms}ms 안에 끝나지 않아 중단했습니다. 범위를 좁히세요."
            return
        except psycopg.Error as e:
            result.status, result.error = "error", (getattr(e.diag, "message_primary", None) or str(e)).strip()
            return
        result.truncated = len(rows) > self.max_rows
        result.rows = [[_json_value(v) for v in row] for row in rows[: self.max_rows]]
        result.row_count = len(result.rows)
