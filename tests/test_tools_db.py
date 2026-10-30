"""4단계 탐색 도구·지표 SQL 통합 테스트 (TEST_DATABASE_URL 필요).

지표 SQL의 기대값은 pandas로 따로 계산한다 (같은 로직을 두 번 짜서 서로 검산).
"""
import os
import unittest

import pandas as pd

from inhouse.agent import knowledge
from inhouse.agent.audit import MemoryAudit
from inhouse.agent.tools import Toolbox
from inhouse.data import load, overlay, transform
from tests.fixtures import mini_raw

DSN = os.environ.get("TEST_DATABASE_URL")
PW = "test-user-pw"
LOCAL = "America/Sao_Paulo"


@unittest.skipUnless(DSN, "TEST_DATABASE_URL 없음")
class TestTools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = overlay.apply(transform.build(mini_raw()), seed=7, test_orders=3, refund_rate=0.5, withdraw_rate=0.34)
        load.load(cls.t, DSN, log=lambda m: None, user_password=PW)

    def box(self, user):
        return Toolbox(DSN, PW, user, MemoryAudit())

    # ---- list_tables / describe_table ------------------------------------------------
    def test_list_tables_shows_only_visible(self):
        hq = {t["name"] for t in self.box("u_e001").list_tables()["tables"]}
        cs = {t["name"] for t in self.box("u_e201").list_tables()["tables"]}
        self.assertIn("ord", hq)
        self.assertIn("cust", hq)  # 일부 열만 보여도 표는 보인다
        self.assertNotIn("emp", hq)
        self.assertNotIn("db_user", hq)
        self.assertNotIn("v_cust_contact", hq)
        self.assertIn("v_cust_contact", cs)

    def test_describe_hides_pii_and_says_so(self):
        d = self.box("u_e104").describe_table("cust")
        names = [c["name"] for c in d["columns"]]
        self.assertIn("cust_uid", names)
        for pii in ["cust_nm", "tel_no", "email"]:
            self.assertNotIn(pii, names)
        self.assertEqual(d["hidden_columns"], 3)
        self.assertIn("SELECT *", d["hidden_note"])

    def test_describe_has_curated_notes_fk_and_samples_under_rls(self):
        d = self.box("u_e104").describe_table("cust")
        col = {c["name"]: c for c in d["columns"]}
        self.assertIn("cust_uid", col["cust_no"]["note"])
        self.assertEqual(col["uf"]["samples"], ["SP"])  # 표본도 RLS를 따른다
        self.assertNotIn("samples", col["cust_no"])  # ID는 표본을 안 준다 (작은 표본에선 값이 적어 범주형처럼 보일 수 있음)
        o = self.box("u_e001").describe_table("ord")
        self.assertTrue(any("cust_no" in fk and "cust" in fk for fk in o["foreign_keys"]))
        self.assertIn("America/Sao_Paulo", {c["name"]: c for c in o["columns"]}["ord_dtm"]["note"])

    def test_describe_unknown_or_hidden_table(self):
        for name in ["emp", "nope", "ord; DROP TABLE ord"]:
            self.assertIn("error", self.box("u_e104").describe_table(name))

    # ---- run_sql via toolbox --------------------------------------------------------
    def test_run_sql_goes_through_runner_and_audit(self):
        b = self.box("u_e105")
        r = b.run_sql("SELECT DISTINCT uf FROM cust", question="q")
        self.assertEqual(r["rows"], [["PR"]])
        self.assertEqual(b.audit.records[-1]["db_user"], "u_e105")

    def test_dispatch_by_tool_name(self):
        b = self.box("u_e001")
        self.assertEqual(b.call("lookup_metric", {"query": "매출"})["metric"]["id"], "net_sales")
        self.assertIn("error", b.call("no_such_tool", {}))

    # ---- 지표 SQL을 pandas 계산과 맞춰 본다 ------------------------------------------------
    def metric(self, mid, start="2016-01-01", end="2019-01-01", user="u_e001"):
        m = next(m for m in knowledge.load()["metrics"] if m["id"] == mid)
        r = self.box(user).run_sql(knowledge.fill(m["sql"], start, end))
        self.assertEqual(r["status"], "ok", r["error"])
        return r["rows"][0][0]

    def valid(self):
        o = self.t["ord"]
        return o[~o.is_test & ~o.stat_cd.isin([90, 91])]

    def test_order_and_customer_counts(self):
        v = self.valid()
        self.assertEqual(self.metric("order_count"), len(v))
        uids = self.t["cust"].set_index("cust_no").loc[v.cust_no, "cust_uid"].nunique()
        self.assertEqual(self.metric("customer_count"), uids)

    def test_net_sales_subtracts_only_refunds_of_included_orders(self):
        v = self.valid()
        sales = self.t["ord_item"][self.t["ord_item"].ord_no.isin(v.ord_no)].sale_amt.sum()
        refunds = self.t["rfnd"][self.t["rfnd"].ord_no.isin(v.ord_no)].rfnd_amt.sum()
        self.assertAlmostEqual(self.metric("net_sales"), round(sales - refunds, 2), places=2)
        # 취소 주문(o2)의 전액 환불은 이미 빠진 주문이라 또 빼면 안 된다
        self.assertIn("o2", set(self.t["rfnd"].ord_no))

    def test_gmv_and_cancel_rate(self):
        v = self.valid()
        items = self.t["ord_item"][self.t["ord_item"].ord_no.isin(v.ord_no)]
        self.assertAlmostEqual(self.metric("gmv"), round((items.sale_amt + items.dlv_fee).sum(), 2), places=2)
        real = self.t["ord"][~self.t["ord"].is_test]
        self.assertAlmostEqual(self.metric("cancel_rate"), round(100 * real.stat_cd.isin([90, 91]).mean(), 2))

    def test_period_uses_local_date(self):
        # o4: 현지 2018-08-01 23:30 (UTC로는 8/2)
        self.assertEqual(self.metric("order_count", "2018-08-01", "2018-08-02"), 1)
        self.assertEqual(self.metric("order_count", "2018-08-02", "2018-08-03"), 0)

    def test_repeat_rate_counts_people_not_cust_no(self):
        # u1은 주문 2건(c1, c2)이지만 c2 주문(o2)이 취소라 유효 주문은 1건 → 재구매 0
        self.assertEqual(self.metric("repeat_rate"), 0.0)

    def test_every_metric_runs_for_a_region_manager(self):
        for m in knowledge.load()["metrics"]:
            r = self.box("u_e104").run_sql(knowledge.fill(m["sql"], "2016-01-01", "2019-01-01"))
            self.assertEqual(r["status"], "ok", f"{m['id']}: {r['error']}")


if __name__ == "__main__":
    unittest.main()
