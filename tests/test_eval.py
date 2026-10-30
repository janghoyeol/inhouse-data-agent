"""5단계 오프라인 테스트: 채점 비교 규칙, 정답셋 파일 형식, 에이전트 루프(가짜 Claude 클라이언트)."""
import json
import unittest
from types import SimpleNamespace as NS

import yaml

from inhouse.agent import agent, personas
from inhouse.agent.sqlguard import check
from inhouse.eval.match import matches, refused, same
from inhouse.eval.run import grade


class TestMatch(unittest.TestCase):
    def test_scalar_found_anywhere_with_rounding(self):
        self.assertTrue(matches([[6031684.57]], [["2017", 6031684.57, 44379]]))
        self.assertTrue(matches([[6031684.57]], [[6031685]]))  # 반올림
        self.assertFalse(matches([[6031684.57]], [[7090569.24]]))
        self.assertTrue(matches([[1176]], [["1,176"]]))

    def test_rate_accepts_fraction_or_percent(self):
        self.assertTrue(matches([[2.83]], [[0.0283]], rate=True))
        self.assertFalse(matches([[2.83]], [[0.0283]]))

    def test_table_ignores_column_names_order_and_extra_columns(self):
        gold = [["SE", 100], ["S", 50]]
        self.assertTrue(matches(gold, [[50, "S", "Sul"], [100, "SE", "Sudeste"]]))
        self.assertFalse(matches(gold, [[100, "SE"]]))  # 행 수가 다름
        self.assertFalse(matches(gold, [[100, "SE"], [51, "S"]]))

    def test_order_matters_for_rankings(self):
        gold = [["a", 3], ["b", 2]]
        self.assertTrue(matches(gold, [["a", 3], ["b", 2]], order_matters=True))
        self.assertFalse(matches(gold, [["b", 2], ["a", 3]], order_matters=True))
        self.assertTrue(matches(gold, [["b", 2], ["a", 3]]))
        # 순위 질문: 상위 10개를 뽑고 3개를 답해도 앞부분이 같으면 맞음, 앞에 다른 행이 끼면 틀림
        self.assertTrue(matches(gold, [["a", 3], ["b", 2], ["c", 1]], order_matters=True))
        self.assertFalse(matches(gold, [["a", 3], ["x", 2.5], ["b", 2]], order_matters=True))
        self.assertFalse(matches(gold, [["a", 3], ["b", 2], ["c", 1]]))  # 순서 무관 표는 행 수가 같아야 함

    def test_dates_and_nulls(self):
        self.assertTrue(same("2017-11-24", "2017-11-24T00:00:00", 0.0005))
        self.assertTrue(same(None, None, 0.0005))
        self.assertTrue(same("2017-01", "2017-01-01", 0.0005))
        self.assertFalse(same("2017-01", "2017-02-01", 0.0005))
        self.assertFalse(same(None, 0, 0.0005))

    def test_refusal_text(self):
        self.assertTrue(refused("죄송하지만 고객 전화번호 원문은 볼 수 없습니다."))
        self.assertTrue(refused("이 요청은 도와드릴 수 없습니다."))
        self.assertFalse(refused("2017년 매출은 603만 헤알입니다."))


class TestQuestionsFile(unittest.TestCase):
    qs = yaml.safe_load(open("eval/questions.yaml", encoding="utf-8"))["questions"]

    def test_fifty_questions_ten_per_category(self):
        self.assertEqual(len(self.qs), 50)
        self.assertEqual(len({q["id"] for q in self.qs}), 50)
        cats = {}
        for q in self.qs:
            cats[q["category"]] = cats.get(q["category"], 0) + 1
        self.assertEqual(set(cats.values()), {10})

    def test_fields(self):
        for q in self.qs:
            self.assertIn(q["db_user"], personas.PERSONAS, q["id"])
            self.assertIn(q["expect"], {"result", "refuse"}, q["id"])
            if q["expect"] == "refuse":
                self.assertFalse(q.get("gold_sql"), q["id"])
            if q.get("gold_sql"):
                self.assertTrue(check(q["gold_sql"]).ok, f"{q['id']}: {check(q['gold_sql']).reason}")


def block(**kw):
    return NS(**kw)


class FakeClient:
    """정해진 응답을 차례로 돌려주는 Claude 클라이언트 대역."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.messages = NS(create=self.create)

    def create(self, **kw):
        self.requests.append(kw)
        return self.responses.pop(0)


def usage(i=100, o=20):
    return NS(input_tokens=i, output_tokens=o, cache_read_input_tokens=0, cache_creation_input_tokens=0)


class FakeToolbox:
    db_user = "u_e104"
    question = None

    def __init__(self):
        self.calls = []

    def call(self, name, args):
        self.calls.append((name, args))
        if name == "run_sql":
            return {"query_id": "q-20261030-0000abcd", "status": "ok", "columns": ["n"], "rows": [[812]], "error": ""}
        return {"metric": {"id": "order_count"}}


class TestAgentLoop(unittest.TestCase):
    def run_agent(self, responses):
        client, tb = FakeClient(responses), FakeToolbox()
        return client, tb, agent.ask(client, tb, "블랙프라이데이 주문 수?", model="claude-haiku-5-5")

    def test_tools_then_answer_with_citation(self):
        client, tb, ans = self.run_agent([
            NS(stop_reason="tool_use", usage=usage(), content=[
                block(type="tool_use", id="t1", name="lookup_metric", input={"query": "주문 수"}),
                block(type="tool_use", id="t2", name="run_sql", input={"sql": "SELECT 812"})]),
            NS(stop_reason="end_turn", usage=usage(200, 30),
               content=[block(type="text", text="남동부 기준 812건입니다 [q-20261030-0000abcd].")]),
        ])
        self.assertEqual([c[0] for c in tb.calls], ["lookup_metric", "run_sql"])
        self.assertEqual(ans.cited, ["q-20261030-0000abcd"])
        self.assertEqual(ans.turns, 2)
        self.assertEqual(ans.tokens["input"], 300)
        # 두 도구 결과는 한 user 메시지로 함께 돌려준다
        # (가짜 클라이언트는 같은 리스트 객체를 받으므로 위치로 찾는다: 질문, 도구 호출, 도구 결과, 최종 답)
        last_user = client.requests[1]["messages"][2]
        self.assertEqual(len(last_user["content"]), 2)
        self.assertEqual(json.loads(last_user["content"][1]["content"])["rows"], [[812]])
        # 시스템 프롬프트에 묻는 사람과 공통 규칙
        sys_text = " ".join(b["text"] for b in client.requests[0]["system"])
        self.assertIn("남동부 권역 매니저", sys_text)
        self.assertIn("America/Sao_Paulo", sys_text)
        self.assertNotIn("tool_choice", client.requests[0])
        q = {"expect": "result"}
        self.assertEqual(grade(q, {"rows": [[812]]}, ans), {"passed": True, "via": "cited"})
        self.assertEqual(grade(q, {"rows": [[999]]}, ans)["passed"], False)

    def test_uncited_correct_result_is_flagged(self):
        _, _, ans = self.run_agent([
            NS(stop_reason="tool_use", usage=usage(), content=[block(type="tool_use", id="t1", name="run_sql", input={"sql": "x"})]),
            NS(stop_reason="end_turn", usage=usage(), content=[block(type="text", text="812건입니다.")]),
        ])
        self.assertEqual(grade({"expect": "result"}, {"rows": [[812]]}, ans), {"passed": True, "via": "uncited"})

    def test_stops_at_max_turns(self):
        loop = NS(stop_reason="tool_use", usage=usage(), content=[block(type="tool_use", id="t", name="run_sql", input={"sql": "x"})])
        _, _, ans = self.run_agent([loop] * agent.MAX_TURNS)
        self.assertEqual(ans.stop_reason, "max_turns")

    def test_refusal_stop_reason_recorded(self):
        _, _, ans = self.run_agent([NS(stop_reason="refusal", usage=usage(), content=[])])
        self.assertEqual((ans.stop_reason, ans.text), ("refusal", ""))


if __name__ == "__main__":
    unittest.main()
