"""Olist 원본 CSV → Tucanova 사내 DB 표 (ADR 0001 결정 2).

실제 데이터의 지저분함(중복 리뷰 ID, 카테고리 결측·미번역, 우편번호당 좌표 여러 행)은 고치지 않고 그대로 둔다.
바꾸는 것은 "이 회사의 DB라면 이렇게 저장했을 것"인 부분뿐이다:
- 열 이름을 사내 약어로, 주문 상태를 코드 값으로 (뜻은 코드 표에만)
- 시각은 현지(America/Sao_Paulo, 2016~2018은 서머타임 있음)를 UTC로 바꿔 저장
"""
from __future__ import annotations

import os

import pandas as pd

RAW_FILES = {
    "customers": "olist_customers_dataset.csv",
    "orders": "olist_orders_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "order_payments": "olist_order_payments_dataset.csv",
    "order_reviews": "olist_order_reviews_dataset.csv",
    "products": "olist_products_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "geolocation": "olist_geolocation_dataset.csv",
    "category_translation": "product_category_name_translation.csv",
}
LOCAL_TZ = "America/Sao_Paulo"

REGIONS = {  # 브라질 5개 권역 (IBGE)
    "N": ("Norte", "북부", ["AC", "AM", "AP", "PA", "RO", "RR", "TO"]),
    "NE": ("Nordeste", "북동부", ["AL", "BA", "CE", "MA", "PB", "PE", "PI", "RN", "SE"]),
    "CO": ("Centro-Oeste", "중서부", ["DF", "GO", "MS", "MT"]),
    "SE": ("Sudeste", "남동부", ["ES", "MG", "RJ", "SP"]),
    "S": ("Sul", "남부", ["PR", "RS", "SC"]),
}
ORDER_STATUS = {  # 레거시 코드 체계: 진행 순서대로 10단위, 90번대는 실패
    10: "created", 20: "approved", 30: "invoiced", 40: "processing",
    50: "shipped", 60: "delivered", 90: "canceled", 91: "unavailable",
}
STATUS_DESC = {
    "created": "주문 생성", "approved": "결제 승인", "invoiced": "송장 발행", "processing": "출고 준비",
    "shipped": "배송 중", "delivered": "배송 완료", "canceled": "주문 취소", "unavailable": "재고 없음(주문 불가)",
}


def read_raw(directory: str) -> dict[str, pd.DataFrame]:
    """모든 열을 문자열로 읽는다 (우편번호 앞자리 0 보존). 번역 파일의 BOM은 utf-8-sig로 제거."""
    return {stem: pd.read_csv(os.path.join(directory, name), dtype=str, keep_default_na=True, encoding="utf-8-sig")
            for stem, name in RAW_FILES.items()}


def _utc(col: pd.Series) -> pd.Series:
    local = pd.to_datetime(col, errors="coerce")
    # 서머타임 끝나는 시각의 겹치는 한 시간은 표준시로, 시작 때 없는 한 시간은 뒤로 민다
    return local.dt.tz_localize(LOCAL_TZ, ambiguous=False, nonexistent="shift_forward").dt.tz_convert("UTC")


def _num(col: pd.Series) -> pd.Series:
    return pd.to_numeric(col, errors="coerce")


def _int(col: pd.Series) -> pd.Series:
    return pd.to_numeric(col, errors="coerce").astype("Int64")


def build(raw: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    t: dict[str, pd.DataFrame] = {}
    t["rgn"] = pd.DataFrame([(cd, pt, ko) for cd, (pt, ko, _) in REGIONS.items()],
                            columns=["rgn_cd", "rgn_nm", "rgn_nm_ko"])
    t["st"] = pd.DataFrame([(uf, cd) for cd, (_, _, ufs) in REGIONS.items() for uf in ufs], columns=["uf", "rgn_cd"])
    t["cd_ord_stat"] = pd.DataFrame([(cd, nm, STATUS_DESC[nm]) for cd, nm in ORDER_STATUS.items()],
                                    columns=["stat_cd", "stat_nm", "stat_desc"])

    c = raw["customers"]
    t["cust"] = pd.DataFrame({
        "cust_no": c.customer_id, "cust_uid": c.customer_unique_id, "zip_pfx": c.customer_zip_code_prefix,
        "city_nm": c.customer_city, "uf": c.customer_state,
    })
    s = raw["sellers"]
    t["slr"] = pd.DataFrame({"slr_id": s.seller_id, "zip_pfx": s.seller_zip_code_prefix,
                             "city_nm": s.seller_city, "uf": s.seller_state})
    tr = raw["category_translation"]
    t["ctgr"] = pd.DataFrame({"ctgr_cd": tr.product_category_name, "ctgr_nm_en": tr.product_category_name_english})
    p = raw["products"]
    t["prd"] = pd.DataFrame({
        "prd_id": p.product_id, "ctgr_cd": p.product_category_name,
        "nm_len": _int(p.product_name_lenght), "desc_len": _int(p.product_description_lenght),
        "photo_qty": _int(p.product_photos_qty), "wt_g": _int(p.product_weight_g),
        "len_cm": _int(p.product_length_cm), "ht_cm": _int(p.product_height_cm), "wd_cm": _int(p.product_width_cm),
    })
    g = raw["geolocation"]
    t["geo_zip"] = pd.DataFrame({"zip_pfx": g.geolocation_zip_code_prefix, "lat": _num(g.geolocation_lat),
                                 "lng": _num(g.geolocation_lng), "city_nm": g.geolocation_city,
                                 "uf": g.geolocation_state})

    o = raw["orders"]
    code_of = {nm: cd for cd, nm in ORDER_STATUS.items()}
    t["ord"] = pd.DataFrame({
        "ord_no": o.order_id, "cust_no": o.customer_id, "stat_cd": o.order_status.map(code_of).astype("Int64"),
        "ord_dtm": _utc(o.order_purchase_timestamp), "aprv_dtm": _utc(o.order_approved_at),
        "dlv_crr_dtm": _utc(o.order_delivered_carrier_date), "dlv_cust_dtm": _utc(o.order_delivered_customer_date),
        "est_dlv_dt": pd.to_datetime(o.order_estimated_delivery_date).dt.date,
        "is_test": False,
    })
    i = raw["order_items"]
    t["ord_item"] = pd.DataFrame({
        "ord_no": i.order_id, "item_seq": _int(i.order_item_id), "prd_id": i.product_id, "slr_id": i.seller_id,
        "ship_lmt_dtm": _utc(i.shipping_limit_date), "sale_amt": _num(i.price), "dlv_fee": _num(i.freight_value),
    })
    pay = raw["order_payments"]
    t["ord_pay"] = pd.DataFrame({
        "ord_no": pay.order_id, "pay_seq": _int(pay.payment_sequential), "pay_mthd": pay.payment_type,
        "inst_cnt": _int(pay.payment_installments), "pay_amt": _num(pay.payment_value),
    })
    r = raw["order_reviews"]
    t["rvw"] = pd.DataFrame({
        "rvw_sn": range(1, len(r) + 1), "rvw_id": r.review_id.values, "ord_no": r.order_id.values,
        "score": _int(r.review_score).values, "title": r.review_comment_title.values,
        "msg": r.review_comment_message.values, "crt_dt": pd.to_datetime(r.review_creation_date).dt.date.values,
        "ans_dtm": _utc(r.review_answer_timestamp).values,
    })
    return t
