"""2단계 권한 통합 테스트: 직원 DB 계정으로 접속해 실제로 보이는 것·막히는 것을 확인한다.

TEST_DATABASE_URL(슈퍼유저)이 있을 때만. 권한은 프롬프트가 아니라 DB가 강제해야 하므로,
에이전트가 만들 법한 우회 SQL(SET ROLE, set_config, row_security 끄기, 통계 뷰)도 여기서 직접 시도한다.
"""
import os
import unittest

from inhouse.data import load, overlay, transform
from tests.fixtures import mini_raw

DSN = os.environ.get("TEST_DATABASE_URL")
PW = "test-user-pw"


@unittest.skipUnless(DSN, "TEST_DATABASE_URL 없음")
class TestSecurity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.psycopg = psycopg
        cls.tables = overlay.apply(transform.build(mini_raw()), seed=7, test_orders=3, refund_rate=0.5,
                                   withdraw_rate=0.34)
        load.load(cls.tables, DSN, log=lambda m: None, user_password=PW)
        cls.admin = psycopg.connect(DSN, autocommit=True)
        # 권역 매니저: enumerate(REGIONS) 순서 N=E101, NE=E102, CO=E103, SE=E104, S=E105
        cls.conns = {u: cls.connect(u) for u in ["u_e001", "u_e104", "u_e105", "u_e101", "u_e201"]}

    @classmethod
    def connect(cls, user):
        from psycopg.conninfo import make_conninfo
        return cls.psycopg.connect(make_conninfo(DSN, user=user, password=PW), autocommit=True)

    @classmethod
    def tearDownClass(cls):
        for c in cls.conns.values():
            c.close()
        cls.admin.close()

    def q(self, user, sql, params=None):
        return self.conns[user].execute(sql, params).fetchall()

    def denied(self, user, sql):
        with self.assertRaises(self.psycopg.Error, msg=f"{user}: {sql}"):
            self.q(user, sql)

    def admin_q(self, sql):
        return self.admin.execute(sql).fetchall()

    # ---- 행 단위 보안 (권역) -------------------------------------------------
    def test_region_manager_sees_only_own_region(self):
        se = {r[0] for r in self.q("u_e104", "SELECT DISTINCT uf FROM cust")}
        self.assertEqual(se, {"SP"})  # 테스트 계정도 SP
        self.assertEqual({r[0] for r in self.q("u_e105", "SELECT DISTINCT uf FROM cust")}, {"PR"})
        self.assertEqual({r[0] for r in self.q("u_e101", "SELECT DISTINCT uf FROM cust")}, {"AM"})

    def test_rls_cascades_to_orders_items_payments_reviews_refunds(self):
        expected = {r[0] for r in self.admin_q(
            "SELECT o.ord_no FROM ord o JOIN cust c USING (cust_no) WHERE c.uf = 'PR'")}
        for table in ["ord", "ord_item", "ord_pay", "rvw", "rfnd"]:
            got = {r[0] for r in self.q("u_e105", f"SELECT ord_no FROM {table}")}
            self.assertTrue(got <= expected, table)
        self.assertEqual({r[0] for r in self.q("u_e105", "SELECT ord_no FROM ord")}, expected)

    def test_aggregate_cannot_include_other_regions(self):
        total = self.admin_q("SELECT sum(sale_amt) FROM ord_item")[0][0]
        pr = self.admin_q("SELECT sum(i.sale_amt) FROM ord_item i JOIN ord o USING (ord_no) "
                          "JOIN cust c USING (cust_no) WHERE c.uf = 'PR'")[0][0]
        self.assertEqual(self.q("u_e105", "SELECT sum(sale_amt) FROM ord_item")[0][0], pr)
        self.assertNotEqual(pr, total)

    def test_hq_and_cs_see_all_rows(self):
        n = self.admin_q("SELECT count(*) FROM ord")[0][0]
        self.assertEqual(self.q("u_e001", "SELECT count(*) FROM ord")[0][0], n)
        self.assertEqual(self.q("u_e201", "SELECT count(*) FROM ord")[0][0], n)

    def test_legacy_summary_filtered_by_region(self):
        self.assertEqual({r[0] for r in self.q("u_e104", "SELECT DISTINCT uf FROM mon_sls_smry_old")}, {"SP"})

    def test_master_data_visible_to_all(self):
        for user in ["u_e104", "u_e201"]:
            self.assertEqual(self.q(user, "SELECT count(*) FROM prd")[0][0], len(self.tables["prd"]))
            self.assertEqual(self.q(user, "SELECT count(*) FROM cd_ord_stat")[0][0], 8)

    def test_unmapped_login_sees_nothing(self):
        self.admin.execute("DROP ROLE IF EXISTS u_stranger")
        self.admin.execute(f"CREATE ROLE u_stranger LOGIN PASSWORD '{PW}' IN ROLE r_agent")
        try:
            with self.connect("u_stranger") as c:
                self.assertEqual(c.execute("SELECT count(*) FROM ord").fetchone()[0], 0)
        finally:
            self.admin.execute("DROP ROLE u_stranger")

    # ---- 개인정보 ---------------------------------------------------------------
    def test_pii_columns_not_selectable(self):
        for user in ["u_e001", "u_e104", "u_e201"]:
            for col in ["cust_nm", "tel_no", "email", "*"]:
                self.denied(user, f"SELECT {col} FROM cust")
        self.assertGreater(self.q("u_e001", "SELECT count(cust_uid) FROM cust")[0][0], 0)

    def test_cs_gets_masked_contacts_only(self):
        rows = self.q("u_e201", "SELECT cust_no, cust_nm_mask, tel_mask, email_mask FROM v_cust_contact "
                      "WHERE cust_nm_mask IS NOT NULL")
        self.assertGreater(len(rows), 0)
        raw = dict((r[0], r[1:]) for r in self.admin_q("SELECT cust_no, cust_nm, tel_no, email FROM cust"))
        for cust_no, nm, tel, email in rows:
            rnm, rtel, remail = raw[cust_no]
            self.assertNotEqual(nm, rnm)
            self.assertTrue(nm.endswith(".") and rnm.startswith(nm.split()[0]))  # "Ana S."
            self.assertEqual(tel[-4:], rtel[-4:])
            self.assertIn("*", tel)
            self.assertEqual(email.split("@")[1], remail.split("@")[1])
            self.assertTrue(email.startswith(remail[0] + "***@"))
        for user in ["u_e001", "u_e104"]:
            self.denied(user, "SELECT * FROM v_cust_contact")

    def test_employee_tables_hidden(self):
        for user in ["u_e001", "u_e104", "u_e201"]:
            self.denied(user, "SELECT * FROM emp")
            self.denied(user, "SELECT * FROM db_user")

    # ---- 쓰기·우회 시도 -----------------------------------------------------------
    def test_no_writes_even_with_read_only_default_off(self):
        # 읽기 전용 기본값은 사용자가 끌 수 있다. 진짜 방어선은 권한이어야 한다.
        c = self.connect("u_e001")
        try:
            c.execute("SET default_transaction_read_only = off")
            for sql in ["INSERT INTO rgn VALUES ('X', 'x', 'x')", "UPDATE ord SET is_test = true",
                        "DELETE FROM rfnd", "CREATE TABLE t (a int)", "CREATE TEMP TABLE t (a int)",
                        "TRUNCATE rvw", "DROP TABLE prd", "CREATE FUNCTION f() RETURNS int AS 'SELECT 1' LANGUAGE sql"]:
                with self.assertRaises(self.psycopg.errors.InsufficientPrivilege, msg=sql):
                    c.execute(sql)
        finally:
            c.close()

    def test_cannot_escalate_role(self):
        self.denied("u_e104", "SET ROLE postgres")
        self.denied("u_e104", "SELECT set_config('role', 'postgres', false)")
        self.denied("u_e104", "SET ROLE u_e001")
        # SET ROLE r_agent는 허용되지만 권역 판단은 session_user 기준이라 얻는 게 없다
        c = self.connect("u_e104")
        try:
            c.execute("SET ROLE r_agent")
            self.assertEqual({r[0] for r in c.execute("SELECT DISTINCT uf FROM cust").fetchall()}, {"SP"})
        finally:
            c.close()

    def test_row_security_off_errors_instead_of_bypassing(self):
        c = self.connect("u_e104")
        try:
            c.execute("SET row_security = off")
            with self.assertRaises(self.psycopg.Error):
                c.execute("SELECT count(*) FROM cust").fetchall()
        finally:
            c.close()

    def test_statistics_do_not_leak_other_regions(self):
        self.admin.execute("ANALYZE cust")
        self.assertEqual(self.q("u_e104", "SELECT count(*) FROM pg_stats WHERE tablename = 'cust'")[0][0], 0)

    def test_session_defaults_read_only_and_timeout(self):
        self.assertEqual(self.q("u_e104", "SHOW default_transaction_read_only")[0][0], "on")
        self.assertEqual(self.q("u_e104", "SHOW statement_timeout")[0][0], "5s")


if __name__ == "__main__":
    unittest.main()
