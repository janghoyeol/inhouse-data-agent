"""2b-2 환불 제안 도구 + 웹 승인 API 통합 테스트 (TEST_DATABASE_URL 필요). 에이전트는 가짜, DB 함수는 진짜."""
import os
import unittest

from inhouse.agent import refunds
from inhouse.agent.audit import MemoryAudit
from inhouse.agent.tools import Toolbox
from inhouse.data import load, overlay, transform
from tests.fixtures import mini_raw

DSN = os.environ.get("TEST_DATABASE_URL")
PW = "test-user-pw"


@unittest.skipUnless(DSN, "TEST_DATABASE_URL 없음")
class TestRefundTools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        t = overlay.apply(transform.build(mini_raw()), seed=7, test_orders=3, refund_rate=0.5, withdraw_rate=0.34)
        load.load(t, DSN, log=lambda m: None, user_password=PW)
        cls.admin = psycopg.connect(DSN, autocommit=True)
        cls.base = cls.admin.execute("SELECT count(*) FROM rfnd").fetchone()[0]

    @classmethod
    def tearDownClass(cls):
        cls.admin.close()

    def setUp(self):
        self.admin.execute("DELETE FROM rfnd_req")
        self.admin.execute("DELETE FROM rfnd WHERE rfnd_no > %s", (self.base,))

    def box(self, user, allow=True):
        return Toolbox(DSN, PW, user, MemoryAudit(), allow_propose=allow)

    def propose_args(self, **kw):
        return {"ord_no": "o3", "rsn_cd": "DMG", "amt": 215.0,
                "basis": 'POL-REF-001@v2#5 "해당 품목 판매가 전액 + 해당 품목 배송비" · q-20261104-0000abcd', **kw}

    # ---- 제안 도구 -------------------------------------------------------------
    def test_tool_proposes_and_audits(self):
        tb = self.box("u_e201")
        tb.question = "o3 파손 환불 제안해 줘"
        r = tb.call("propose_refund", self.propose_args())
        self.assertEqual(r["status"], "pending")
        self.assertIn("승인", r["note"])
        rec = tb.audit.records[-1]
        self.assertEqual((rec["kind"], rec["db_user"], rec["req_no"]), ("refund_propose", "u_e201", r["req_no"]))
        self.assertEqual(self.admin.execute("SELECT count(*) FROM rfnd").fetchone()[0], self.base)

    def test_tool_returns_db_reason_on_refusal(self):
        r = self.box("u_e001").call("propose_refund", self.propose_args())
        self.assertIn("CS 담당·CS 팀장만", r["error"])
        r = self.box("u_e201").call("propose_refund", self.propose_args(amt=215.01))
        self.assertIn("남은 환불 가능 금액", r["error"])
        self.assertEqual(self.box("u_e201").call("propose_refund", {"ord_no": "o3"})["error"][:6], "인자가 빠졌")

    def test_tool_off_when_not_allowed(self):
        tb = self.box("u_e201", allow=False)
        self.assertNotIn("propose_refund", [s["name"] for s in tb.schemas()])
        self.assertIn("꺼져", tb.call("propose_refund", self.propose_args())["error"])
        self.assertIn("propose_refund", [s["name"] for s in self.box("u_e201").schemas()])
        self.assertEqual(self.admin.execute("SELECT count(*) FROM rfnd_req").fetchone()[0], 0)

    # ---- 웹 승인 API ------------------------------------------------------------
    def client(self, approvals=True):
        from fastapi.testclient import TestClient

        from inhouse.web.app import create_app
        from tests.test_web import fake_ask
        return TestClient(create_app(ask_fn=fake_ask, cost_cap_usd=1.0, dsn=DSN, user_password=PW, approvals=approvals))

    def test_approval_api_flow(self):
        req = refunds.propose(DSN, PW, "u_e201", "o1", "WRONG", 10, "POL-REF-001@v2#5")["req_no"]
        c = self.client()
        lst = c.get("/api/requests", params={"db_user": "u_e203"}).json()
        mine = next(x for x in lst["requests"] if x["req_no"] == req)
        self.assertEqual((mine["status"], mine["proposed_by"], mine["uf"]), ("pending", "u_e201", "SP"))
        self.assertEqual(lst["role"], "CS 팀장")
        self.assertNotIn(req, [x["req_no"] for x in c.get("/api/requests", params={"db_user": "u_e105"}).json()["requests"]])
        r = c.post(f"/api/requests/{req}/decide", json={"db_user": "u_e201", "approve": True})
        self.assertEqual(r.status_code, 403)
        self.assertIn("승인 권한이 없습니다", r.json()["detail"])
        r = c.post(f"/api/requests/{req}/decide", json={"db_user": "u_e203", "approve": True, "note": "증빙 확인"})
        self.assertEqual(r.json()["status"], "executed")
        done = next(x for x in c.get("/api/requests", params={"db_user": "u_e203"}).json()["requests"] if x["req_no"] == req)
        self.assertEqual((done["status"], done["decided_by"], done["decision_note"]), ("executed", "u_e203", "증빙 확인"))
        self.assertIsNotNone(done["rfnd_no"])
        self.assertEqual(c.post(f"/api/requests/{req}/decide", json={"db_user": "u_e002", "approve": True}).status_code, 403)

    def test_approval_api_guards(self):
        c = self.client()
        self.assertEqual(c.get("/api/requests", params={"db_user": "postgres"}).status_code, 400)
        self.assertEqual(c.post("/api/requests/1/decide", json={"db_user": "nobody", "approve": True}).status_code, 400)
        off = self.client(approvals=False)
        self.assertEqual(off.get("/api/requests", params={"db_user": "u_e203"}).status_code, 404)
        self.assertEqual(off.post("/api/requests/1/decide", json={"db_user": "u_e203", "approve": True}).status_code, 404)
        self.assertEqual(off.get("/approvals").status_code, 404)
        self.assertIn("승인", self.client().get("/approvals").text)


if __name__ == "__main__":
    unittest.main()
