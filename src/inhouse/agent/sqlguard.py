"""모델이 쓴 SQL을 실행 전에 검사한다 (3단계).

이 층은 방어선이 아니다. 진짜 방어선은 DB(직원 역할 권한·RLS·읽기 전용 트랜잭션, ADR 0002)이고,
여기서는 실행해 봤자 거절될 SQL을 미리 걸러 **모델이 고쳐 쓸 수 있는 이유**를 돌려준다.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

logging.getLogger("sqlglot").setLevel(logging.ERROR)  # 모르는 구문을 Command로 읽을 때의 경고

ROOTS = (exp.Select, exp.SetOperation)  # SELECT, UNION/INTERSECT/EXCEPT
WRITES = {exp.Insert: "INSERT", exp.Update: "UPDATE", exp.Delete: "DELETE", exp.Merge: "MERGE",
          exp.Create: "CREATE", exp.Drop: "DROP", exp.Alter: "ALTER", exp.TruncateTable: "TRUNCATE",
          exp.Copy: "COPY", exp.Grant: "GRANT", exp.Revoke: "REVOKE", exp.Command: "명령"}
# 세션·권한을 바꾸거나, 파일·외부에 닿거나, 문자열로 다른 쿼리를 실행하거나, 시간을 끄는 함수
DENIED_FUNC_PREFIXES = ("pg_", "set_config", "dblink", "lo_", "query_to_", "table_to_", "schema_to_",
                        "database_to_", "cursor_to_", "nextval", "setval", "currval", "txid_")
CATALOG_SCHEMAS = {"pg_catalog", "information_schema"}


@dataclass(frozen=True)
class Check:
    ok: bool
    sql: str = ""
    reason: str = ""


def _func_name(f: exp.Func) -> str:
    return (f.name if isinstance(f, exp.Anonymous) else f.sql_name()).lower()


def check(sql: str) -> Check:
    text = re.sub(r"[\s;]+$", "", sql or "").strip()
    if not text:
        return Check(False, reason="SQL이 비어 있습니다.")
    try:
        statements = [s for s in sqlglot.parse(text, read="postgres") if s is not None]
    except sqlglot.errors.SqlglotError as e:
        return Check(False, reason=f"SQL을 해석하지 못했습니다: {str(e).splitlines()[0]}")
    if len(statements) != 1:
        return Check(False, reason="한 번에 한 문장만 실행할 수 있습니다. 여러 결과가 필요하면 나눠서 실행하세요.")
    tree = statements[0]
    if not isinstance(tree, ROOTS):
        return Check(False, reason=f"SELECT(조회) 문만 실행할 수 있습니다. 받은 문장: {type(tree).__name__}")

    for node in tree.walk():
        for kind, label in WRITES.items():
            if isinstance(node, kind):
                return Check(False, reason=f"조회 안에서 데이터·권한을 바꾸는 구문({label})은 쓸 수 없습니다.")
        if isinstance(node, exp.Into):
            return Check(False, reason="SELECT INTO(새 표 만들기)는 쓸 수 없습니다. 결과는 그냥 SELECT로 받으세요.")
        if isinstance(node, exp.Lock):
            return Check(False, reason="FOR UPDATE/SHARE(행 잠금)는 쓸 수 없습니다.")
        if isinstance(node, exp.Func):
            name = _func_name(node)
            if name.startswith(DENIED_FUNC_PREFIXES):
                return Check(False, reason=f"함수 {name}()는 쓸 수 없습니다 (세션·권한·파일·외부 접근 함수).")
        if isinstance(node, exp.Table):
            if node.db.lower() in CATALOG_SCHEMAS or node.name.lower().startswith("pg_"):
                return Check(False, reason="시스템 카탈로그(pg_*, information_schema)는 조회할 수 없습니다. "
                                           "표와 열은 탐색 도구(list_tables, describe_table)로 확인하세요.")
    return Check(True, sql=text)
