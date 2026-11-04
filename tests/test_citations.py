"""2a-3 문서 인용 검증 (오프라인): 답 속 [조각ID "인용"]이 실제로 검색해 받은 조각에 있는지 코드가 확인한다."""
import unittest

from inhouse.agent import citations

CHUNKS = {
    "POL-REF-001@v2#3": {"chunk_id": "POL-REF-001@v2#3", "doc_id": "POL-REF-001", "version": 2, "status": "active",
                         "approval_no": None,
                         "content": "고객은 배송 완료일(현지 날짜) 다음 날부터 **15일 이내**에 환불을 신청해야 한다. 제1판의 7일에서 늘렸다."},
    "POL-REF-001@v3#1": {"chunk_id": "POL-REF-001@v3#1", "doc_id": "POL-REF-001", "version": 3, "status": "draft",
                         "approval_no": None, "content": "- 신청 기한: 배송 완료 다음 날부터 **30일 이내** (현행 15일)"},
    "NOTICE-SE-2018-05@v1#2": {"chunk_id": "NOTICE-SE-2018-05@v1#2", "doc_id": "NOTICE-SE-2018-05", "version": 1,
                               "status": "active", "approval_no": None,
                               "content": "이 기간 배송 지연 고객에게는 지연 보상을 **두 배**로 해 주기로 권역 회의에서 정했다."},
}


class TestCitations(unittest.TestCase):
    def check(self, text):
        return citations.verify(text, CHUNKS)

    def test_parse_ids_and_quotes(self):
        cs = citations.parse('기한은 15일입니다 [POL-REF-001@v2#3 "다음 날부터 15일 이내"]. 근거 [POL-REF-001@v3#1]')
        self.assertEqual([(c.chunk_id, c.quote) for c in cs],
                         [("POL-REF-001@v2#3", "다음 날부터 15일 이내"), ("POL-REF-001@v3#1", None)])
        self.assertEqual(citations.parse("쿼리 [q-20261104-0000abcd] 만 있음"), [])
        inner = citations.parse('[GUIDE-CS-001@v1#2 "고객이 \\"반품\\"이라고 해도"] 끝')
        self.assertEqual(inner[0].quote, '고객이 \\"반품\\"이라고 해도')
        self.assertTrue(citations.quote_in(inner[0].quote, '고객이 "반품"이라고 해도 사유가'))
        rev = citations.parse('한도 ["CS 팀장 | R$ 300 이하" POL-APR-001@v1#2]')
        self.assertEqual([(c.chunk_id, c.quote) for c in rev], [("POL-APR-001@v1#2", "CS 팀장 | R$ 300 이하")])

    def test_quote_found_ignoring_markdown_and_spaces(self):
        r = self.check('[POL-REF-001@v2#3 “다음 날부터  15일 이내에 환불을”]')[0]
        self.assertTrue(r["retrieved"] and r["quote_found"])
        self.assertEqual(r["warnings"], [])

    def test_quote_with_ellipsis_must_appear_in_order(self):
        self.assertTrue(self.check('[POL-REF-001@v2#3 "배송 완료일 … 15일 이내"]')[0]["quote_found"])
        self.assertFalse(self.check('[POL-REF-001@v2#3 "15일 이내 … 배송 완료일"]')[0]["quote_found"])

    def test_made_up_quote_is_caught(self):
        r = self.check('[POL-REF-001@v2#3 "배송 완료일부터 30일 이내"]')[0]
        self.assertFalse(r["quote_found"])

    def test_chunk_not_retrieved_is_caught(self):
        r = self.check('[HR-002@v1#1 "기본급의 0~20%"]')[0]
        self.assertFalse(r["retrieved"])
        self.assertIsNone(r["quote_found"])

    def test_missing_quote(self):
        r = self.check("[POL-REF-001@v2#3]")[0]
        self.assertTrue(r["retrieved"])
        self.assertIsNone(r["quote_found"])

    def test_warnings_for_draft_and_unapproved_notice(self):
        self.assertIn("draft", self.check('[POL-REF-001@v3#1 "30일 이내"]')[0]["warnings"])
        self.assertIn("unapproved_notice", self.check('[NOTICE-SE-2018-05@v1#2 "두 배"]')[0]["warnings"])

    def test_summary(self):
        s = citations.summary(self.check('[POL-REF-001@v2#3 "15일 이내"] [POL-REF-001@v2#3 "30일"]'))
        self.assertEqual(s, {"citations": 2, "verified": 1, "unverified": 1, "warnings": 0})


if __name__ == "__main__":
    unittest.main()
