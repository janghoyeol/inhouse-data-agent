"""Olist 원본 + 합성 덧층을 Postgres에 적재한다. 기존 표는 지우고 새로 만든다.

사용법: python -m inhouse.data.load --raw data/raw [--dsn postgresql://...]  (기본 DSN: DATABASE_URL)
직원 로그인 역할(u_e001 …)의 비밀번호는 TCN_USER_PASSWORD (2단계 권한, security.sql).
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import time
from importlib import resources

import pandas as pd
import psycopg
from psycopg import sql

from . import overlay, transform

# FK 순서
ORDER = ["rgn", "st", "cd_ord_stat", "slr", "ctgr", "prd", "geo_zip", "cust", "ord", "ord_item", "ord_pay",
         "rvw", "rfnd", "emp", "mon_sls_smry_old"]
NULL = r"\N"


def _copy(cur, name: str, df: pd.DataFrame) -> None:
    buf = io.StringIO()
    df.to_csv(buf, index=False, header=False, na_rep=NULL)
    cols = ", ".join(df.columns)
    with cur.copy(f"COPY {name} ({cols}) FROM STDIN WITH (FORMAT csv, NULL '{NULL}')") as cp:
        cp.write(buf.getvalue())


def _sql(name: str) -> str:
    return resources.files("inhouse.data").joinpath(name).read_text(encoding="utf-8")


def load(tables: dict[str, pd.DataFrame], dsn: str, log=print, user_password: str | None = None) -> dict[str, int]:
    missing = set(ORDER) ^ set(tables)
    if missing:
        raise ValueError(f"표 목록이 맞지 않음: {sorted(missing)}")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(_sql("schema.sql"))
        for name in ORDER:
            t0 = time.time()
            _copy(cur, name, tables[name])
            log(f"  {name}: {len(tables[name]):,}행 ({time.time() - t0:.1f}s)")
        cur.execute(_sql("security.sql"))
        if user_password:
            for (role,) in cur.execute("SELECT db_role FROM db_user ORDER BY 1").fetchall():
                cur.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(user_password)))
        log("  권한: 역할·RLS·마스킹 뷰 적용")
        conn.commit()
        conn.autocommit = True
        cur.execute("ANALYZE")
        return {n: cur.execute(f"SELECT count(*) FROM {n}").fetchone()[0] for n in ORDER}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    if not a.dsn:
        sys.exit("DATABASE_URL 또는 --dsn 이 필요합니다")
    t0 = time.time()
    tables = overlay.apply(transform.build(transform.read_raw(a.raw)), seed=a.seed)
    print(f"변환 + 합성 {time.time() - t0:.1f}s")
    password = os.environ.get("TCN_USER_PASSWORD")
    if not password:
        print("TCN_USER_PASSWORD 없음: 직원 역할에 비밀번호를 안 걸어서 비밀번호 인증으로는 접속할 수 없습니다")
    load(tables, a.dsn, user_password=password)
    print(f"완료 {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
