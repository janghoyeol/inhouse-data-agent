"""Postgres 적재 통합 테스트. TEST_DATABASE_URL이 있을 때만 (그 DB의 표를 지우고 다시 만든다)."""
import os
import unittest

from inhouse.data import load, overlay, transform
from tests.fixtures import mini_raw

DSN = os.environ.get("TEST_DATABASE_URL")


@unittest.skipUnless(DSN, "TEST_DATABASE_URL 없음")
class TestLoad(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.tables = overlay.apply(transform.build(mini_raw()), seed=7, test_orders=3, refund_rate=0.5,
                                   withdraw_rate=0.34)
        cls.counts = load.load(cls.tables, DSN, log=lambda m: None)
        cls.conn = psycopg.connect(DSN)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def q(self, sql):
        return self.conn.execute(sql).fetchall()

    def test_row_counts_match(self):
        for name, n in self.counts.items():
            self.assertEqual(n, len(self.tables[name]), name)

    def test_timestamps_are_utc_instants(self):
        local = self.q("SELECT (ord_dtm AT TIME ZONE 'America/Sao_Paulo')::text FROM ord WHERE ord_no = 'o3'")[0][0]
        self.assertEqual(local, "2017-12-01 10:00:00")

    def test_nulls_and_flags_survive(self):
        self.assertEqual(self.q("SELECT count(*) FROM ord WHERE is_test")[0][0], 3)
        self.assertEqual(self.q("SELECT count(*) FROM prd WHERE ctgr_cd IS NULL")[0][0], 1)
        self.assertEqual(self.q("SELECT zip_pfx FROM cust WHERE cust_no = 'c1'")[0][0], "01037")

    def test_status_needs_code_table(self):
        self.assertEqual(self.q("SELECT s.stat_nm FROM ord o JOIN cd_ord_stat s USING (stat_cd) "
                                "WHERE o.ord_no = 'o2'")[0][0], "canceled")

    def test_comments_present_for_describe(self):
        self.assertEqual(self.q("SELECT col_description('ord'::regclass, attnum) FROM pg_attribute "
                                "WHERE attrelid = 'ord'::regclass AND attname = 'ord_dtm'")[0][0], "주문 일시")
        self.assertEqual(self.q("SELECT obj_description('ord'::regclass)")[0][0], "주문")


if __name__ == "__main__":
    unittest.main()
