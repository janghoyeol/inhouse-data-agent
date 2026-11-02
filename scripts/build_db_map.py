"""Tucanova DB 지도(HTML)를 실제 DB 카탈로그 + 지표 사전에서 만든다. 정답 SQL을 쓰는 사람이 옆에 띄워 두는 용도.

사용법: DATABASE_URL=postgresql://postgres:...@.../tucanova python scripts/build_db_map.py docs/db-map.html
표·열·외래키·행 수는 DB에서, 열 주의사항은 metrics.yaml의 column_notes에서, 쉬운 설명은 아래 MEANING에서 온다.
"""
from __future__ import annotations

import html
import os
import sys

import psycopg

from inhouse.agent import knowledge

# 직원 역할에 보이지 않는 표는 지도에서 뺀다
HIDDEN = {"emp", "db_user"}

GROUPS = [
    ("주문", "주문 한 건과 거기 딸린 것들. 질문 대부분이 여기서 시작한다.", ["ord", "ord_item", "ord_pay", "rfnd", "rvw"]),
    ("사람·상품", "누가 샀고, 누가 팔았고, 무엇을 팔았나.", ["cust", "slr", "prd", "ctgr"]),
    ("코드·지역", "숫자·약어로 된 값의 뜻을 풀어 주는 표.", ["cd_ord_stat", "st", "rgn", "geo_zip"]),
    ("특수", "쓰기 전에 주의가 필요한 표와 뷰.", ["mon_sls_smry_old", "v_cust_contact"]),
]

TABLE_KO = {
    "ord": ("주문", "주문 1건 = 1행. 언제, 누가(고객번호), 어떤 상태인지."),
    "ord_item": ("주문 품목", "주문 안의 상품 1개 = 1행. 판매가·배송비가 여기 있다. 매출은 이 표로 센다."),
    "ord_pay": ("결제", "결제 1건 = 1행. 한 주문에 여러 건일 수 있다(바우처 분할)."),
    "rfnd": ("환불", "환불 1건 = 1행. 취소 주문은 전액 환불."),
    "rvw": ("리뷰", "리뷰 1건 = 1행. 주문 하나에 여러 건일 수 있다."),
    "cust": ("고객", "주문할 때마다 1행이 생긴다(고객번호가 주문마다 다름). 같은 사람은 cust_uid가 같다."),
    "slr": ("판매자", "판매자 1곳 = 1행."),
    "prd": ("상품", "상품 1개 = 1행. 카테고리 코드는 포르투갈어."),
    "ctgr": ("카테고리", "카테고리 코드(포르투갈어) → 영문 이름."),
    "cd_ord_stat": ("주문 상태 코드", "주문 상태 숫자의 뜻. 60 배송 완료, 90 취소 등."),
    "st": ("주(州)", "브라질 주 27개 → 소속 권역."),
    "rgn": ("권역", "브라질 5개 권역의 이름."),
    "geo_zip": ("우편번호 좌표", "우편번호 앞 5자리 → 위도·경도. 같은 앞자리에 여러 행."),
    "mon_sls_smry_old": ("구 시스템 월 매출 요약", "2017년까지만, 천 헤알 단위, 지금 매출 정의와 다름."),
    "v_cust_contact": ("고객 연락처(마스킹)", "CS 담당만 볼 수 있다. 이름·전화·이메일이 가려져 있다."),
}

MEANING = {
    "ord.ord_no": "주문번호", "ord.cust_no": "고객번호 (cust 표와 연결)", "ord.stat_cd": "주문 상태 숫자",
    "ord.ord_dtm": "주문한 순간", "ord.aprv_dtm": "결제 승인된 순간", "ord.dlv_crr_dtm": "택배사에 넘긴 순간",
    "ord.dlv_cust_dtm": "고객에게 도착한 순간", "ord.est_dlv_dt": "예상 도착일", "ord.is_test": "테스트 주문인지",
    "ord_item.ord_no": "주문번호", "ord_item.item_seq": "주문 안 품목 순번 (1, 2, 3…)", "ord_item.prd_id": "상품 ID",
    "ord_item.slr_id": "판매자 ID", "ord_item.ship_lmt_dtm": "출고 기한", "ord_item.sale_amt": "판매가 (헤알)",
    "ord_item.dlv_fee": "배송비 (헤알)",
    "ord_pay.ord_no": "주문번호", "ord_pay.pay_seq": "결제 순번", "ord_pay.pay_mthd": "결제 수단",
    "ord_pay.inst_cnt": "할부 개월 수", "ord_pay.pay_amt": "결제 금액 (헤알)",
    "rfnd.rfnd_no": "환불번호", "rfnd.ord_no": "주문번호", "rfnd.rfnd_amt": "환불 금액 (헤알)",
    "rfnd.rfnd_dtm": "환불한 순간", "rfnd.rsn_cd": "환불 사유 코드",
    "rvw.rvw_sn": "리뷰 행 번호 (진짜 키)", "rvw.rvw_id": "리뷰 ID (중복 있음)", "rvw.ord_no": "주문번호",
    "rvw.score": "점수 1~5", "rvw.title": "리뷰 제목", "rvw.msg": "리뷰 내용", "rvw.crt_dt": "리뷰 작성일",
    "rvw.ans_dtm": "리뷰 답변 순간",
    "cust.cust_no": "고객번호 (주문마다 새로)", "cust.cust_uid": "사람 ID (같은 사람이면 같음)",
    "cust.zip_pfx": "우편번호 앞 5자리", "cust.city_nm": "도시", "cust.uf": "주 코드 2자리 (SP, RJ…)",
    "cust.cust_nm": "이름 (개인정보)", "cust.tel_no": "전화번호 (개인정보)", "cust.email": "이메일 (개인정보)",
    "cust.join_dt": "가입일", "cust.deleted_at": "탈퇴한 순간 (탈퇴 안 했으면 비어 있음)",
    "cust.is_test_acct": "테스트 계정인지",
    "slr.slr_id": "판매자 ID", "slr.zip_pfx": "우편번호 앞 5자리", "slr.city_nm": "도시", "slr.uf": "주 코드 2자리",
    "prd.prd_id": "상품 ID", "prd.ctgr_cd": "카테고리 코드 (포르투갈어)", "prd.nm_len": "상품명 글자 수",
    "prd.desc_len": "설명 글자 수", "prd.photo_qty": "사진 수", "prd.wt_g": "무게 (g)", "prd.len_cm": "길이 (cm)",
    "prd.ht_cm": "높이 (cm)", "prd.wd_cm": "너비 (cm)",
    "ctgr.ctgr_cd": "카테고리 코드 (포르투갈어)", "ctgr.ctgr_nm_en": "카테고리 영문 이름",
    "cd_ord_stat.stat_cd": "상태 숫자", "cd_ord_stat.stat_nm": "상태 이름 (영어)", "cd_ord_stat.stat_desc": "상태 설명 (한국어)",
    "st.uf": "주 코드 2자리", "st.rgn_cd": "권역 코드",
    "rgn.rgn_cd": "권역 코드 (N, NE, CO, SE, S)", "rgn.rgn_nm": "권역 이름 (포르투갈어)", "rgn.rgn_nm_ko": "권역 이름 (한국어)",
    "geo_zip.zip_pfx": "우편번호 앞 5자리", "geo_zip.lat": "위도", "geo_zip.lng": "경도", "geo_zip.city_nm": "도시",
    "geo_zip.uf": "주 코드 2자리",
    "mon_sls_smry_old.yyyymm": "연월 (201711 형식)", "mon_sls_smry_old.uf": "주 코드 2자리",
    "mon_sls_smry_old.sls_amt_k": "매출 (천 헤알)", "mon_sls_smry_old.ord_cnt": "주문 수",
    "v_cust_contact.cust_no": "고객번호", "v_cust_contact.cust_uid": "사람 ID", "v_cust_contact.uf": "주 코드",
    "v_cust_contact.cust_nm_mask": "가려진 이름 (Ana S.)", "v_cust_contact.tel_mask": "가려진 전화번호",
    "v_cust_contact.email_mask": "가려진 이메일",
}

PII = {"cust.cust_nm", "cust.tel_no", "cust.email"}

TRAPS = [
    ("T1", "테스트 주문", "ord.is_test가 true인 주문은 항상 뺀다. 9,999.99짜리 가짜 품목이 섞여 있다."),
    ("T2", "날짜와 시간대", "ord_dtm 같은 시각 열을 ::date로 자르면 접속한 프로그램의 시간대로 잘린다 (DBeaver는 한국 시간). 날짜는 (ord_dtm AT TIME ZONE 'America/Sao_Paulo')::date 로."),
    ("T3", "고객번호와 사람", "cust_no는 주문마다 새로 생긴다. 사람 수·재구매는 cust_uid로 센다."),
    ("T4", "상태는 숫자", "stat_cd는 숫자. 뜻은 cd_ord_stat에 있다. 90 취소, 91 주문 불가."),
    ("T5", "환불은 다른 표", "환불은 rfnd에 따로 있다. 취소 주문의 전액 환불을 매출에서 또 빼지 않는다."),
    ("T6", "구 시스템 요약표", "mon_sls_smry_old는 천 헤알 단위이고 정의가 다르다 (배송비 포함, 취소만 제외)."),
    ("T7", "결제 ≠ 매출", "결제 합과 품목 합은 다르다 (바우처·할부). 매출은 ord_item.sale_amt로."),
    ("T8", "카테고리 빈칸", "카테고리 없는 상품 610개, 번역 표에 없는 코드 2개. ctgr는 LEFT JOIN."),
    ("T9", "리뷰 중복", "주문 하나에 리뷰 여러 건, rvw_id도 중복. 주문당 최신 1건만."),
    ("T10", "좌표 중복", "geo_zip은 우편번호 앞자리마다 여러 행. 그대로 JOIN 하면 행이 불어난다."),
    ("T11", "탈퇴 고객", "탈퇴하면 개인정보가 지워지고 deleted_at만 남는다."),
    ("T12", "약어 열 이름", "sale_amt(판매가), dlv_fee(배송비), inst_cnt(할부), uf(주)… 이 지도에서 뜻을 찾는다."),
    ("T13", "SELECT * 금지(고객 표)", "cust의 개인정보 열은 권한이 없어서 SELECT * 가 오류 난다. 열을 골라 쓴다."),
    ("T14", "권역 범위", "권역 매니저 계정으로는 자기 권역 데이터만 보인다. DB가 알아서 거른다."),
]


def simple_type(t: str) -> str:
    if t.startswith("timestamp"):
        return "시각"
    if t == "date":
        return "날짜"
    if t.startswith(("integer", "smallint")):
        return "정수"
    if t.startswith("numeric"):
        return "금액·소수"
    if t.startswith("double"):
        return "소수"
    if t == "boolean":
        return "참/거짓"
    if t.startswith("character("):
        return f"글자 {t[10:-1]}자리"
    return "글자"


def load_catalog(dsn: str):
    with psycopg.connect(dsn) as conn:
        cols = conn.execute("""
            SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod)
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace JOIN pg_attribute a ON a.attrelid = c.oid
            WHERE n.nspname = 'public' AND c.relkind IN ('r', 'v') AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY c.relname, a.attnum""").fetchall()
        pks = {(r[0], r[1]) for r in conn.execute("""
            SELECT c.relname, a.attname FROM pg_index i
            JOIN pg_class c ON c.oid = i.indrelid JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey)
            WHERE i.indisprimary""").fetchall()}
        fks = {}
        for tbl, col, ref_tbl, ref_col in conn.execute("""
            SELECT c.relname, a.attname, rc.relname, ra.attname
            FROM pg_constraint k
            JOIN pg_class c ON c.oid = k.conrelid JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.conkey[1]
            JOIN pg_class rc ON rc.oid = k.confrelid JOIN pg_attribute ra ON ra.attrelid = rc.oid AND ra.attnum = k.confkey[1]
            WHERE k.contype = 'f'""").fetchall():
            fks[(tbl, col)] = (ref_tbl, ref_col)
        counts = {}
        for (tbl,) in conn.execute("SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                                    "WHERE n.nspname = 'public' AND relkind IN ('r', 'v')").fetchall():
            counts[tbl] = conn.execute(f'SELECT count(*) FROM "{tbl}"').fetchone()[0]
    tables: dict[str, list[tuple[str, str]]] = {}
    for tbl, col, typ in cols:
        tables.setdefault(tbl, []).append((col, typ))
    return tables, pks, fks, counts


def e(s) -> str:
    return html.escape(str(s), quote=True)


def mermaid(fks) -> str:
    lines = ["erDiagram"]
    for (tbl, col), (ref, _) in sorted(fks.items()):
        if tbl in HIDDEN or ref in HIDDEN:
            continue
        lines.append(f'    {ref} ||--o{{ {tbl} : "{col}"')
    return "\n".join(lines)


def render(tables, pks, fks, counts) -> str:
    notes = knowledge.load().get("column_notes", {})
    metrics = knowledge.load()["metrics"]
    cards = []
    for gname, gdesc, names in GROUPS:
        group_cards = []
        for t in names:
            ko, desc = TABLE_KO[t]
            rows = []
            for col, typ in tables[t]:
                key = f"{t}.{col}"
                note = notes.get(t, {}).get(col)
                badges = []
                if (t, col) in pks:
                    badges.append('<span class="badge key">키</span>')
                if (t, col) in fks:
                    ref, rcol = fks[(t, col)]
                    badges.append(f'<a class="badge link" href="#t-{ref}">→ {e(ref)}</a>')
                if key in PII:
                    badges.append('<span class="badge lock">권한 없음</span>')
                search = " ".join([t, ko, col, MEANING.get(key, ""), note or ""]).lower()
                rows.append(
                    f'<tr data-q="{e(search)}"><td class="col"><code>{e(col)}</code></td>'
                    f'<td class="mean">{e(MEANING.get(key, ""))} {"".join(badges)}'
                    + (f'<div class="note">⚠ {e(note)}</div>' if note else "")
                    + f'</td><td class="type">{simple_type(typ)}</td></tr>')
            access = ("CS 담당만" if t == "v_cust_contact" else
                      "권역 매니저는 자기 권역만" if t in {"ord", "ord_item", "ord_pay", "rfnd", "rvw", "cust", "mon_sls_smry_old"}
                      else "모두 전체")
            group_cards.append(f"""
      <article class="tcard" id="t-{t}" data-table="{t}">
        <header>
          <h3><code>{t}</code> <span class="ko">{e(ko)}</span></h3>
          <p class="meta"><span>{counts[t]:,}행</span><span>{access}</span></p>
          <p class="desc">{e(desc)}</p>
        </header>
        <div class="scroll"><table>
          <thead><tr><th>열</th><th>뜻</th><th>종류</th></tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table></div>
      </article>""")
        cards.append(f"""
    <section class="group" data-group>
      <div class="ghead"><h2>{e(gname)}</h2><p>{e(gdesc)}</p></div>
      <div class="grid">{''.join(group_cards)}</div>
    </section>""")
    traps = "".join(f'<li data-q="{e((c + n + d).lower())}"><b>{c}</b> <span class="tn">{e(n)}</span><p>{e(d)}</p></li>'
                    for c, n, d in TRAPS)
    mets = "".join(f'<li><b>{e(m["name"])}</b> <code>{e(m["id"])}</code><p>{e(m["definition"])}</p></li>' for m in metrics)
    return TEMPLATE.replace("%%CARDS%%", "".join(cards)).replace("%%TRAPS%%", traps).replace(
        "%%METRICS%%", mets).replace("%%MERMAID%%", e(mermaid(fks)))


TEMPLATE = """<title>Tucanova DB 지도</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
/* 레이아웃: 위에 검색창(고정), 아래로 분류별 표 카드 2열 → 좁으면 1열. 함정·지표는 맨 아래 참고 목록 */
:root {
  --bg: #f4f6f3; --panel: #ffffff; --ink: #1d2420; --muted: #5d6a63; --line: #dde3de;
  --accent: #0f6b5c; --accent-soft: #e0efe9; --warn: #a3471a; --warn-soft: #fbeee5; --lock: #6b4fa0;
  --sans: "IBM Plex Sans KR", "Apple SD Gothic Neo", "Malgun Gothic", sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, Consolas, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #121715; --panel: #1a211e; --ink: #e4ebe7; --muted: #9aa8a1; --line: #2c3632;
  --accent: #5cc2ad; --accent-soft: #1d3530; --warn: #f0a27a; --warn-soft: #3a2519; --lock: #b9a2e6; color-scheme: dark } }
:root[data-theme="dark"] {
  --bg: #121715; --panel: #1a211e; --ink: #e4ebe7; --muted: #9aa8a1; --line: #2c3632;
  --accent: #5cc2ad; --accent-soft: #1d3530; --warn: #f0a27a; --warn-soft: #3a2519; --lock: #b9a2e6; color-scheme: dark }
body { background: var(--bg); color: var(--ink); font-family: var(--sans); font-size: 15px; line-height: 1.55; }
body::before { content: ""; position: fixed; inset: 0 0 auto; height: env(safe-area-inset-top, 0px); z-index: 10; background: var(--bg); }
.wrap { max-width: 1180px; margin: 0 auto; padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); padding-block: 0 48px; }
code { font-family: var(--mono); font-size: .92em; }
h1 { font-size: 1.6rem; margin: 28px 0 4px; text-wrap: balance; }
.lead { color: var(--muted); margin: 0 0 4px; max-width: 70ch; }
.bar { position: sticky; top: env(safe-area-inset-top, 0px); z-index: 5; background: var(--bg); padding-block: 12px; border-bottom: 1px solid var(--line); display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.bar input { flex: 1 1 260px; min-width: 0; font: inherit; padding: 10px 12px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel); color: var(--ink); }
.bar input:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
.bar .count { color: var(--muted); font-size: .9rem; font-variant-numeric: tabular-nums; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; }
.chips button { font: inherit; font-size: .85rem; padding: 4px 10px; border-radius: 999px; border: 1px solid var(--line); background: var(--panel); color: var(--ink); cursor: pointer; }
.chips button:hover, .chips button:focus-visible { border-color: var(--accent); color: var(--accent); }
.how { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; margin: 20px 0 8px; }
.how div { background: var(--accent-soft); border-radius: 10px; padding: 12px 14px; }
.how b { display: block; margin-bottom: 2px; }
.how p { margin: 0; font-size: .92rem; }
.group { margin-top: 32px; }
.ghead { display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap; border-bottom: 2px solid var(--ink); padding-bottom: 6px; margin-bottom: 14px; }
.ghead h2 { margin: 0; font-size: 1.2rem; }
.ghead p { margin: 0; color: var(--muted); font-size: .92rem; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 500px), 1fr)); gap: 16px; }
.tcard { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; min-width: 0; scroll-margin-top: 80px; }
.tcard:target { outline: 2px solid var(--accent); }
.tcard header { padding: 14px 16px 6px; }
.tcard h3 { margin: 0; font-size: 1.1rem; display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
.tcard h3 code { color: var(--accent); font-size: 1.05rem; }
.ko { font-weight: 500; }
.meta { margin: 4px 0 0; display: flex; gap: 10px; flex-wrap: wrap; font-size: .82rem; color: var(--muted); font-variant-numeric: tabular-nums; }
.meta span + span::before { content: "·"; margin-right: 10px; }
.desc { margin: 6px 0 0; font-size: .92rem; }
.scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: .9rem; }
th { text-align: left; font-weight: 500; color: var(--muted); font-size: .78rem; letter-spacing: .04em; padding: 8px 16px; border-top: 1px solid var(--line); }
td { padding: 7px 16px; border-top: 1px solid var(--line); vertical-align: top; }
td.col { white-space: nowrap; }
td.type { white-space: nowrap; color: var(--muted); font-size: .82rem; }
.note { margin-top: 4px; font-size: .84rem; color: var(--warn); background: var(--warn-soft); padding: 4px 8px; border-radius: 6px; }
.badge { display: inline-block; font-size: .72rem; padding: 0 6px; border-radius: 4px; margin-left: 4px; border: 1px solid currentColor; text-decoration: none; vertical-align: 1px; }
.badge.key { color: var(--muted); }
.badge.link { color: var(--accent); }
.badge.lock { color: var(--lock); }
mark { background: var(--accent-soft); color: inherit; }
.ref { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 420px), 1fr)); gap: 24px; margin-top: 40px; }
.ref h2 { font-size: 1.2rem; border-bottom: 2px solid var(--ink); padding-bottom: 6px; margin: 0 0 10px; }
.ref ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
.ref li { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 8px 12px; }
.ref li p { margin: 2px 0 0; font-size: .9rem; color: var(--muted); }
.ref li b { color: var(--warn); margin-right: 4px; font-variant-numeric: tabular-nums; }
.ref .mets li b { color: var(--accent); }
.diagram { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 12px; overflow-x: auto; margin-top: 40px; }
.diagram h2 { font-size: 1.2rem; margin: 0 0 8px; }
.empty { color: var(--muted); margin-top: 24px; }
@media (prefers-reduced-motion: no-preference) { html { scroll-behavior: smooth; } }
</style>

<div class="wrap">
  <h1>Tucanova DB 지도</h1>
  <p class="lead">정답 SQL을 쓸 때 옆에 띄워 두는 표·열 사전. 표 이름, 열 이름, 쉬운 뜻, 주의사항(⚠)을 한 화면에 모았다.
  데이터 기간은 2016-09 ~ 2018-10, 금액 단위는 헤알(BRL).</p>

  <div class="bar">
    <label for="q" class="count" style="flex-basis:100%">질문 속 단어로 찾기 (예: 고객, 매출, 취소, 날짜)</label>
    <input id="q" type="search" placeholder="고객, 판매가, 탈퇴, uf …" autocomplete="off">
    <div class="chips" aria-label="자주 찾는 단어">
      <button type="button" data-w="고객">고객</button><button type="button" data-w="판매가">판매가</button>
      <button type="button" data-w="취소">취소</button><button type="button" data-w="시각">시각</button>
      <button type="button" data-w="탈퇴">탈퇴</button><button type="button" data-w="카테고리">카테고리</button>
      <button type="button" data-w="리뷰">리뷰</button><button type="button" data-w="">모두 보기</button>
    </div>
    <span class="count" id="count" aria-live="polite"></span>
  </div>

  <div class="how">
    <div><b>1. 질문을 단어로 쪼갠다</b><p>"구매한 <u>고객</u> 수" → 고객이 어느 표·열에 있는지 위에서 찾는다.</p></div>
    <div><b>2. ⚠ 주의사항을 읽는다</b><p>열마다 붙은 ⚠ 줄과 아래 함정 목록(T1~T14)이 흔한 실수를 막아 준다.</p></div>
    <div><b>3. 지표 단어는 정의를 따른다</b><p>매출·주문 수·고객 수 같은 단어는 맨 아래 지표 정의대로 센다.</p></div>
  </div>

  %%CARDS%%
  <p class="empty" id="empty" hidden>찾는 단어가 들어간 열이 없습니다. 다른 표현으로 찾아보세요 (예: 사람 → 고객).</p>

  <div class="diagram">
    <h2>표 연결 지도</h2>
    <p class="lead">선은 JOIN 할 수 있는 길이다. 선 위의 이름이 두 표를 잇는 열.</p>
    <pre class="mermaid">%%MERMAID%%</pre>
  </div>

  <div class="ref">
    <section><h2>함정 T1~T14</h2><ul id="traps">%%TRAPS%%</ul></section>
    <section class="mets"><h2>합의된 지표 정의</h2><ul>%%METRICS%%</ul></section>
  </div>
</div>

<script>
(() => {
  const q = document.getElementById('q');
  const rows = [...document.querySelectorAll('tr[data-q]')];
  const cards = [...document.querySelectorAll('.tcard')];
  const groups = [...document.querySelectorAll('[data-group]')];
  const traps = [...document.querySelectorAll('#traps li')];
  const count = document.getElementById('count');
  const empty = document.getElementById('empty');
  function apply() {
    const w = q.value.trim().toLowerCase();
    let n = 0;
    rows.forEach(r => { const hit = !w || r.dataset.q.includes(w); r.hidden = !hit; if (hit && w) n++; });
    cards.forEach(c => { c.hidden = !!w && !c.querySelector('tr[data-q]:not([hidden])'); });
    groups.forEach(g => { g.hidden = !!w && !g.querySelector('.tcard:not([hidden])'); });
    traps.forEach(t => { t.hidden = !!w && !t.dataset.q.includes(w); });
    count.textContent = w ? `열 ${n}개` : '';
    empty.hidden = !w || n > 0;
  }
  q.addEventListener('input', apply);
  document.querySelectorAll('.chips button').forEach(b => b.addEventListener('click', () => { q.value = b.dataset.w; apply(); q.focus(); }));
})();
</script>
"""


if __name__ == "__main__":
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("DATABASE_URL(슈퍼유저 또는 표 소유자)이 필요합니다")
    tables, pks, fks, counts = load_catalog(dsn)
    out = sys.argv[1] if len(sys.argv) > 1 else "docs/db-map.html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(render(tables, pks, fks, counts))
    print(f"완료: {out}")
