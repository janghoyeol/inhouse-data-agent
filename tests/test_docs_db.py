"""2a-2 문서 권한 통합 테스트: 직원 역할로 접속해 보이는 문서·조각을 확인하고 우회를 시도한다 (ADR 0004 결정 4).

기대값은 SQL 함수가 아니라 문서 머리말에서 파이썬으로 따로 계산한다.
"""
import os
import unittest

from inhouse.data import docs, load, overlay, transform
from tests.fixtures import mini_raw

DSN = os.environ.get("TEST_DATABASE_URL")
PW = "test-user-pw"
TAGS = {"u_e001": {"all", "hq", "mgr"}, "u_e101": {"all", "mgr", "rgn:N"}, "u_e102": {"all", "mgr", "rgn:NE"},
        "u_e103": {"all", "mgr", "rgn:CO"}, "u_e104": {"all", "mgr", "rgn:SE"}, "u_e105": {"all", "mgr", "rgn:S"},
        "u_e201": {"all", "cs"}, "u_e202": {"all", "cs"}}


@unittest.skipUnless(DSN, "TEST_DATABASE_URL 없음")
class TestDocSecurity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg.conninfo import make_conninfo
        cls.psycopg = psycopg
        tables = overlay.apply(transform.build(mini_raw()), seed=7, test_orders=3, refund_rate=0.5, withdraw_rate=0.34)
        load.load(tables, DSN, log=lambda m: None, user_password=PW)
        cls.corpus = docs.read_corpus()
        cls.admin = psycopg.connect(DSN, autocommit=True)
        cls.conns = {u: psycopg.connect(make_conninfo(DSN, user=u, password=PW), autocommit=True) for u in TAGS}

    @classmethod
    def tearDownClass(cls):
        for c in cls.conns.values():
            c.close()
        cls.admin.close()

    def q(self, user, sql, params=None):
        return self.conns[user].execute(sql, params).fetchall()

    def expected(self, user):
        return {d.doc_key for d in self.corpus if TAGS[user] & set(d.audience)}

    def test_loaded_everything(self):
        self.assertEqual(self.admin.execute("SELECT count(*) FROM doc").fetchone()[0], 30)
        n_chunks = sum(len(d.chunks) for d in self.corpus)
        self.assertEqual(self.admin.execute("SELECT count(*) FROM doc_chunk").fetchone()[0], n_chunks)

    def test_each_employee_sees_exactly_their_documents(self):
        for user in TAGS:
            got = {r[0] for r in self.q(user, "SELECT doc_key FROM doc")}
            self.assertEqual(got, self.expected(user), user)

    def test_chunks_follow_documents(self):
        for user in TAGS:
            got = {r[0] for r in self.q(user, "SELECT DISTINCT doc_key FROM doc_chunk")}
            self.assertEqual(got, self.expected(user), user)

    def test_nobody_sees_hr_or_reads_hidden_text(self):
        for user in TAGS:
            self.assertEqual(self.q(user, "SELECT count(*) FROM doc_chunk WHERE doc_key LIKE 'HR-%'")[0][0], 0, user)
        # 검색으로도 못 찾는다 (성과급 기준 문구)
        hits = self.q("u_e001", "SELECT count(*) FROM doc_chunk WHERE content LIKE '%기본급의 0~20%%'")
        self.assertEqual(hits[0][0], 0)
        self.assertEqual(self.q("u_e201", "SELECT count(*) FROM doc_chunk WHERE doc_key LIKE 'RPT-SEC%'")[0][0], 0)
        self.assertEqual(self.q("u_e104", "SELECT count(*) FROM doc WHERE doc_id = 'MTG-NE-2018-02'")[0][0], 0)

    def test_search_vector_is_usable_by_employee(self):
        rows = self.q("u_e201", "SELECT doc_key FROM doc_chunk WHERE tsv @@ '환불 & 기한'::tsquery")
        self.assertTrue(rows)
        self.assertTrue({r[0] for r in rows} <= self.expected("u_e201"))

    def test_bypass_attempts(self):
        import psycopg
        for sql in ["SET ROLE u_e001", "SELECT set_config('role', 'u_e001', false)",
                    "INSERT INTO doc (doc_key) VALUES ('x')", "UPDATE doc SET audience = '{all}'",
                    "DELETE FROM doc_chunk", "SELECT * FROM db_user"]:
            with self.assertRaises(psycopg.Error, msg=sql):
                self.q("u_e201", sql)
        # set_config로 바꿀 세션 변수가 없다: 판단은 session_user
        self.q("u_e201", "SELECT set_config('app.user', 'u_e001', false)")
        self.assertEqual({r[0] for r in self.q("u_e201", "SELECT doc_key FROM doc")}, self.expected("u_e201"))
        # row_security를 끄면 숨은 행이 보이는 게 아니라 쿼리가 거절된다 (따로 접속해서 시험)
        from psycopg.conninfo import make_conninfo
        with psycopg.connect(make_conninfo(DSN, user="u_e201", password=PW), autocommit=True) as c:
            c.execute("SET row_security = off")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                c.execute("SELECT count(*) FROM doc_chunk")
        # 통계 뷰로 숨은 조각 내용이 새지 않는다
        self.assertEqual(self.q("u_e201", "SELECT count(*) FROM pg_stats WHERE tablename IN ('doc', 'doc_chunk')")[0][0],
                         0)

    def test_unmapped_login_sees_nothing(self):
        import psycopg
        from psycopg.conninfo import make_conninfo
        self.admin.execute("DROP ROLE IF EXISTS u_docstranger")
        self.admin.execute("CREATE ROLE u_docstranger LOGIN PASSWORD 'x-pw' IN ROLE r_agent")
        try:
            with psycopg.connect(make_conninfo(DSN, user="u_docstranger", password="x-pw")) as c:
                self.assertEqual(c.execute("SELECT count(*) FROM doc_chunk").fetchone()[0], 0)
        finally:
            self.admin.execute("DROP ROLE u_docstranger")


if __name__ == "__main__":
    unittest.main()
