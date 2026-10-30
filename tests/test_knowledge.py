"""4단계 지표 사전 오프라인 테스트: 형식, 이름 충돌, 예시 SQL이 검사를 통과하는지, 찾기."""
import unittest

from inhouse.agent import knowledge
from inhouse.agent.sqlguard import check

K = knowledge.load()


class TestMetricsFile(unittest.TestCase):
    def test_required_fields(self):
        for m in K["metrics"]:
            for f in ["id", "name", "aliases", "definition", "rules", "sql", "status"]:
                self.assertIn(f, m, m.get("id"))
            self.assertIn(m["status"], {"draft", "agreed"})

    def test_ids_and_aliases_do_not_collide(self):
        seen = {}
        for m in K["metrics"]:
            for word in [m["id"], m["name"], *m["aliases"]]:
                key = knowledge.normalize(word)
                self.assertNotIn(key, seen, f"{word}: {m['id']} vs {seen.get(key)}")
                seen[key] = m["id"]

    def test_example_sql_passes_guard_after_filling_period(self):
        for m in K["metrics"]:
            sql = knowledge.fill(m["sql"], "2017-01-01", "2018-01-01")
            r = check(sql)
            self.assertTrue(r.ok, f"{m['id']}: {r.reason}")
            self.assertNotIn("{", sql, m["id"])

    def test_common_rules_cover_known_traps(self):
        text = " ".join(K["common_rules"])
        for word in ["America/Sao_Paulo", "is_test", "cust_uid", "90", "91", "mon_sls_smry_old", "geo_zip"]:
            self.assertIn(word, text)


class TestLookup(unittest.TestCase):
    def test_alias_and_case_insensitive(self):
        self.assertEqual(knowledge.lookup("매출")["metric"]["id"], "net_sales")
        self.assertEqual(knowledge.lookup("gmv")["metric"]["id"], "gmv")
        self.assertEqual(knowledge.lookup("  재구매율 ")["metric"]["id"], "repeat_rate")

    def test_contained_word(self):
        self.assertEqual(knowledge.lookup("지난달 객단가")["metric"]["id"], "aov")

    def test_not_found_lists_available_and_says_state_assumption(self):
        r = knowledge.lookup("LTV")
        self.assertNotIn("metric", r)
        self.assertIn("순매출", r["available"])
        self.assertIn("가정", r["message"])

    def test_result_includes_common_rules_and_draft_flag(self):
        r = knowledge.lookup("매출")
        self.assertTrue(r["common_rules"])
        self.assertIn("status", r["metric"])


if __name__ == "__main__":
    unittest.main()
