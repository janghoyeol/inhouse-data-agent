"""에이전트 도구 (4단계): list_tables, describe_table, lookup_metric, run_sql + (2단계) search_docs, read_doc.

모든 도구는 **묻는 직원의 DB 역할로** 동작한다. 그래서 표 목록·열·표본 값도 그 사람의 권한과 RLS를 그대로 따른다.
카탈로그 조회는 도구 코드가 직접 하고(run_sql 검사는 카탈로그를 막는다), 모델이 쓴 SQL은 run_sql만 실행한다.
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from ..data import docs as docs_mod
from . import knowledge
from .runner import Runner

SAMPLE_TYPES = ("text", "character", "smallint", "boolean")
ID_SUFFIXES = ("_no", "_id", "_uid")  # 식별자 열은 표본을 주지 않는다
MAX_DISTINCT = 50  # 표본 2,000행 안의 서로 다른 값이 이보다 많으면 범주형이 아니다
MAX_QUERY = 500
DOC_COLS = "d.doc_id, d.version, d.title, d.status, d.eff_from, d.eff_to, d.approval_no"

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
    {
        "name": "search_docs",
        "description": "사내 문서(규정·지침·공지·회의록)를 지금 사용자가 볼 수 있는 범위 안에서 키워드로 찾아 관련 조각을 돌려준다. "
                       "조각마다 문서 번호·판·상태(active 시행 중/superseded 대체됨/draft 초안)·시행 기간·승인 번호가 붙는다. "
                       "같은 문서의 여러 판이 함께 나올 수 있으니 상태와 시행 기간을 보고 고른다. 규정·절차·기한 질문이면 먼저 쓴다.",
        "input_schema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "찾을 내용 (예: 환불 신청 기한)"},
            "k": {"type": "integer", "description": "돌려받을 조각 수 (기본 5, 최대 10)"}}, "required": ["query"]},
    },
    {
        "name": "read_doc",
        "description": "문서 한 판의 모든 절을 읽는다. doc_key는 search_docs 결과의 '문서번호@v판' (예: POL-REF-001@v2). "
                       "조각 하나로 부족할 때(표, 적용 대상, 예외) 쓴다.",
        "input_schema": {"type": "object", "properties": {"doc_key": {"type": "string"}}, "required": ["doc_key"]},
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
            if name == "search_docs":
                return self.search_docs(args["query"], int(args.get("k") or 5))
            if name == "read_doc":
                return self.read_doc(args["doc_key"])
        except KeyError as e:
            return {"error": f"인자가 빠졌습니다: {e}"}
        except psycopg.Error as e:  # 문서 표가 아직 없는 DB 등. 모델에게 이유를 돌려주고 대화는 이어간다
            return {"error": f"DB 오류: {str(e).splitlines()[0]}"}
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

    # ---- 2단계 사내 문서 (ADR 0004) ----------------------------------------------------
    def search_docs(self, query: str, k: int = 5) -> dict[str, Any]:
        """키워드 검색. 직원 역할로 접속하므로 보이지 않는 문서는 RLS가 거른다 (앱이 거르지 않는다)."""
        toks = list(dict.fromkeys(docs_mod.tokens((query or "")[:MAX_QUERY])))
        if not toks:
            return {"error": "검색어가 비었습니다. 찾을 낱말을 써 주세요."}
        k = max(1, min(int(k), 10))
        tsq = " | ".join("'" + t.replace("'", "''") + "'" for t in toks)
        now = dt.datetime.now(dt.timezone.utc)
        search_id = f"s-{now:%Y%m%d}-{uuid.uuid4().hex[:8]}"
        with self._connect() as conn:
            conn.execute("SET statement_timeout = 3000")
            rows = conn.execute(f"""
                SELECT c.chunk_id, {DOC_COLS}, c.heading, c.content, round(ts_rank(c.tsv, q)::numeric, 4) AS score
                FROM doc_chunk c JOIN doc d USING (doc_key), to_tsquery('simple', %s) q
                WHERE c.tsv @@ q
                ORDER BY score DESC, c.chunk_id
                LIMIT %s""", (tsq, k)).fetchall()
        cols = ["chunk_id", "doc_id", "version", "title", "status", "eff_from", "eff_to", "approval_no", "heading",
                "content", "score"]
        results = [dict(zip(cols, r)) for r in rows]
        for r in results:
            r["eff_from"], r["eff_to"] = str(r["eff_from"]), str(r["eff_to"]) if r["eff_to"] else None
            r["score"] = float(r["score"])
        self.audit.write({"kind": "doc_search", "search_id": search_id, "executed_at": now.isoformat(timespec="seconds"),
                          "db_user": self.db_user, "question": self.question, "query": query,
                          "chunk_ids": [r["chunk_id"] for r in results]})
        out: dict[str, Any] = {"search_id": search_id, "results": results}
        if not results:
            out["note"] = "맞는 문서가 없거나 이 사용자에게 보이지 않습니다. 다른 낱말로 다시 찾아 보세요."
        return out

    def read_doc(self, doc_key: str) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute("SET statement_timeout = 3000")
            d = conn.execute(f"SELECT {DOC_COLS}, d.owner, d.supersedes FROM doc d WHERE d.doc_key = %s",
                             (doc_key,)).fetchone()
            if d is None:
                return {"error": f"문서 '{doc_key}'가 없거나 이 사용자에게 보이지 않습니다."}
            chunks = conn.execute("SELECT chunk_id, heading, content FROM doc_chunk WHERE doc_key = %s ORDER BY sec_no",
                                  (doc_key,)).fetchall()
        keys = ["doc_id", "version", "title", "status", "eff_from", "eff_to", "approval_no", "owner", "supersedes"]
        meta = dict(zip(keys, d))
        meta["eff_from"], meta["eff_to"] = str(meta["eff_from"]), str(meta["eff_to"]) if meta["eff_to"] else None
        self.audit.write({"kind": "doc_read", "executed_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                          "db_user": self.db_user, "question": self.question, "doc_key": doc_key})
        return {"doc": meta, "chunks": [{"chunk_id": c, "heading": h, "content": t,
                                         **{k: meta[k] for k in ("doc_id", "version", "status", "approval_no")}}
                                        for c, h, t in chunks]}
