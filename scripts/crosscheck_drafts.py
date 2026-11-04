"""Claude 초안 정답 SQL 30개 교차 검산 (eval/REVIEW.md).

정답 SQL과 다른 방식(날짜는 extract/to_char, 조인 대신 서브쿼리·EXISTS, DISTINCT ON 대신 row_number …)으로
같은 질문을 다시 계산해 값이 같은지 본다. 초안과 같은 실수를 반복하지 않도록 지표 사전 정의에서 바로 다시 썼다.

사용법: python scripts/crosscheck_drafts.py "postgresql://postgres:…@host/tucanova"   (전체 데이터를 보는 관리자 계정)
"""
import sys

import psycopg
import yaml

from inhouse.eval.match import matches
L = "(o.ord_dtm AT TIME ZONE 'America/Sao_Paulo')"   # 현지 시각 (timestamp)
V = "o.is_test = false AND o.stat_cd <> ALL (ARRAY[90,91])"
def yr(y, col=L): return f"extract(year FROM {col}) = {y}"
ALT = {
 "A01": "SELECT count(*) FROM (SELECT ctgr_cd FROM prd WHERE ctgr_cd IS NOT NULL GROUP BY ctgr_cd) t",
 "A02": "SELECT count(DISTINCT slr_id) FROM slr",
 "A03": "SELECT (SELECT stat_nm FROM cd_ord_stat s WHERE s.stat_cd=o.stat_cd), count(*) FROM ord o WHERE o.is_test IS FALSE GROUP BY o.stat_cd",
 "A04": "SELECT pay_mthd, count(*) FROM ord_pay WHERE ord_no NOT IN (SELECT ord_no FROM ord WHERE is_test) GROUP BY 1 ORDER BY 2 DESC",
 "A05": "SELECT inst_cnt FROM ord_pay p WHERE EXISTS (SELECT 1 FROM ord o WHERE o.ord_no=p.ord_no AND NOT o.is_test) ORDER BY inst_cnt DESC LIMIT 1",
 "A06": "SELECT wt_g FROM prd WHERE wt_g IS NOT NULL ORDER BY wt_g DESC LIMIT 1",
 "A07": "SELECT sum(CASE WHEN uf='SP' THEN 1 ELSE 0 END) FROM slr",
 "A08": "SELECT round(sum(inst_cnt)::numeric/count(inst_cnt),2) FROM ord_pay p JOIN ord o ON o.ord_no=p.ord_no AND o.is_test=false WHERE pay_mthd='credit_card'",
 "A09": "SELECT rsn_cd, count(rfnd_amt) FROM rfnd GROUP BY 1",
 "A10": f"SELECT count(*) FROM ord o WHERE {V} AND to_char({L},'YYYY-MM')='2018-08'",
 "B01": f"""SELECT cat, sum(amt) s FROM (SELECT CASE WHEN c.ctgr_nm_en IS NOT NULL THEN c.ctgr_nm_en WHEN p.ctgr_cd IS NOT NULL THEN p.ctgr_cd ELSE '미분류' END cat, i.sale_amt amt
          FROM ord_item i JOIN ord o ON o.ord_no=i.ord_no JOIN prd p ON p.prd_id=i.prd_id LEFT JOIN ctgr c ON c.ctgr_cd=p.ctgr_cd WHERE {V} AND {yr(2017)}) t GROUP BY cat ORDER BY s DESC LIMIT 5""",
 "B02": f"""SELECT (SELECT r.rgn_nm_ko FROM st JOIN rgn r USING(rgn_cd) WHERE st.uf=c.uf), count(*) FROM ord o JOIN cust c ON c.cust_no=o.cust_no WHERE {V} AND {yr(2017)} GROUP BY 1""",
 "B03": f"SELECT s.uf, sum(i.sale_amt) FROM ord_item i JOIN slr s ON s.slr_id=i.slr_id WHERE i.ord_no IN (SELECT ord_no FROM ord o WHERE {V} AND {yr(2018)}) GROUP BY 1 ORDER BY 2 DESC LIMIT 3",
 "B04": f"""SELECT uf, round(100.0*avg(CASE WHEN late THEN 1 ELSE 0 END),2) r FROM (SELECT c.uf, date_trunc('day', o.dlv_cust_dtm AT TIME ZONE 'America/Sao_Paulo')::date > o.est_dlv_dt late
          FROM ord o JOIN cust c ON c.cust_no=o.cust_no WHERE o.is_test=false AND o.stat_cd=60 AND o.dlv_cust_dtm IS NOT NULL AND {yr(2017)}) t GROUP BY uf HAVING count(*)>=300 ORDER BY r DESC LIMIT 3""",
 "B05": """SELECT category, round(avg(score),2) a FROM (
          SELECT DISTINCT r.ord_no, coalesce(c.ctgr_nm_en, p.ctgr_cd) category, r.score FROM
            (SELECT ord_no, score, row_number() OVER (PARTITION BY ord_no ORDER BY ans_dtm DESC NULLS LAST, rvw_sn DESC) rn FROM rvw) r
            JOIN ord o ON o.ord_no=r.ord_no AND NOT o.is_test JOIN ord_item i ON i.ord_no=r.ord_no JOIN prd p ON p.prd_id=i.prd_id AND p.ctgr_cd IS NOT NULL
            LEFT JOIN ctgr c ON c.ctgr_cd=p.ctgr_cd WHERE r.rn=1) t GROUP BY category HAVING count(*)>=500 ORDER BY a LIMIT 3""",
 "B06": f"""SELECT to_char({L},'YYYY-MM') ym, round(sum((SELECT coalesce(sum(sale_amt),0) FROM ord_item i WHERE i.ord_no=o.ord_no) - (SELECT coalesce(sum(rfnd_amt),0) FROM rfnd f WHERE f.ord_no=o.ord_no)),2)
          FROM ord o WHERE {V} AND {yr(2017)} GROUP BY 1 ORDER BY 1""",
 "B07": "SELECT count(*) FROM ord_item i WHERE i.ord_no IN (SELECT ord_no FROM ord WHERE NOT is_test) GROUP BY ord_no ORDER BY 1 DESC LIMIT 1",
 "B08": f"SELECT count(DISTINCT o.ord_no) FROM ord o JOIN ord_pay p ON p.ord_no=o.ord_no AND p.pay_mthd='voucher' WHERE {V} AND {yr(2017)}",
 "B09": f"""SELECT sum(i.sale_amt) FROM ord_item i JOIN ord o ON o.ord_no=i.ord_no WHERE {V} AND {yr(2018)} AND extract(quarter FROM {L})=1
          AND i.prd_id IN (SELECT prd_id FROM prd WHERE ctgr_cd IN (SELECT ctgr_cd FROM ctgr WHERE ctgr_nm_en='health_beauty'))""",
 "B10": f"""SELECT round(100.0*sum(CASE WHEN c.uf=s.uf THEN 1 ELSE 0 END)/count(*),2) FROM ord_item i JOIN ord o ON o.ord_no=i.ord_no JOIN cust c ON c.cust_no=o.cust_no JOIN slr s ON s.slr_id=i.slr_id WHERE {V} AND {yr(2017)}""",
 "C01": f"SELECT round(sum((SELECT coalesce(sum(sale_amt),0) FROM ord_item i WHERE i.ord_no=o.ord_no) - (SELECT coalesce(sum(rfnd_amt),0) FROM rfnd f WHERE f.ord_no=o.ord_no)),2) FROM ord o WHERE {V} AND {yr(2017)}",
 "C02": f"SELECT count(*) FROM (SELECT c.cust_uid FROM cust c JOIN ord o ON o.cust_no=c.cust_no WHERE {V} AND {yr(2018)} AND extract(month FROM {L})<=6 AND NOT c.is_test_acct GROUP BY 1) t",
 "C03": f"""SELECT round(100.0*count(DISTINCT cust_uid) FILTER (WHERE k>=2)/count(DISTINCT cust_uid),2) FROM (SELECT c.cust_uid, count(*) OVER (PARTITION BY c.cust_uid) k FROM ord o JOIN cust c ON c.cust_no=o.cust_no WHERE {V} AND {yr(2017)} AND NOT c.is_test_acct) t""",
 "C04": f"SELECT round(sum(i.sale_amt)/count(DISTINCT o.ord_no),2) FROM ord o LEFT JOIN ord_item i ON i.ord_no=o.ord_no WHERE {V} AND to_char({L},'YYYY-MM')='2017-11'",
 "C05": f"SELECT round(100.0*avg(CASE WHEN stat_cd IN (90,91) THEN 1 ELSE 0 END),2) FROM ord o WHERE NOT is_test AND {yr(2018)}",
 "C06": f"""SELECT round(100.0*count(DISTINCT f.ord_no)/(SELECT count(*) FROM ord o WHERE NOT is_test AND stat_cd=60 AND {yr(2017)}),2)
          FROM rfnd f JOIN ord o ON o.ord_no=f.ord_no WHERE f.rsn_cd<>'CNCL' AND NOT o.is_test AND o.stat_cd=60 AND {yr(2017)}""",
 "C07": f"SELECT round((sum(extract(epoch FROM o.dlv_cust_dtm) - extract(epoch FROM o.ord_dtm))/count(*)/86400)::numeric,2) FROM ord o WHERE NOT is_test AND stat_cd=60 AND dlv_cust_dtm IS NOT NULL AND {yr(2017)}",
 "C08": f"""SELECT round(100.0*avg(((dlv_cust_dtm AT TIME ZONE 'America/Sao_Paulo')::date > est_dlv_dt)::int),2) FROM ord o WHERE NOT is_test AND stat_cd=60 AND dlv_cust_dtm IS NOT NULL AND {yr(2018)} AND extract(quarter FROM {L})=1""",
 "C09": f"""SELECT round(avg(score),2) FROM (SELECT r.score, row_number() OVER (PARTITION BY r.ord_no ORDER BY r.ans_dtm DESC NULLS LAST, r.rvw_sn DESC) rn FROM rvw r JOIN ord o ON o.ord_no=r.ord_no WHERE NOT o.is_test AND {yr(2017)}) t WHERE rn=1""",
 "C10": f"SELECT round(sum(i.sale_amt)+sum(i.dlv_fee),2) FROM ord_item i WHERE i.ord_no IN (SELECT ord_no FROM ord o WHERE {V} AND {yr(2017)})",
}
qs = yaml.safe_load(open("eval/questions.yaml"))
qs = qs["questions"] if isinstance(qs, dict) else qs
bad = 0
with psycopg.connect(sys.argv[1]) as conn:
    for q in qs:
        if q.get("gold_by") != "claude-draft": continue
        cmp = q.get("compare") or {}
        run = lambda s: [list(r) for r in conn.execute(s).fetchall()]
        g, a = run(q["gold_sql"]), run(ALT[q["id"]])
        ok = matches(g, a, rate=cmp.get("rate", False), order_matters=cmp.get("order_matters", False))
        bad += not ok
        print(q["id"], "OK " if ok else "DIFF", g if len(g) <= 3 else f"{len(g)}행 {g[:3]}…", "" if ok else f"\n    alt: {a[:6]}")
print(f"{len(ALT) - bad}/{len(ALT)} 일치")
sys.exit(1 if bad else 0)
