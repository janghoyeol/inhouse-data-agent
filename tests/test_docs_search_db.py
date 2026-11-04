"""2a-3 search_docs·read_doc 도구 통합 테스트: 직원 권한 안에서만 찾고, 판·시행 정보를 같이 돌려준다."""
import os
import unittest

from inhouse.agent.audit import MemoryAudit
from inhouse.agent.tools import Toolbox
from inhouse.data import load, overlay, transform
from tests.fixtures import mini_raw

DSN = os.environ.get("TEST_DATABASE_URL")
PW = "test-user-pw"


@unittest.skipUnless(DSN, "TEST_DATABASE_URL 없음")
class TestDocSearch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        t = overlay.apply(transform.build(mini_raw()), seed=7, test_orders=3, refund_rate=0.5, withdraw_rate=0.34)
        load.load(t, DSN, log=lambda m: None, user_password=PW)

    def box(self, user):
        return Toolbox(DSN, PW, user, MemoryAudit())

    def test_finds_deadline_clauses_with_version_info(self):
        r = self.box("u_e201").search_docs("환불 신청 기한이 며칠이야?")
        top = r["results"]
        self.assertLessEqual(len(top), 5)
        ids = [h["chunk_id"] for h in top]
        self.assertIn("POL-REF-001@v2#3", ids)
        h = next(h for h in top if h["chunk_id"] == "POL-REF-001@v2#3")
        self.assertEqual((h["status"], str(h["eff_from"]), h["version"]), ("active", "2018-01-01", 2))
        self.assertIn("15일 이내", h["content"])
        self.assertTrue(r["search_id"].startswith("s-"))

    def test_respects_permissions(self):
        for user, hidden in [("u_e201", "RPT-SEC-2018-03"), ("u_e104", "MTG-NE-2018-02"), ("u_e001", "HR-002")]:
            words = {"RPT-SEC-2018-03": "보안 사고 계정 공유", "MTG-NE-2018-02": "북동부 운영 회의록 마라냥",
                     "HR-002": "권역 매니저 성과급 기준"}[hidden]
            r = self.box(user).search_docs(words, k=10)
            self.assertNotIn(hidden, {h["doc_id"] for h in r["results"]}, user)
            self.assertIn("error", self.box(user).read_doc(f"{hidden}@v1"), user)

    def test_read_doc_returns_all_sections(self):
        r = self.box("u_e104").read_doc("POL-REF-001@v2")
        self.assertEqual(r["doc"]["status"], "active")
        self.assertEqual([c["chunk_id"] for c in r["chunks"]][:2], ["POL-REF-001@v2#1", "POL-REF-001@v2#2"])

    def test_odd_query_text_is_safe(self):
        for q in ["' | ! :* & (", "DROP TABLE doc;", "", "   ", "a" * 600]:
            r = self.box("u_e201").search_docs(q)
            self.assertTrue("results" in r or "error" in r, q)

    def test_tool_dispatch_and_audit(self):
        tb = self.box("u_e201")
        tb.question = "기한?"
        r = tb.call("search_docs", {"query": "환불 기한"})
        self.assertTrue(r["results"])
        rec = tb.audit.records[-1]
        self.assertEqual((rec["kind"], rec["db_user"], rec["search_id"]), ("doc_search", "u_e201", r["search_id"]))
        self.assertEqual(rec["chunk_ids"], [h["chunk_id"] for h in r["results"]])
        self.assertIn("chunks", tb.call("read_doc", {"doc_key": "POL-REF-001@v2"}))


if __name__ == "__main__":
    unittest.main()
