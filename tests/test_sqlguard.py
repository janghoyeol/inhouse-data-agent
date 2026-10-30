"""3단계 SQL 검사 오프라인 테스트. 검사는 방어선이 아니라 모델에게 '왜 안 되는지'를 알려주는 층이다
(진짜 방어선은 DB 권한·읽기 전용 트랜잭션, tests/test_security_db.py, tests/test_runner_db.py)."""
import unittest

from inhouse.agent.sqlguard import check

ALLOWED = [
    "SELECT 1",
    "select count(*) from ord where not is_test",
    "WITH m AS (SELECT ord_no, sum(sale_amt) s FROM ord_item GROUP BY 1) SELECT avg(s) FROM m",
    "SELECT uf FROM cust UNION SELECT uf FROM slr",
    "SELECT uf, count(*) FILTER (WHERE stat_cd = 60) OVER (PARTITION BY uf) FROM cust JOIN ord USING (cust_no)",
    "SELECT (ord_dtm AT TIME ZONE 'America/Sao_Paulo')::date d, count(*) FROM ord GROUP BY 1 ORDER BY 1 DESC LIMIT 7",
    "SELECT * FROM v_cust_contact WHERE uf = 'SP'",
    "SELECT current_setting('statement_timeout')",
    "SELECT o.ord_no FROM ord o WHERE EXISTS (SELECT 1 FROM rfnd r WHERE r.ord_no = o.ord_no)",
    "  SELECT 1 ;  ",  # 끝 세미콜론 하나는 허용
]

REJECTED = {
    "": "비어",
    "SELECT 1; SELECT 2": "한 문장",
    "SELECT 1; DROP TABLE ord": "한 문장",
    "INSERT INTO rgn VALUES ('X','x','x')": "SELECT",
    "UPDATE ord SET is_test = true": "SELECT",
    "DELETE FROM rfnd": "SELECT",
    "TRUNCATE rvw": "SELECT",
    "DROP TABLE prd": "SELECT",
    "CREATE TABLE t AS SELECT 1": "SELECT",
    "ALTER TABLE ord ADD COLUMN x int": "SELECT",
    "GRANT SELECT ON emp TO r_agent": "SELECT",
    "COPY ord TO '/tmp/x'": "SELECT",
    "SET ROLE postgres": "SELECT",
    "RESET ROLE": "SELECT",
    "SET statement_timeout = 0": "SELECT",
    "BEGIN": "SELECT",
    "VACUUM": "SELECT",
    "EXPLAIN ANALYZE SELECT 1": "SELECT",
    "CALL p()": "SELECT",
    "DO $$ BEGIN END $$": "SELECT",
    "LISTEN x": "SELECT",
    "VALUES (1)": "SELECT",
    "WITH d AS (DELETE FROM rfnd RETURNING *) SELECT * FROM d": "DELETE",
    "SELECT * INTO t FROM ord": "INTO",
    "SELECT * FROM ord FOR UPDATE": "FOR UPDATE",
    "SELECT set_config('role', 'postgres', false)": "set_config",
    "SELECT pg_catalog.set_config('role', 'postgres', false)": "set_config",
    "SELECT PG_SLEEP(10)": "pg_sleep",
    "SELECT pg_read_file('/etc/passwd')": "pg_read_file",
    "SELECT dblink('host=x', 'select 1')": "dblink",
    "SELECT query_to_xml('select * from emp', true, true, '')": "query_to_xml",
    "SELECT lo_import('/etc/passwd')": "lo_import",
    "SELECT nextval('s')": "nextval",
    "SELECT * FROM pg_stats": "시스템 카탈로그",
    "SELECT * FROM pg_catalog.pg_class": "시스템 카탈로그",
    "SELECT * FROM information_schema.tables": "시스템 카탈로그",
    "SELECT ((( FROM": "해석",
}


class TestSqlGuard(unittest.TestCase):
    def test_allowed(self):
        for sql in ALLOWED:
            r = check(sql)
            self.assertTrue(r.ok, f"{sql!r}: {r.reason}")

    def test_rejected_with_reason_the_model_can_act_on(self):
        for sql, hint in REJECTED.items():
            r = check(sql)
            self.assertFalse(r.ok, sql)
            self.assertIn(hint.lower(), r.reason.lower(), f"{sql!r}: {r.reason}")

    def test_normalized_sql_is_single_statement_without_trailing_semicolon(self):
        self.assertEqual(check("  SELECT 1 ;  ").sql, "SELECT 1")


if __name__ == "__main__":
    unittest.main()
