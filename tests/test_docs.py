"""2a-2 문서 읽기·조각 나누기·검색어 만들기 오프라인 테스트 (ADR 0004)."""
import datetime as dt
import unittest

from inhouse.data import docs

TAGS = {"all", "cs", "mgr", "hq", "hr", "rgn:N", "rgn:NE", "rgn:CO", "rgn:SE", "rgn:S"}


class TestCorpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docs = docs.read_corpus()

    def test_thirty_documents_with_valid_header(self):
        self.assertEqual(len(self.docs), 30)
        self.assertEqual(len({d.doc_key for d in self.docs}), 30)
        for d in self.docs:
            self.assertIn(d.status, {"active", "superseded", "draft"}, d.doc_key)
            self.assertTrue(set(d.audience) <= TAGS and d.audience, d.doc_key)
            self.assertIsInstance(d.eff_from, dt.date)
            if d.eff_to:
                self.assertLessEqual(d.eff_from, d.eff_to, d.doc_key)

    def test_versions_do_not_overlap_and_one_is_active(self):
        by_id = {}
        for d in self.docs:
            by_id.setdefault(d.doc_id, []).append(d)
        refund = sorted(by_id["POL-REF-001"], key=lambda d: d.version)
        self.assertEqual([d.status for d in refund], ["superseded", "active", "draft"])
        self.assertLess(refund[0].eff_to, refund[1].eff_from)
        self.assertEqual([d.status for d in sorted(by_id["POL-APR-001"], key=lambda d: d.version)],
                         ["superseded", "active"])

    def test_approval_number_only_on_approved_notices(self):
        approved = {d.doc_id: d.approval_no for d in self.docs if d.approval_no}
        self.assertEqual(approved, {"NOTICE-N-2018-01": "APR-2018-004", "NOTICE-NE-2017-11": "APR-2017-031"})

    def test_chunks_follow_sections_and_keep_preamble(self):
        ref = next(d for d in self.docs if d.doc_key == "POL-REF-001@v2")
        heads = [c.heading for c in ref.chunks]
        self.assertEqual(heads[0], "1. 목적")
        self.assertIn("3. 신청 기한", heads)
        self.assertIn("15일 이내", next(c for c in ref.chunks if c.heading == "3. 신청 기한").content)
        # 승인 번호처럼 첫 절 앞에 있는 글도 조각으로 남는다
        north = next(d for d in self.docs if d.doc_id == "NOTICE-N-2018-01")
        self.assertIn("APR-2018-004", " ".join(c.content for c in north.chunks))
        for d in self.docs:
            self.assertTrue(d.chunks, d.doc_key)
            self.assertEqual(len({c.chunk_id for c in d.chunks}), len(d.chunks))

    def test_visible_counts_per_persona(self):
        # docs/doc-notes.md '누가 무엇을 보나'와 같아야 한다
        tags = {"u_e001": {"all", "hq", "mgr"}, "u_e104": {"all", "mgr", "rgn:SE"}, "u_e201": {"all", "cs"}}
        n = {u: sum(bool(t & set(d.audience)) for d in self.docs) for u, t in tags.items()}
        self.assertEqual(n, {"u_e001": 27, "u_e104": 18, "u_e201": 22})


class TestTokens(unittest.TestCase):
    def test_korean_bigrams_and_ascii_words(self):
        self.assertEqual(docs.tokens("환불 신청 기한"), ["환불", "신청", "기한"])
        self.assertEqual(docs.tokens("배송완료일"), ["배송", "송완", "완료", "료일"])
        self.assertEqual(docs.tokens("코드 DMG, POL-REF-001"), ["코드", "dmg", "pol-ref-001"])
        self.assertEqual(docs.tokens("15일 R$ 100"), ["15", "일", "r", "100"])

    def test_tsvector_literal_quotes_and_positions(self):
        self.assertEqual(docs.tsvector_literal(["a'b", "환불", "a'b"]), "'a''b':1,3 '환불':2")
        self.assertEqual(docs.tsvector_literal([]), "")


if __name__ == "__main__":
    unittest.main()
