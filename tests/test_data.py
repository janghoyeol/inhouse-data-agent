"""1단계 데이터 층 오프라인 테스트: Olist → 회사 DB 표 변환, 합성 덧층.

실제 Postgres 적재 확인은 tests/test_load_db.py (DATABASE_URL 필요).
"""
import unittest

import pandas as pd

from inhouse.data import overlay, transform
from tests.fixtures import mini_raw


class TestTransform(unittest.TestCase):
    t = transform.build(mini_raw())

    def test_all_27_states_have_region(self):
        st = self.t["st"]
        self.assertEqual(len(st), 27)
        self.assertEqual(set(st.rgn_cd), {"N", "NE", "CO", "SE", "S"})
        self.assertEqual(st.set_index("uf").loc["SP", "rgn_cd"], "SE")
        self.assertEqual(st.set_index("uf").loc["AM", "rgn_cd"], "N")

    def test_status_becomes_code(self):
        o = self.t["ord"].set_index("ord_no")
        codes = self.t["cd_ord_stat"].set_index("stat_cd")
        self.assertEqual(codes.loc[o.loc["o1", "stat_cd"], "stat_nm"], "delivered")
        self.assertEqual(codes.loc[o.loc["o2", "stat_cd"], "stat_nm"], "canceled")
        self.assertEqual(len(codes), 8)

    def test_local_time_stored_as_utc_with_dst(self):
        o = self.t["ord"].set_index("ord_no")
        self.assertEqual(o.loc["o1", "ord_dtm"], pd.Timestamp("2017-10-02 13:56:33", tz="UTC"))  # UTC-3
        self.assertEqual(o.loc["o3", "ord_dtm"], pd.Timestamp("2017-12-01 12:00:00", tz="UTC"))  # 서머타임 UTC-2
        # 현지 8/1 23:30 주문은 UTC로는 8/2 — "8월 1일 주문"을 UTC 날짜로 자르면 빠진다
        self.assertEqual(o.loc["o4", "ord_dtm"].date().isoformat(), "2018-08-02")
        self.assertTrue(pd.isna(o.loc["o2", "aprv_dtm"]))

    def test_estimated_delivery_is_date(self):
        self.assertEqual(str(self.t["ord"].set_index("ord_no").loc["o1", "est_dlv_dt"]), "2017-10-18")

    def test_reviews_keep_duplicate_ids_with_surrogate_key(self):
        r = self.t["rvw"]
        self.assertEqual(len(r), 3)
        self.assertEqual(r.rvw_sn.is_unique, True)
        self.assertEqual((r.rvw_id == "r1").sum(), 2)

    def test_categories_and_missing_values_kept_as_is(self):
        self.assertEqual(set(self.t["ctgr"].ctgr_cd), {"perfumaria", "beleza_saude"})
        self.assertTrue(pd.isna(self.t["prd"].set_index("prd_id").loc["p2", "ctgr_cd"]))

    def test_read_raw_strips_bom(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            for stem, df in mini_raw().items():
                name = transform.RAW_FILES[stem]
                enc = "utf-8-sig" if stem == "category_translation" else "utf-8"
                df.to_csv(os.path.join(d, name), index=False, encoding=enc)
            raw = transform.read_raw(d)
        self.assertIn("product_category_name", raw["category_translation"].columns)
        self.assertEqual(raw["customers"].customer_zip_code_prefix.iloc[0], "01037")  # 앞자리 0 보존


class TestOverlay(unittest.TestCase):
    base = transform.build(mini_raw())
    t = overlay.apply(base, seed=7, test_orders=3, refund_rate=0.5, withdraw_rate=0.34)

    def test_deterministic(self):
        again = overlay.apply(transform.build(mini_raw()), seed=7, test_orders=3, refund_rate=0.5, withdraw_rate=0.34)
        for name in self.t:
            pd.testing.assert_frame_equal(self.t[name], again[name], obj=name)

    def test_input_not_mutated(self):
        self.assertNotIn("cust_nm", self.base["cust"].columns)

    def test_same_person_same_pii(self):
        c = self.t["cust"]
        real = c[~c.is_test_acct]
        per_person = real.groupby("cust_uid")[["cust_nm", "tel_no", "email"]].nunique(dropna=False)
        self.assertTrue((per_person <= 1).all().all())

    def test_withdrawn_customers_have_no_pii(self):
        c = self.t["cust"]
        gone = c[c.deleted_at.notna()]
        self.assertGreater(len(gone), 0)
        self.assertTrue(gone[["cust_nm", "tel_no", "email"]].isna().all().all())
        alive = c[c.deleted_at.isna()]
        self.assertTrue(alive.email.str.endswith(("@example.com", "@example.net", "@example.org")).all())

    def test_test_orders_flagged_with_own_customers(self):
        o, c = self.t["ord"], self.t["cust"]
        test = o[o.is_test]
        self.assertEqual(len(test), 3)
        self.assertFalse(o[~o.is_test].ord_no.isin(test.ord_no).any())
        self.assertTrue(c[c.cust_no.isin(test.cust_no)].is_test_acct.all())
        self.assertTrue(self.t["ord_item"].ord_no.isin(test.ord_no).any())
        self.assertTrue(self.t["ord_pay"].ord_no.isin(test.ord_no).any())

    def test_refunds_never_exceed_payment(self):
        r = self.t["rfnd"]
        self.assertGreater(len(r), 0)
        paid = self.t["ord_pay"].groupby("ord_no").pay_amt.sum()
        refunded = r.groupby("ord_no").rfnd_amt.sum()
        self.assertTrue((refunded <= paid.loc[refunded.index] + 1e-9).all())
        # 취소 주문은 전액 환불
        self.assertAlmostEqual(refunded.get("o2"), paid.loc["o2"])
        self.assertFalse(r.ord_no.isin(self.t["ord"].query("is_test").ord_no).any())

    def test_employees_cover_roles_and_regions(self):
        e = self.t["emp"]
        self.assertEqual(set(e.role_cd), {"HQ_ANALYST", "RGN_MGR", "CS"})
        self.assertEqual(set(e[e.role_cd == "RGN_MGR"].rgn_cd), {"N", "NE", "CO", "SE", "S"})
        self.assertTrue(e[e.role_cd != "RGN_MGR"].rgn_cd.isna().all())

    def test_legacy_summary_in_thousands_by_local_month(self):
        s = self.t["mon_sls_smry_old"].set_index(["yyyymm", "uf"])
        # 2017-10 SP: o1만 (취소 o2는 11월·제외). 상품 58.90+10.00, 배송비 13.29+5.00 → 87.19 헤알 → 0.1 천 헤알
        self.assertAlmostEqual(s.loc[("201710", "SP"), "sls_amt_k"], 0.1)
        self.assertEqual(s.loc[("201710", "SP"), "ord_cnt"], 1)
        self.assertNotIn(("201711", "SP"), s.index)  # 취소만 있던 달
        # 레거시는 2017년까지만 (2018-01 시스템 이관)
        self.assertTrue((s.reset_index().yyyymm <= "201712").all())


if __name__ == "__main__":
    unittest.main()
