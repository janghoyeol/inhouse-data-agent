-- 2단계: 역할·행 단위 보안(RLS)·마스킹 뷰 (ADR 0001 결정 6). load가 표를 새로 만들 때마다 다시 건다.
--
-- 원칙: 권한은 프롬프트가 아니라 DB가 거절한다.
-- - 직원마다 로그인 역할(u_e001 …)이 있고, 에이전트는 묻는 사람의 역할로 접속한다.
-- - 권역 판단은 session_user 기준. SET ROLE·set_config로 current_user를 바꿔도 결과가 안 바뀐다.
-- - 쓰기 권한은 아예 없다. 읽기 전용 기본값·타임아웃은 보조 장치일 뿐 (사용자가 끌 수 있음).
-- - 고객 개인정보 원문은 어떤 직원 역할로도 안 나간다. CS만 마스킹된 연락처 뷰를 본다.

DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'r_agent') THEN CREATE ROLE r_agent NOLOGIN; END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'r_cs') THEN CREATE ROLE r_cs NOLOGIN; END IF;
END $$;

-- 로그인 역할 → 직원
CREATE TABLE db_user (
    db_role name PRIMARY KEY,
    emp_no  text NOT NULL REFERENCES emp
);
INSERT INTO db_user SELECT 'u_' || lower(emp_no), emp_no FROM emp;

DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT d.db_role, e.role_cd FROM db_user d JOIN emp e USING (emp_no) LOOP
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = r.db_role) THEN
            EXECUTE format('CREATE ROLE %I LOGIN', r.db_role);
        END IF;
        EXECUTE format('ALTER ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION INHERIT',
                       r.db_role);
        EXECUTE format('GRANT r_agent TO %I', r.db_role);
        IF r.role_cd = 'CS' THEN
            EXECUTE format('GRANT r_cs TO %I', r.db_role);
        ELSE
            EXECUTE format('REVOKE r_cs FROM %I', r.db_role);
        END IF;
        EXECUTE format('ALTER ROLE %I SET default_transaction_read_only = on', r.db_role);
        EXECUTE format('ALTER ROLE %I SET statement_timeout = %L', r.db_role, '5s');
    END LOOP;
    EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', current_database());
END $$;

-- 지금 접속한 직원이 볼 수 있는 주(UF). 본사 분석가·CS는 전부, 권역 매니저는 자기 권역, 매핑 없는 계정은 없음.
CREATE OR REPLACE FUNCTION tcn_visible_uf() RETURNS SETOF char(2)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT st.uf
    FROM db_user d
    JOIN emp e USING (emp_no)
    JOIN st ON e.role_cd <> 'RGN_MGR' OR st.rgn_cd = e.rgn_cd
    WHERE d.db_role = session_user
$$;

-- 권한: 쓰기 없음, 직원·계정 표 없음, 고객 표는 개인정보 열을 뺀 열만
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
GRANT SELECT ON rgn, st, cd_ord_stat, slr, ctgr, prd, geo_zip,
                ord, ord_item, ord_pay, rvw, rfnd, mon_sls_smry_old TO r_agent;
GRANT SELECT (cust_no, cust_uid, zip_pfx, city_nm, uf, join_dt, deleted_at, is_test_acct) ON cust TO r_agent;

-- 행 단위 보안: 고객의 주(UF)로 거르고, 주문 → 품목·결제·리뷰·환불로 이어진다
-- (하위 표의 정책이 상위 표를 조회하면 상위 표의 정책이 다시 적용된다)
ALTER TABLE cust ENABLE ROW LEVEL SECURITY;
CREATE POLICY p_rgn ON cust FOR SELECT TO r_agent USING (uf IN (SELECT tcn_visible_uf()));
ALTER TABLE mon_sls_smry_old ENABLE ROW LEVEL SECURITY;
CREATE POLICY p_rgn ON mon_sls_smry_old FOR SELECT TO r_agent USING (uf IN (SELECT tcn_visible_uf()));
ALTER TABLE ord ENABLE ROW LEVEL SECURITY;
CREATE POLICY p_rgn ON ord FOR SELECT TO r_agent USING (cust_no IN (SELECT cust_no FROM cust));
ALTER TABLE ord_item ENABLE ROW LEVEL SECURITY;
CREATE POLICY p_rgn ON ord_item FOR SELECT TO r_agent USING (ord_no IN (SELECT ord_no FROM ord));
ALTER TABLE ord_pay ENABLE ROW LEVEL SECURITY;
CREATE POLICY p_rgn ON ord_pay FOR SELECT TO r_agent USING (ord_no IN (SELECT ord_no FROM ord));
ALTER TABLE rvw ENABLE ROW LEVEL SECURITY;
CREATE POLICY p_rgn ON rvw FOR SELECT TO r_agent USING (ord_no IN (SELECT ord_no FROM ord));
ALTER TABLE rfnd ENABLE ROW LEVEL SECURITY;
CREATE POLICY p_rgn ON rfnd FOR SELECT TO r_agent USING (ord_no IN (SELECT ord_no FROM ord));

-- CS 전용 마스킹 연락처: "Ana S.", "+55 11 ****-1234", "a***@example.com"
CREATE VIEW v_cust_contact WITH (security_barrier) AS
SELECT cust_no, cust_uid, uf,
       CASE WHEN cust_nm IS NOT NULL
            THEN split_part(cust_nm, ' ', 1) || ' ' || left(split_part(cust_nm, ' ', 2), 1) || '.' END AS cust_nm_mask,
       CASE WHEN tel_no IS NOT NULL THEN left(tel_no, 7) || '****-' || right(tel_no, 4) END AS tel_mask,
       CASE WHEN email IS NOT NULL THEN left(email, 1) || '***@' || split_part(email, '@', 2) END AS email_mask
FROM cust
WHERE uf IN (SELECT tcn_visible_uf());
COMMENT ON VIEW v_cust_contact IS '고객 연락처 (마스킹, CS 전용)';
GRANT SELECT ON v_cust_contact TO r_cs;
