"""2a-4 문서 질문 채점기 (오프라인): 규칙 기반 — 낱말 묶음·금지 낱말·인용·정답 숫자."""
import unittest
from types import SimpleNamespace as NS

from inhouse.eval import docs_eval


def ans(text, cites=()):
    return NS(text=text, doc_citations=[{"chunk_id": c, "retrieved": r, "quote_found": q, "warnings": []}
                                        for c, r, q in cites])


class TestDocGrade(unittest.TestCase):
    Q = {"expect": "answer", "must_include": [["2018-01-12", "1월 12일"]], "must_cite": ["POL-REF-001@v1#3"]}

    def test_pass_needs_words_and_retrieved_citation(self):
        self.assertTrue(docs_eval.grade(self.Q, ans("1월 12일까지", [("POL-REF-001@v1#3", True, True)]))["passed"])
        g = docs_eval.grade(self.Q, ans("1월 12일까지", [("POL-REF-001@v1#3", False, None)]))
        self.assertEqual((g["passed"], g["why"]), (False, "no_citation"))
        self.assertEqual(docs_eval.grade(self.Q, ans("1월 13일", [("POL-REF-001@v1#3", True, True)]))["why"], "missing:2018-01-12")

    def test_unverified_quote_still_counts_but_is_reported(self):
        g = docs_eval.grade(self.Q, ans("2018-01-12", [("POL-REF-001@v1#3", True, False)]))
        self.assertTrue(g["passed"])
        self.assertEqual(g["citations_verified"], (0, 1))

    def test_must_not_include(self):
        q = {"expect": "not_found", "must_not_include": ["기본급"]}
        self.assertTrue(docs_eval.grade(q, ans("찾을 수 없습니다"))["passed"])
        self.assertEqual(docs_eval.grade(q, ans("기본급의 20%"))["why"], "leaked:기본급")

    def test_gold_numbers(self):
        q = {"expect": "answer"}
        self.assertTrue(docs_eval.grade(q, ans("순매출은 6,031,684.57 헤알"), gold=[[6031684.57]])["passed"])
        self.assertFalse(docs_eval.grade(q, ans("순매출은 약 603만 헤알"), gold=[[6031684.57]])["passed"])
        self.assertTrue(docs_eval.grade(q, ans("56.85헤알"), gold=[[56.846]], tol=0.001)["passed"])
        many = [["DMG", 1006], ["WRONG", 926]]
        self.assertTrue(docs_eval.grade(q, ans("파손 1,006건, 오배송 926건"), gold=many)["passed"])
        self.assertFalse(docs_eval.grade(q, ans("파손 1,006건"), gold=many)["passed"])

    def test_numbers_in_text(self):
        self.assertEqual(docs_eval.numbers("R$ 1,234.50 과 2018-01-12, 15일"), [1234.5, 2018, 1, 12, 15])


if __name__ == "__main__":
    unittest.main()
