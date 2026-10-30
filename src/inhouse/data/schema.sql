-- Tucanova 사내 DB (가상 고객사). 1단계: 표와 키만. 역할·RLS·마스킹 뷰는 2단계.
-- 실제 사내 DB처럼 설명은 짧고, 함정은 docs/schema-notes.md에만 적는다.
-- 카테고리(prd.ctgr_cd → ctgr)에는 일부러 FK가 없다: 원본에 번역 표에 없는 카테고리가 있다.

DROP VIEW IF EXISTS v_cust_contact;
DROP TABLE IF EXISTS db_user, rfnd, rvw, ord_pay, ord_item, ord, cust, mon_sls_smry_old, emp, geo_zip, prd, ctgr, slr,
    cd_ord_stat, st, rgn CASCADE;

CREATE TABLE rgn (
    rgn_cd    text PRIMARY KEY,
    rgn_nm    text NOT NULL,
    rgn_nm_ko text NOT NULL
);
COMMENT ON TABLE rgn IS '권역';

CREATE TABLE st (
    uf     char(2) PRIMARY KEY,
    rgn_cd text NOT NULL REFERENCES rgn
);
COMMENT ON TABLE st IS '주(UF) - 권역 매핑';

CREATE TABLE cd_ord_stat (
    stat_cd   smallint PRIMARY KEY,
    stat_nm   text NOT NULL,
    stat_desc text
);
COMMENT ON TABLE cd_ord_stat IS '주문 상태 코드';

CREATE TABLE slr (
    slr_id  text PRIMARY KEY,
    zip_pfx char(5),
    city_nm text,
    uf      char(2)
);
COMMENT ON TABLE slr IS '판매자';

CREATE TABLE ctgr (
    ctgr_cd    text PRIMARY KEY,
    ctgr_nm_en text
);
COMMENT ON TABLE ctgr IS '상품 카테고리 (영문명)';

CREATE TABLE prd (
    prd_id    text PRIMARY KEY,
    ctgr_cd   text,
    nm_len    int,
    desc_len  int,
    photo_qty int,
    wt_g      int,
    len_cm    int,
    ht_cm     int,
    wd_cm     int
);
COMMENT ON TABLE prd IS '상품';
COMMENT ON COLUMN prd.ctgr_cd IS '카테고리 코드';
COMMENT ON COLUMN prd.nm_len IS '상품명 글자 수';

CREATE TABLE geo_zip (
    zip_pfx char(5),
    lat     double precision,
    lng     double precision,
    city_nm text,
    uf      char(2)
);
COMMENT ON TABLE geo_zip IS '우편번호 앞 5자리 좌표';
CREATE INDEX ON geo_zip (zip_pfx);

CREATE TABLE cust (
    cust_no      text PRIMARY KEY,
    cust_uid     text NOT NULL,
    zip_pfx      char(5),
    city_nm      text,
    uf           char(2) REFERENCES st,
    cust_nm      text,
    tel_no       text,
    email        text,
    join_dt      date,
    deleted_at   timestamptz,
    is_test_acct boolean NOT NULL DEFAULT false
);
COMMENT ON TABLE cust IS '고객';
COMMENT ON COLUMN cust.cust_no IS '고객번호';
COMMENT ON COLUMN cust.cust_uid IS '고객 고유 ID';
COMMENT ON COLUMN cust.deleted_at IS '탈퇴 일시';
CREATE INDEX ON cust (cust_uid);

CREATE TABLE ord (
    ord_no       text PRIMARY KEY,
    cust_no      text NOT NULL REFERENCES cust,
    stat_cd      smallint NOT NULL REFERENCES cd_ord_stat,
    ord_dtm      timestamptz NOT NULL,
    aprv_dtm     timestamptz,
    dlv_crr_dtm  timestamptz,
    dlv_cust_dtm timestamptz,
    est_dlv_dt   date,
    is_test      boolean NOT NULL DEFAULT false
);
COMMENT ON TABLE ord IS '주문';
COMMENT ON COLUMN ord.stat_cd IS '주문 상태';
COMMENT ON COLUMN ord.ord_dtm IS '주문 일시';
COMMENT ON COLUMN ord.aprv_dtm IS '결제 승인 일시';
COMMENT ON COLUMN ord.dlv_crr_dtm IS '택배사 인계 일시';
COMMENT ON COLUMN ord.dlv_cust_dtm IS '배송 완료 일시';
COMMENT ON COLUMN ord.est_dlv_dt IS '예상 배송일';
CREATE INDEX ON ord (cust_no);
CREATE INDEX ON ord (ord_dtm);

CREATE TABLE ord_item (
    ord_no       text NOT NULL REFERENCES ord,
    item_seq     int NOT NULL,
    prd_id       text NOT NULL REFERENCES prd,
    slr_id       text NOT NULL REFERENCES slr,
    ship_lmt_dtm timestamptz,
    sale_amt     numeric(12, 2) NOT NULL,
    dlv_fee      numeric(12, 2) NOT NULL,
    PRIMARY KEY (ord_no, item_seq)
);
COMMENT ON TABLE ord_item IS '주문 품목';
COMMENT ON COLUMN ord_item.sale_amt IS '판매가';
COMMENT ON COLUMN ord_item.dlv_fee IS '배송비';

CREATE TABLE ord_pay (
    ord_no   text NOT NULL REFERENCES ord,
    pay_seq  int NOT NULL,
    pay_mthd text,
    inst_cnt int,
    pay_amt  numeric(12, 2) NOT NULL,
    PRIMARY KEY (ord_no, pay_seq)
);
COMMENT ON TABLE ord_pay IS '결제';
COMMENT ON COLUMN ord_pay.inst_cnt IS '할부 개월';

CREATE TABLE rvw (
    rvw_sn  int PRIMARY KEY,
    rvw_id  text NOT NULL,
    ord_no  text NOT NULL REFERENCES ord,
    score   smallint,
    title   text,
    msg     text,
    crt_dt  date,
    ans_dtm timestamptz
);
COMMENT ON TABLE rvw IS '리뷰';

CREATE TABLE rfnd (
    rfnd_no  int PRIMARY KEY,
    ord_no   text NOT NULL REFERENCES ord,
    rfnd_amt numeric(12, 2) NOT NULL,
    rfnd_dtm timestamptz NOT NULL,
    rsn_cd   text
);
COMMENT ON TABLE rfnd IS '환불';

CREATE TABLE emp (
    emp_no  text PRIMARY KEY,
    emp_nm  text NOT NULL,
    role_cd text NOT NULL,
    rgn_cd  text REFERENCES rgn,
    email   text
);
COMMENT ON TABLE emp IS '임직원';

CREATE TABLE mon_sls_smry_old (
    yyyymm    char(6) NOT NULL,
    uf        char(2) NOT NULL,
    sls_amt_k numeric(12, 1),
    ord_cnt   int,
    PRIMARY KEY (yyyymm, uf)
);
COMMENT ON TABLE mon_sls_smry_old IS '월별 매출 요약 (구 시스템)';
