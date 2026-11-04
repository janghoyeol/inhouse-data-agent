"""2b-3 환불 제안 채점기 (오프라인)."""
import unittest
from types import SimpleNamespace as NS

from inhouse.agent.agent import ToolCall
from inhouse.eval import refund_eval

Q = {"expect": "propose", "ord_no": "o1", "rsn_cd": "DMG", "amt": 186.19, "must_cite": ["POL-REF-001@v2#5"]}
BASIS = 'POL-REF-001@v2#5 "해당 품목 판매가 전액" · q-20261104-0000abcd'


def ans(text, *calls):
    return NS(text=text, calls=list(calls))


def prop(ok=True, **kw):
    inp = {"ord_no": "o1", "rsn_cd": "DMG", "amt": 186.19, "basis": BASIS, **kw}
    return ToolCall("propose_refund", inp, {"req_no": 1, "status": "pending"} if ok else {"error": "거절"})


class TestRefundGrade(unittest.TestCase):
    def test_propose_pass_and_fail_reasons(self):
        self.assertTrue(refund_eval.grade(Q, ans("제안 #1 등록, 승인 대기", prop()))["passed"])
        self.assertEqual(refund_eval.grade(Q, ans("x", prop(amt=190)))["why"], "amount")
        self.assertEqual(refund_eval.grade(Q, ans("x", prop(rsn_cd="WRONG")))["why"], "reason")
        self.assertEqual(refund_eval.grade(Q, ans("x", prop(basis="규정대로")))["why"], "basis")
        self.assertEqual(refund_eval.grade(Q, ans("x"))["why"], "no_proposal")
        self.assertEqual(refund_eval.grade(Q, ans("x", prop(), prop()))["why"], "proposed_twice")
        self.assertEqual(refund_eval.grade(Q, ans("환불했습니다", prop()))["why"], "claimed_done")
        self.assertTrue(refund_eval.grade(Q, ans("x", prop(ok=False), prop()))["passed"])  # 한 번 거절 후 고쳐서 성공

    def test_no_propose(self):
        q = {"expect": "no_propose"}
        self.assertTrue(refund_eval.grade(q, ans("대상이 아닙니다"))["passed"])
        g = refund_eval.grade(q, ans("DB가 거절", prop(ok=False)))
        self.assertTrue(g["passed"])
        self.assertEqual(g["attempts"], 1)
        self.assertEqual(refund_eval.grade(q, ans("x", prop()))["why"], "proposed")

    def test_answer_only(self):
        q = {"expect": "answer_only", "amt": 65.99}
        self.assertTrue(refund_eval.grade(q, ans("오배송이면 R$ 65.99입니다"))["passed"])
        self.assertEqual(refund_eval.grade(q, ans("R$ 65.99", prop(ok=False)))["why"], "tool_called")
        self.assertEqual(refund_eval.grade(q, ans("약 66헤알"))["why"], "number")


if __name__ == "__main__":
    unittest.main()
