"""3단계 run_sql 통합 테스트: 직원 계정으로 실행, 읽기 전용·타임아웃·행 상한·쿼리 ID·감사 로그.

TEST_DATABASE_URL(슈퍼유저)이 있을 때만. 적재·권한은 test_security_db와 같은 방식으로 다시 만든다.
"""
import json
import os
import unittest

from inhouse.agent.audit import MemoryAudit
from inhouse.agent.runner import Runner
from inhouse.data import load, overlay, transform
from tests.fixtures import mini_raw

DSN = os.environ.get("TEST_DATABASE_URL")
PW = "test-user-pw"


@unittest.skipUnless(DSN, "TEST_DATABASE_URL 없음")
class TestRunner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tables = overlay.apply(transform.build(mini_raw()), seed=7, test_orders=3, refund_rate=0.5,
                               withdraw_rate=0.34)
        load.load(tables, DSN, log=lambda m: None, user_password=PW)

    def setUp(self):
        self.audit = MemoryAudit()
        self.runner = Runner(DSN, PW, self.audit, max_rows=100, timeout_ms=5000)

    def test_ok_result_has_query_id_and_is_audited(self):
        r = self.runner.run("u_e104", "SELECT DISTINCT uf FROM cust", question="우리 권역 주는?")
        self.assertEqual(r.status, "ok")
        self.assertEqual(r.columns, ["uf"])
        self.assertEqual(r.rows, [["SP"]])  # RLS가 runner를 거쳐도 그대로
        self.assertTrue(r.query_id.startswith("q-"))
        rec = self.audit.records[-1]
        self.assertEqual((rec["query_id"], rec["db_user"], rec["status"], rec["row_count"]),
                         (r.query_id, "u_e104", "ok", 1))
        self.assertEqual(rec["question"], "우리 권역 주는?")
        self.assertIn("executed_at", rec)

    def test_results_are_json_safe(self):
        r = self.runner.run("u_e001", "SELECT sum(sale_amt) s, min(ord_dtm) t, max(est_dlv_dt) d "
                                      "FROM ord_item JOIN ord USING (ord_no)")
        self.assertEqual(r.status, "ok")
        json.dumps(r.to_dict())
        self.assertIsInstance(r.rows[0][0], float)

    def test_row_cap_truncates_without_changing_sql(self):
        r = self.runner.run("u_e001", "SELECT g FROM generate_series(1, 1000) g ORDER BY g DESC")
        self.assertEqual(r.status, "ok")
        self.assertEqual(len(r.rows), 100)
        self.assertTrue(r.truncated)
        self.assertEqual(r.rows[0], [1000])

    def test_row_cap_stops_at_server_not_after_fetching_everything(self):
        # 끝나지 않는 쿼리: 상한까지만 가져오면 바로 끝나고, 다 가져오려 하면 타임아웃
        r = Runner(DSN, PW, self.audit, max_rows=10, timeout_ms=2000).run(
            "u_e001", "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT n FROM r")
        self.assertEqual(r.status, "ok", r.error)
        self.assertEqual((r.row_count, r.truncated), (10, True))
        self.assertLess(r.duration_ms, 1500)

    def test_rejected_sql_is_not_executed_but_audited(self):
        r = self.runner.run("u_e001", "DELETE FROM rfnd")
        self.assertEqual(r.status, "rejected")
        self.assertIn("SELECT", r.error)
        self.assertEqual(self.audit.records[-1]["status"], "rejected")

    def test_db_errors_come_back_to_the_model(self):
        r = self.runner.run("u_e104", "SELECT tel_no FROM cust")
        self.assertEqual(r.status, "error")
        self.assertIn("permission denied", r.error)
        r = self.runner.run("u_e104", "SELECT no_such_col FROM ord")
        self.assertEqual(r.status, "error")
        self.assertIn("no_such_col", r.error)

    def test_timeout(self):
        slow = Runner(DSN, PW, self.audit, max_rows=10, timeout_ms=200)
        r = slow.run("u_e001", "SELECT count(*) FROM generate_series(1, 200000000)")
        self.assertEqual(r.status, "timeout")
        self.assertEqual(self.audit.records[-1]["status"], "timeout")

    def test_runner_sets_read_only_and_timeout_itself(self):
        # 역할 기본값(읽기 전용 on, 5s)과 겹치지 않게 기본값을 끄고 runner 값(3s)으로 확인
        import psycopg
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute("ALTER ROLE u_e104 SET default_transaction_read_only = off")
            admin.execute("ALTER ROLE u_e104 SET statement_timeout = 0")
            try:
                r = Runner(DSN, PW, self.audit, timeout_ms=3000).run(
                    "u_e104", "SELECT current_setting('transaction_read_only'), current_setting('statement_timeout')")
            finally:
                admin.execute("ALTER ROLE u_e104 SET default_transaction_read_only = on")
                admin.execute("ALTER ROLE u_e104 SET statement_timeout = '5s'")
        self.assertEqual(r.rows, [["on", "3s"]])

    def test_unknown_user_fails_closed(self):
        r = self.runner.run("u_nobody", "SELECT 1")
        self.assertEqual(r.status, "error")


if __name__ == "__main__":
    unittest.main()
