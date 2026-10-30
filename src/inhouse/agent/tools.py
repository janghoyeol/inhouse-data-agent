"""에이전트 도구 (4단계): list_tables, describe_table, lookup_metric, run_sql.

모든 도구는 **묻는 직원의 DB 역할로** 동작한다. 그래서 표 목록·열·표본 값도 그 사람의 권한과 RLS를 그대로 따른다.
카탈로그 조회는 도구 코드가 직접 하고(run_sql 검사는 카탈로그를 막는다), 모델이 쓴 SQL은 run_sql만 실행한다.
"""
from __future__ import annotations

from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from . import knowledge
from .runner import Runner

SAMPLE_TYPES = ("text", "character", "smallint", "boolean")
ID_SUFFIXES = ("_no", "_id", "_uid")  # 식별자 열은 표본을 주지 않는다
MAX_DISTINCT = 50  # 표본 2,000행 안의 서로 다른 값이 이보다 많으면 범주형이 아니다

TOOL_SCHEMAS = [
    {
        "name": "list_tables",
        "description": "지금 사용자가 조회할 수 있는 표와 뷰 목록을 설명과 함께 돌려준다. 어떤 표가 있는지 모를 때 가장 먼저 쓴다.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "describe_table",
        "description": "표 하나의 열(이 사용자가 볼 수 있는 열만), 타입, 설명, 주의사항, 외래키, 표본 값을 돌려준다. "
                       "SQL을 쓰기 전에 쓸 표마다 확인한다. 숨겨진 열이 있으면 SELECT *는 권한 오류가 난다.",
        "input_schema": {"type": "object", "properties": {"table": {"type": "string", "description": "표 이름 (예: ord)"}},
                         "required": ["table"]},
    },
    {
        "name": "lookup_metric",
        "description": "매출·고객 수·재구매율 같은 지표의 회사 합의 정의, 규칙, 예시 SQL을 찾는다. "
                       "지표가 들어간 질문이면 SQL을 쓰기 전에 반드시 먼저 찾는다. 모든 질문에 적용되는 공통 규칙도 함께 온다.",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "지표 이름이나 질문 속 표현 (예: 매출)"}},
                         "required": ["query"]},
    },
    {
        "name": "run_sql",
        "description": "PostgreSQL SELECT 한 문장을 지금 사용자 권한으로 실행한다. 결과에 query_id가 붙고, 답의 숫자에는 이 query_id를 출처로 단다. "
                       "집계·비율 계산은 SQL 안에서 끝내고, 결과 행을 보고 다시 계산하지 않는다. 행이 많으면 잘린다(truncated).",
        "input_schema": {"type": "object", "properties": {"sql": {"type": "string", "description": "SELECT 문 한 개"}},
                         "required": ["sql"]},
    },
]


class Toolbox:
    def __init__(self, dsn: str, user_password: str, db_user: str, audit, max_rows: int = 500,
                 timeout_ms: int = 5000):
        self.dsn, self.user_password, self.db_user, self.audit = dsn, user_password, db_user, audit
        self.runner = Runner(dsn, user_password, audit, max_rows=max_rows, timeout_ms=timeout_ms)
        self.question: str | None = None

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            if name == "list_tables":
                return self.list_tables()
            if name == "describe_table":
                return self.describe_table(args["table"])
            if name == "lookup_metric":
                return knowledge.lookup(args["query"])
            if name == "run_sql":
                return self.run_sql(args["sql"], self.question)
        except KeyError as e:
            return {"error": f"인자가 빠졌습니다: {e}"}
        return {"error": f"알 수 없는 도구: {name}"}

    def _connect(self):
        conn = psycopg.connect(make_conninfo(self.dsn, user=self.db_user, password=self.user_password, connect_timeout=5))
        conn.read_only = True
        return conn

    def _visible_tables(self, conn) -> list[tuple[str, str, str | None]]:
        return conn.execute("""
            SELECT c.relname, CASE c.relkind WHEN 'v' THEN 'view' ELSE 'table' END, obj_description(c.oid)
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind IN ('r', 'v')
              AND (has_table_privilege(c.oid, 'SELECT') OR has_any_column_privilege(c.oid, 'SELECT'))
            ORDER BY 1""").fetchall()

    def list_tables(self) -> dict[str, Any]:
        with self._connect() as conn:
            rows = self._visible_tables(conn)
        return {"tables": [{"name": n, "kind": k, "comment": c} for n, k, c in rows]}

    def describe_table(self, table: str) -> dict[str, Any]:
        notes = knowledge.load().get("column_notes", {}).get(table, {})
        with self._connect() as conn:
            visible = {n: (k, c) for n, k, c in self._visible_tables(conn)}
            if table not in visible:
                return {"error": f"'{table}' 표가 없거나 이 사용자에게 보이지 않습니다. list_tables로 확인하세요."}
            cols = conn.execute("""
                SELECT a.attname, format_type(a.atttypid, a.atttypmod), col_description(a.attrelid, a.attnum),
                       has_column_privilege(a.attrelid, a.attnum, 'SELECT')
                FROM pg_attribute a
                WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped
                ORDER BY a.attnum""", (f"public.{table}",)).fetchall()
            fks = [r[0] for r in conn.execute("""
                SELECT pg_get_constraintdef(oid) FROM pg_constraint
                WHERE conrelid = %s::regclass AND contype = 'f' ORDER BY conname""", (f"public.{table}",)).fetchall()]
            conn.execute("SET statement_timeout = 2000")
            columns = []
            for name, typ, comment, ok in cols:
                if not ok:
                    continue
                col = {"name": name, "type": typ, "comment": comment, "note": notes.get(name)}
                if typ.startswith(SAMPLE_TYPES) and not name.endswith(ID_SUFFIXES):
                    # 범주형 열만 표본을 준다. 값이 많으면(자유 텍스트 등) 표본은 토큰만 쓴다
                    q = sql.SQL("SELECT DISTINCT {c} FROM (SELECT {c} FROM {t} WHERE {c} IS NOT NULL LIMIT 2000) s "
                                "ORDER BY 1 LIMIT {n}").format(c=sql.Identifier(name), t=sql.Identifier(table),
                                                               n=sql.Literal(MAX_DISTINCT + 1))
                    values = [r[0] for r in conn.execute(q).fetchall()]
                    if len(values) <= MAX_DISTINCT:
                        col["samples"] = values[:8]
                columns.append(col)
        hidden = sum(1 for *_, ok in cols if not ok)
        out = {"table": table, "kind": visible[table][0], "comment": visible[table][1], "columns": columns,
               "foreign_keys": fks, "hidden_columns": hidden}
        if hidden:
            out["hidden_note"] = f"이 사용자에게 보이지 않는 열이 {hidden}개 있습니다(개인정보). SELECT *는 권한 오류가 나니 열을 골라 쓰세요."
        return out

    def run_sql(self, sql_text: str, question: str | None = None) -> dict[str, Any]:
        return self.runner.run(self.db_user, sql_text, question=question).to_dict()
