"""6단계 웹 화면 오프라인 테스트: 가짜 에이전트를 넣어 DB·API 키 없이 돈다."""
import datetime as dt
import unittest

from fastapi.testclient import TestClient

from inhouse.agent.agent import Answer, ToolCall
from inhouse.web.app import CostGuard, RateLimit, create_app


def fake_ask(db_user, question):
    a = Answer(question=question, db_user=db_user, model="claude-haiku-5-5", text="812건입니다 [q-20261104-0000abcd].",
               cited=["q-20261104-0000abcd"], turns=2, cost_usd=0.001, seconds=1.2, stop_reason="end_turn")
    a.calls = [ToolCall("lookup_metric", {"query": "주문 수"}, {"metric": {}}),
               ToolCall("run_sql", {"sql": "SELECT 812"},
                        {"query_id": "q-20261104-0000abcd", "status": "ok", "columns": ["n"],
                         "rows": [[i] for i in range(30)], "row_count": 30, "truncated": False, "error": ""})]
    return a


class TestWeb(unittest.TestCase):
    def client(self, cap=1.0):
        return TestClient(create_app(ask_fn=fake_ask, cost_cap_usd=cap))

    def test_index_and_personas(self):
        c = self.client()
        self.assertIn("Tucanova 데이터 에이전트", c.get("/").text)
        ps = c.get("/api/personas").json()
        self.assertEqual({p["db_user"] for p in ps},
                         {"u_e001", "u_e101", "u_e102", "u_e103", "u_e104", "u_e105", "u_e201", "u_e202"})
        self.assertEqual(next(p for p in ps if p["db_user"] == "u_e104")["region"], "남동부")

    def test_ask_returns_answer_with_sql_sources(self):
        d = self.client().post("/api/ask", json={"db_user": "u_e104", "question": "블프 주문 수?"}).json()
        self.assertEqual(d["cited"], ["q-20261104-0000abcd"])
        sql = [c for c in d["calls"] if c["tool"] == "run_sql"][0]
        self.assertEqual(sql["sql"], "SELECT 812")
        self.assertEqual(len(sql["rows"]), 20)  # 화면에는 20행까지만
        self.assertTrue(sql["truncated"])

    def test_doc_calls_and_citations_are_returned(self):
        def doc_ask(db_user, question):
            a = fake_ask(db_user, question)
            hit = {"chunk_id": "POL-REF-001@v2#3", "doc_id": "POL-REF-001", "version": 2, "title": "환불·반품 규정",
                   "status": "active", "eff_from": "2018-01-01", "eff_to": None, "approval_no": None,
                   "heading": "3. 신청 기한", "content": "15일 이내", "score": 0.3}
            a.calls.append(ToolCall("search_docs", {"query": "기한"}, {"search_id": "s-1", "results": [hit]}))
            a.doc_citations = [{"chunk_id": "POL-REF-001@v2#3", "quote": "15일 이내", "retrieved": True,
                                "quote_found": True, "warnings": []}]
            return a
        d = TestClient(create_app(ask_fn=doc_ask, cost_cap_usd=1.0)).post(
            "/api/ask", json={"db_user": "u_e201", "question": "기한?"}).json()
        call = next(c for c in d["calls"] if c["tool"] == "search_docs")
        self.assertEqual(call["chunks"][0]["heading"], "3. 신청 기한")
        self.assertNotIn("score", call["chunks"][0])
        self.assertTrue(d["doc_citations"][0]["quote_found"])

    def test_only_known_employees(self):
        c = self.client()
        for user in ["postgres", "u_stranger", "r_agent", ""]:
            self.assertEqual(c.post("/api/ask", json={"db_user": user, "question": "x"}).status_code, 400, user)

    def test_question_length(self):
        c = self.client()
        self.assertEqual(c.post("/api/ask", json={"db_user": "u_e001", "question": "   "}).status_code, 400)
        self.assertEqual(c.post("/api/ask", json={"db_user": "u_e001", "question": "가" * 501}).status_code, 400)

    def test_daily_cost_cap(self):
        c = self.client(cap=0.0025)
        codes = [c.post("/api/ask", json={"db_user": "u_e001", "question": "q"}).status_code for _ in range(4)]
        self.assertEqual(codes, [200, 200, 200, 429])  # 0.001씩 쓰다가 0.003 ≥ 0.0025에서 막힘

    def test_cost_guard_resets_next_day(self):
        now = [dt.datetime(2026, 11, 4, 23, 0)]
        g = CostGuard(0.01, clock=lambda: now[0])
        g.add(0.02)
        self.assertFalse(g.allow())
        now[0] = dt.datetime(2026, 11, 5, 0, 1)
        self.assertTrue(g.allow())

    def test_rate_limit_per_ip(self):
        c = TestClient(create_app(ask_fn=fake_ask, cost_cap_usd=1.0, rate_limit=RateLimit(per_minute=2, per_day=100)))
        ask = lambda ip: c.post("/api/ask", json={"db_user": "u_e001", "question": "q"},
                                headers={"x-forwarded-for": f"{ip}, 10.0.0.1"}).status_code
        self.assertEqual([ask("1.1.1.1") for _ in range(3)], [200, 200, 429])
        self.assertEqual(ask("2.2.2.2"), 200)  # 다른 IP는 따로 센다

    def test_rate_limit_window_and_day(self):
        now = [dt.datetime(2026, 11, 4, 12, 0, 0)]
        r = RateLimit(per_minute=1, per_day=2, clock=lambda: now[0])
        self.assertTrue(r.allow("a"))
        self.assertFalse(r.allow("a"))
        now[0] += dt.timedelta(seconds=61)
        self.assertTrue(r.allow("a"))
        now[0] += dt.timedelta(seconds=61)
        self.assertFalse(r.allow("a"))  # 하루 2회 다 씀
        now[0] = dt.datetime(2026, 11, 5, 0, 0, 1)
        self.assertTrue(r.allow("a"))

    def test_agent_error_is_502_with_reason(self):
        class AuthenticationError(Exception):
            pass

        def broken(db_user, question):
            raise AuthenticationError("bad key")
        c = TestClient(create_app(ask_fn=broken, cost_cap_usd=1.0))
        r = c.post("/api/ask", json={"db_user": "u_e001", "question": "q"})
        self.assertEqual(r.status_code, 502)
        self.assertIn("인증", r.json()["detail"])

    def test_missing_api_key_fails_at_startup(self):
        import os
        from unittest import mock
        env = {"DATABASE_URL": "postgresql://x/y", "TCN_USER_PASSWORD": "p", "ANTHROPIC_API_KEY": " "}
        with mock.patch.dict(os.environ, env, clear=True), self.assertRaises(RuntimeError):
            create_app()

    def test_audit_api_off_by_default(self):
        self.assertEqual(self.client().get("/api/audit").status_code, 404)
        on = TestClient(create_app(ask_fn=fake_ask, cost_cap_usd=1.0, audit_api=True))
        self.assertEqual(on.get("/api/audit").json(), [])
        self.assertEqual(self.client().get("/healthz").json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
