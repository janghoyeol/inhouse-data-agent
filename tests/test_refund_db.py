"""2b-1 환불 제안·승인 통합 테스트 (ADR 0004 결정 6). 직원 역할로 접속해 실제로 막히는지 본다.

미니 데이터: o1 = SP(남동부) 배송 완료, 품목 68.90 + 배송비 18.29, 이미 DMG 50.02 환불 → 남은 한도 37.17
             o3 = PR(남부) 배송 완료, 200.00 + 15.00 / o4 = AM 배송 중 / o2 = 취소 / 테스트 주문 3건
"""
import datetime as dt
import os
import unittest
from decimal import Decimal

from inhouse.agent.audit import MemoryAudit
from inhouse.agent.runner import Runner
from inhouse.data import load, overlay, transform
from tests.fixtures import mini_raw

DSN = os.environ.get("TEST_DATABASE_URL")
PW = "test-user-pw"
USERS = ["u_e001", "u_e002", "u_e104", "u_e105", "u_e201", "u_e202", "u_e203"]


@unittest.skipUnless(DSN, "TEST_DATABASE_URL 없음")
class TestRefundFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg.conninfo import make_conninfo
        cls.psycopg = psycopg
        cls.t = overlay.apply(transform.build(mini_raw()), seed=7, test_orders=3, refund_rate=0.5, withdraw_rate=0.34)
        load.load(cls.t, DSN, log=lambda m: None, user_password=PW)
        cls.admin = psycopg.connect(DSN, autocommit=True)
        cls.conns = {u: psycopg.connect(make_conninfo(DSN, user=u, password=PW), autocommit=True) for u in USERS}
        cls.test_ord = cls.admin.execute("SELECT ord_no FROM ord WHERE is_test AND stat_cd = 60 LIMIT 1").fetchone()[0]
        cls.base_rfnd = cls.admin.execute("SELECT count(*) FROM rfnd").fetchone()[0]

    @classmethod
    def tearDownClass(cls):
        for c in cls.conns.values():
            c.close()
        cls.admin.close()

    def setUp(self):
        self.admin.execute("DELETE FROM rfnd_req")
        self.admin.execute("DELETE FROM rfnd WHERE rfnd_no > %s", (self.base_rfnd,))

    # 직원이 자기 계정으로 부르는 함수. 역할 기본값이 읽기 전용이라 쓰기 트랜잭션을 직접 연다 (웹·제안 도구와 같게)
    def call(self, user, sql, params=()):
        c = self.conns[user]
        with c.transaction():
            c.execute("SET TRANSACTION READ WRITE")
            return c.execute(sql, params).fetchone()[0]

    def propose(self, user="u_e201", ord_no="o1", rsn="WRONG", amt="10.00", basis="POL-REF-001@v2#5"):
        return self.call(user, "SELECT propose_refund(%s, %s, %s, %s)", (ord_no, rsn, Decimal(amt), basis))

    def decide(self, user, req, approve=True, note=None):
        return self.call(user, "SELECT decide_refund(%s, %s, %s)", (req, approve, note))

    def fails(self, fn, *a, msg=None, **kw):
        with self.assertRaises(self.psycopg.Error, msg=msg) as cm:
            fn(*a, **kw)
        return str(cm.exception)

    def req(self, req_no):
        return self.admin.execute("SELECT status, proposed_by, decided_by, rfnd_no, expires_at - proposed_at "
                                  "FROM rfnd_req WHERE req_no = %s", (req_no,)).fetchone()

    def n_rfnd(self):
        return self.admin.execute("SELECT count(*) FROM rfnd").fetchone()[0]

    # ---- 제안 ----------------------------------------------------------------
    def test_cs_proposes_pending_request(self):
        r = self.propose()
        status, by, _, rfnd_no, ttl = self.req(r)
        self.assertEqual((status, by, rfnd_no, ttl), ("pending", "u_e201", None, dt.timedelta(hours=72)))
        self.assertEqual(self.n_rfnd(), self.base_rfnd)  # 제안만으로는 환불이 생기지 않는다

    def test_only_cs_can_propose(self):
        self.propose(user="u_e203", amt="5.00")  # CS 팀장도 제안은 할 수 있다 (자기 제안 승인은 못 함)
        for user in ["u_e001", "u_e002", "u_e104"]:
            self.assertIn("제안", self.fails(self.propose, user=user, msg=user))

    def test_proposal_checks(self):
        cases = {"없는 주문": dict(ord_no="nope"), "테스트 주문": dict(ord_no=self.test_ord),
                 "취소 주문": dict(ord_no="o2"), "배송 전": dict(ord_no="o4"), "취소 사유는 시스템만": dict(rsn="CNCL"),
                 "모르는 사유": dict(rsn="XYZ"), "0원": dict(amt="0"), "음수": dict(amt="-1"),
                 "남은 한도 초과": dict(amt="37.18"), "이미 같은 사유로 환불됨": dict(rsn="DMG"),
                 "근거 없음": dict(basis=" ")}
        for name, kw in cases.items():
            self.fails(self.propose, msg=name, **kw)
        self.propose(amt="37.17")  # 남은 한도까지는 된다

    def test_pending_counts_against_ceiling_and_same_reason_once(self):
        self.propose(amt="30.00")
        self.fails(self.propose, rsn="LATE", amt="7.18", msg="대기 중 제안도 한도에 넣는다")
        self.propose(ord_no="o3", rsn="DMG", amt="1.00")
        self.fails(self.propose, ord_no="o3", rsn="DMG", amt="1.00", msg="같은 주문·같은 사유는 한 번")

    # ---- 승인 ----------------------------------------------------------------
    def test_approve_executes_exactly_once(self):
        r = self.propose(amt="12.34")
        self.assertEqual(self.decide("u_e203", r), "executed")
        status, _, by, rfnd_no, _ = self.req(r)
        self.assertEqual((status, by), ("executed", "u_e203"))
        row = self.admin.execute("SELECT ord_no, rfnd_amt, rsn_cd FROM rfnd WHERE rfnd_no = %s", (rfnd_no,)).fetchone()
        self.assertEqual(row, ("o1", Decimal("12.34"), "WRONG"))
        self.fails(self.decide, "u_e203", r, msg="두 번 실행")
        self.fails(self.decide, "u_e002", r, msg="다른 사람도 다시 실행 못 함")
        self.assertEqual(self.n_rfnd(), self.base_rfnd + 1)

    def test_concurrent_approvals_execute_once(self):
        """두 승인권자가 동시에 승인해도 환불은 한 번. 첫 승인이 끝나기 전 두 번째는 기다렸다가 '이미 처리됨'으로 실패한다."""
        import threading
        import time
        r = self.propose(amt="5.00")
        a = self.conns["u_e203"]
        out = {}

        def second():
            try:
                out["b"] = self.decide("u_e002", r)
            except self.psycopg.Error as e:
                out["b"] = f"error: {e}"
        with a.transaction():
            a.execute("SET TRANSACTION READ WRITE")
            self.assertEqual(a.execute("SELECT decide_refund(%s, true, NULL)", (r,)).fetchone()[0], "executed")
            t = threading.Thread(target=second)
            t.start()
            time.sleep(0.5)
            self.assertTrue(t.is_alive(), "두 번째 승인은 첫 승인이 끝날 때까지 기다려야 한다")
        t.join(10)
        self.assertIn("이미 처리된", out["b"])
        self.assertEqual(self.n_rfnd(), self.base_rfnd + 1)

    def test_reject_needs_reason_and_creates_nothing(self):
        r = self.propose()
        self.fails(self.decide, "u_e203", r, approve=False, msg="거절 사유 없음")
        self.assertEqual(self.decide("u_e203", r, approve=False, note="증빙 없음"), "rejected")
        self.assertEqual(self.req(r)[0], "rejected")
        self.assertEqual(self.n_rfnd(), self.base_rfnd)

    def test_no_self_approval(self):
        r = self.propose(user="u_e203", amt="5.00")
        self.assertIn("본인", self.fails(self.decide, "u_e203", r))

    def test_who_can_approve(self):
        r = self.propose()
        for user in ["u_e001", "u_e201", "u_e202"]:   # 이유까지: 한도표에 줄이 없어서가 아니라 승인권자가 아니라서 막혀야 한다
            self.assertIn("승인 권한이 없습니다", self.fails(self.decide, user, r, msg=user))
        self.assertEqual(self.req(r)[0], "pending")

    def test_region_manager_only_own_region(self):
        se, s = self.propose(ord_no="o1"), self.propose(ord_no="o3")
        self.fails(self.decide, "u_e105", se, msg="남부 매니저가 남동부 주문")
        self.assertEqual(self.decide("u_e104", se), "executed")
        self.assertEqual(self.decide("u_e105", s), "executed")
        # 제안 목록도 권역으로 걸러진다
        seen = {r[0] for r in self.conns["u_e105"].execute("SELECT ord_no FROM rfnd_req").fetchall()}
        self.assertEqual(seen, {"o3"})

    def test_limits_by_role_on_approval_date(self):
        self.admin.execute("UPDATE apr_limit SET max_amt = 30 WHERE role_cd = 'CS_LEAD' AND eff_to IS NULL")
        try:
            big, small = self.propose(ord_no="o3", amt="30.01"), self.propose(ord_no="o1", amt="30.00")
            self.assertIn("한도", self.fails(self.decide, "u_e203", big))
            self.assertEqual(self.decide("u_e203", small), "executed")
            self.assertEqual(self.decide("u_e002", big), "executed")  # 본사 운영책임자는 한도 없음
        finally:
            self.admin.execute("UPDATE apr_limit SET max_amt = 500 WHERE role_cd = 'CS_LEAD' AND eff_to IS NULL")
        limits = dict(self.admin.execute("SELECT role_cd, max_amt FROM apr_limit WHERE eff_to IS NULL").fetchall())
        self.assertEqual(limits, {"CS_LEAD": Decimal("500.00"), "RGN_MGR": Decimal("2000.00"), "HQ_OPS": None})

    def test_expired_request(self):
        r = self.propose()
        self.admin.execute("UPDATE rfnd_req SET expires_at = now() - interval '1 minute' WHERE req_no = %s", (r,))
        self.assertEqual(self.decide("u_e203", r), "expired")
        self.assertEqual(self.req(r)[0], "expired")
        self.assertEqual(self.n_rfnd(), self.base_rfnd)

    # ---- 우회 ----------------------------------------------------------------
    def test_no_direct_writes(self):
        r = self.propose()
        for user in ["u_e201", "u_e203", "u_e002"]:
            for sql in ["INSERT INTO rfnd (rfnd_no, ord_no, rfnd_amt, rfnd_dtm, rsn_cd) VALUES (999, 'o1', 1, now(), 'DMG')",
                        "INSERT INTO rfnd_req (ord_no, rsn_cd, amt, basis) VALUES ('o1', 'LATE', 1, 'x')",
                        f"UPDATE rfnd_req SET status = 'approved' WHERE req_no = {r}",
                        "UPDATE apr_limit SET max_amt = NULL", f"DELETE FROM rfnd_req WHERE req_no = {r}"]:
                self.fails(self.call, user, sql + " RETURNING 1", msg=f"{user}: {sql}")
        self.assertEqual(self.req(r)[0], "pending")

    def test_employee_roles_have_no_write_privilege(self):
        # RLS에 쓰기 정책이 없어서 막히는 것과 별개로, 권한 자체가 없어야 한다 (층마다 따로 확인)
        for user in USERS:
            for table in ["rfnd", "rfnd_req", "apr_limit", "ord", "doc"]:
                for priv in ["INSERT", "UPDATE", "DELETE"]:
                    has = self.admin.execute("SELECT has_table_privilege(%s, %s, %s)", (user, table, priv)).fetchone()[0]
                    self.assertFalse(has, f"{user} {priv} {table}")
        for fn in ["propose_refund(text, text, numeric, text)", "decide_refund(bigint, boolean, text)"]:
            self.assertFalse(self.admin.execute("SELECT has_function_privilege('public', %s, 'EXECUTE')", (fn,)).fetchone()[0])

    def test_agent_run_sql_cannot_propose_or_decide(self):
        r = self.propose()
        runner = Runner(DSN, PW, MemoryAudit())
        for sql in [f"SELECT decide_refund({r}, true, NULL)", "SELECT propose_refund('o3', 'DMG', 1, 'x')"]:
            res = runner.run("u_e203", sql)
            self.assertNotEqual(res.status, "ok", sql)
        self.assertEqual(self.req(r)[0], "pending")
        self.assertEqual(self.admin.execute("SELECT count(*) FROM rfnd_req").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
