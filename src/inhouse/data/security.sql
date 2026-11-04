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
        -- 새 역할은 기본값이 전부 NO(슈퍼유저·RLS 우회·역할 생성…)라 LOGIN만 준다. 관리형 Postgres(Neon 등)의
        -- 관리자는 슈퍼유저가 아니라서 이런 속성을 ALTER로 바꿀 수 없으므로, 이미 있는 역할은 바꾸지 않고 검사만 한다.
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = r.db_role) THEN
            EXECUTE format('CREATE ROLE %I LOGIN', r.db_role);
        END IF;
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = r.db_role
                   AND (rolsuper OR rolbypassrls OR rolreplication OR rolcreaterole OR rolcreatedb OR NOT rolinherit
                        OR NOT rolcanlogin)) THEN
            RAISE EXCEPTION '직원 역할 %의 속성이 위험하거나 로그인할 수 없습니다. 역할을 지우고 다시 적재하세요.', r.db_role;
        END IF;
        EXECUTE format('GRANT r_agent TO %I', r.db_role);
        IF r.role_cd IN ('CS', 'CS_LEAD') THEN
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

-- 2단계 문서 권한 (ADR 0004 결정 4): 문서의 열람 태그와 직원의 태그가 겹치면 보인다. 판단은 session_user.
-- 본사(분석가·운영책임자) = all·hq·mgr, 권역 매니저 = all·mgr·rgn:자기 권역, CS(담당·팀장) = all·cs, 매핑 없는 계정 = 없음
CREATE OR REPLACE FUNCTION tcn_doc_tags() RETURNS text[]
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT coalesce((
        SELECT CASE e.role_cd WHEN 'HQ_ANALYST' THEN ARRAY['all', 'hq', 'mgr']
                              WHEN 'HQ_OPS' THEN ARRAY['all', 'hq', 'mgr']
                              WHEN 'CS_LEAD' THEN ARRAY['all', 'cs']
                              WHEN 'RGN_MGR' THEN ARRAY['all', 'mgr', 'rgn:' || e.rgn_cd]
                              WHEN 'CS' THEN ARRAY['all', 'cs'] END
        FROM db_user d JOIN emp e USING (emp_no) WHERE d.db_role = session_user), '{}')
$$;
GRANT SELECT ON doc, doc_chunk TO r_agent;
ALTER TABLE doc ENABLE ROW LEVEL SECURITY;
CREATE POLICY p_tag ON doc FOR SELECT TO r_agent USING (audience && tcn_doc_tags());
ALTER TABLE doc_chunk ENABLE ROW LEVEL SECURITY;
CREATE POLICY p_tag ON doc_chunk FOR SELECT TO r_agent USING (doc_key IN (SELECT doc_key FROM doc));

-- 2단계 환불 제안·승인 (ADR 0004 결정 6) ---------------------------------------------------------------
-- 직원 역할에는 rfnd·rfnd_req·apr_limit 쓰기 권한이 없다. 쓰기는 아래 두 함수로만 하고, 함수가 규칙을 DB 안에서 검사한다.
-- 함수는 쓰기 트랜잭션에서만 동작한다: 에이전트의 run_sql은 읽기 전용 트랜잭션이라 이 함수들을 불러도 실패한다.
-- 에이전트에게는 제안 도구만 있고 승인 도구는 없다. 승인은 사람이 웹 화면에서 한다.
CREATE SEQUENCE rfnd_no_seq;
SELECT setval('rfnd_no_seq', (SELECT coalesce(max(rfnd_no), 0) + 1 FROM rfnd), false);

CREATE OR REPLACE FUNCTION propose_refund(p_ord_no text, p_rsn_cd text, p_amt numeric, p_basis text) RETURNS bigint
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    me record; o record; ceiling numeric; used numeric; req bigint;
BEGIN
    SELECT e.role_cd INTO me FROM db_user d JOIN emp e USING (emp_no) WHERE d.db_role = session_user;
    IF NOT FOUND OR me.role_cd NOT IN ('CS', 'CS_LEAD') THEN
        RAISE EXCEPTION '환불 제안은 CS 담당·CS 팀장만 할 수 있습니다';
    END IF;
    IF p_rsn_cd IS NULL OR p_rsn_cd NOT IN ('DMG', 'WRONG', 'LATE') THEN
        RAISE EXCEPTION '사유 코드는 DMG·WRONG·LATE 중 하나입니다 (CNCL은 주문 시스템만 넣습니다)';
    END IF;
    IF p_amt IS NULL OR p_amt <= 0 THEN RAISE EXCEPTION '환불 금액은 0보다 커야 합니다'; END IF;
    IF coalesce(trim(p_basis), '') = '' THEN RAISE EXCEPTION '근거(규정 판·조항)를 적어 주세요'; END IF;
    SELECT * INTO o FROM ord WHERE ord_no = p_ord_no FOR UPDATE;   -- 같은 주문의 제안을 한 줄로 세운다
    IF NOT FOUND THEN RAISE EXCEPTION '주문 %이 없습니다', p_ord_no; END IF;
    IF NOT EXISTS (SELECT 1 FROM cust c WHERE c.cust_no = o.cust_no AND c.uf IN (SELECT tcn_visible_uf())) THEN
        RAISE EXCEPTION '주문 %이 없습니다', p_ord_no;            -- 볼 수 없는 주문은 없는 것과 같게
    END IF;
    IF o.is_test THEN RAISE EXCEPTION '테스트 주문은 환불 대상이 아닙니다'; END IF;
    IF o.stat_cd <> 60 THEN RAISE EXCEPTION '배송 완료(60) 주문만 환불할 수 있습니다 (지금 상태 %)', o.stat_cd; END IF;
    IF EXISTS (SELECT 1 FROM rfnd WHERE ord_no = p_ord_no AND rsn_cd = p_rsn_cd)
       OR EXISTS (SELECT 1 FROM rfnd_req WHERE ord_no = p_ord_no AND rsn_cd = p_rsn_cd AND status = 'pending') THEN
        RAISE EXCEPTION '같은 주문에 같은 사유(%)의 환불이 이미 있습니다', p_rsn_cd;
    END IF;
    SELECT coalesce(sum(sale_amt + dlv_fee), 0) INTO ceiling FROM ord_item WHERE ord_no = p_ord_no;
    SELECT coalesce((SELECT sum(rfnd_amt) FROM rfnd WHERE ord_no = p_ord_no), 0)
         + coalesce((SELECT sum(amt) FROM rfnd_req WHERE ord_no = p_ord_no AND status = 'pending'), 0) INTO used;
    IF p_amt > ceiling - used THEN
        RAISE EXCEPTION '남은 환불 가능 금액(R$ %)을 넘습니다 (품목+배송비 %, 환불·대기 %)', ceiling - used, ceiling, used;
    END IF;
    INSERT INTO rfnd_req (ord_no, rsn_cd, amt, basis, proposed_by, expires_at)
    VALUES (p_ord_no, p_rsn_cd, p_amt, p_basis, session_user, now() + interval '72 hours')
    RETURNING req_no INTO req;
    RETURN req;
END $$;

CREATE OR REPLACE FUNCTION decide_refund(p_req_no bigint, p_approve boolean, p_note text DEFAULT NULL) RETURNS text
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    me record; r record; rgn text; lim record; n int;
    today date := (now() AT TIME ZONE 'America/Sao_Paulo')::date;
BEGIN
    SELECT e.role_cd, e.rgn_cd INTO me FROM db_user d JOIN emp e USING (emp_no) WHERE d.db_role = session_user;
    IF NOT FOUND OR me.role_cd NOT IN ('CS_LEAD', 'RGN_MGR', 'HQ_OPS') THEN
        RAISE EXCEPTION '환불 승인 권한이 없습니다 (승인권자: CS 팀장·권역 매니저·본사 운영책임자)';
    END IF;
    SELECT * INTO r FROM rfnd_req WHERE req_no = p_req_no FOR UPDATE;   -- 동시에 두 번 승인해도 한 번만
    IF NOT FOUND THEN RAISE EXCEPTION '제안 %이 없습니다', p_req_no; END IF;
    IF r.status <> 'pending' THEN RAISE EXCEPTION '이미 처리된 제안입니다 (상태: %)', r.status; END IF;
    IF r.proposed_by = session_user THEN RAISE EXCEPTION '본인이 제안한 환불은 승인할 수 없습니다'; END IF;
    IF me.role_cd = 'RGN_MGR' THEN
        SELECT st.rgn_cd INTO rgn FROM ord o JOIN cust c USING (cust_no) JOIN st USING (uf) WHERE o.ord_no = r.ord_no;
        IF rgn IS DISTINCT FROM me.rgn_cd THEN RAISE EXCEPTION '권역 매니저는 자기 권역 고객의 주문만 승인할 수 있습니다'; END IF;
    END IF;
    IF now() >= r.expires_at THEN
        UPDATE rfnd_req SET status = 'expired', decided_at = now() WHERE req_no = p_req_no;
        RETURN 'expired';
    END IF;
    IF NOT p_approve THEN
        IF coalesce(trim(p_note), '') = '' THEN RAISE EXCEPTION '거절 사유를 적어 주세요'; END IF;
        UPDATE rfnd_req SET status = 'rejected', decided_by = session_user, decided_at = now(), decision_note = p_note
        WHERE req_no = p_req_no;
        RETURN 'rejected';
    END IF;
    SELECT * INTO lim FROM apr_limit
    WHERE role_cd = me.role_cd AND eff_from <= today AND (eff_to IS NULL OR eff_to >= today);
    IF NOT FOUND THEN RAISE EXCEPTION '오늘 적용되는 승인 한도가 없습니다'; END IF;
    IF lim.max_amt IS NOT NULL AND r.amt > lim.max_amt THEN
        RAISE EXCEPTION '승인 한도(R$ %)를 넘습니다. 한도가 맞는 승인권자에게 넘기세요', lim.max_amt;
    END IF;
    INSERT INTO rfnd (rfnd_no, ord_no, rfnd_amt, rfnd_dtm, rsn_cd)
    VALUES (nextval('rfnd_no_seq'), r.ord_no, r.amt, now(), r.rsn_cd)
    RETURNING rfnd_no INTO n;
    UPDATE rfnd_req SET status = 'executed', decided_by = session_user, decided_at = now(), decision_note = p_note,
                        rfnd_no = n
    WHERE req_no = p_req_no;
    RETURN 'executed';
END $$;

REVOKE ALL ON FUNCTION propose_refund(text, text, numeric, text), decide_refund(bigint, boolean, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION propose_refund(text, text, numeric, text), decide_refund(bigint, boolean, text) TO r_agent;
GRANT SELECT ON rfnd_req, apr_limit TO r_agent;
ALTER TABLE rfnd_req ENABLE ROW LEVEL SECURITY;
CREATE POLICY p_rgn ON rfnd_req FOR SELECT TO r_agent USING (ord_no IN (SELECT ord_no FROM ord));
