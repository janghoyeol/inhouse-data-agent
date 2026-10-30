"""Olist에 없는 것을 시드 고정 합성 데이터로 얹는다 (ADR 0001 결정 2).

- 고객 개인정보(이름·전화·이메일): Olist는 익명화돼 있다. 같은 사람(cust_uid)은 같은 값. 이메일은 예약 도메인(example.*)만.
- 탈퇴 고객: 개인정보를 지우고 deleted_at만 남긴다 (주문은 남음).
- 테스트 주문: 실제 주문과 똑같이 생겼고 is_test로만 구분된다. 일부는 9,999.99 같은 큰 금액 → 빼먹으면 매출이 튄다.
- 환불: 취소 주문은 전액, 배송 완료 주문 일부는 부분 환불.
- 사내 조직: 본사 분석가·권역 매니저 5명·CS → 2단계 RLS의 기준.
- 레거시 월별 요약(2017년까지): 현지 월 기준, 천 헤알 단위, 취소만 빼고 배송비 포함 — 지금 지표 정의와 다르다.
"""
from __future__ import annotations

import random
import unicodedata

import pandas as pd

from .transform import LOCAL_TZ, REGIONS

FIRST = ["Ana", "Bruno", "Carla", "Daniel", "Eduarda", "Felipe", "Gabriela", "Hugo", "Isabela", "João",
         "Larissa", "Lucas", "Mariana", "Mateus", "Natália", "Otávio", "Paula", "Rafael", "Sofia", "Thiago",
         "Vitória", "Yuri"]
LAST = ["Silva", "Santos", "Oliveira", "Souza", "Lima", "Pereira", "Costa", "Rodrigues", "Almeida",
        "Nascimento", "Carvalho", "Gomes", "Martins", "Rocha", "Ribeiro", "Barbosa"]
DDD = {"SP": 11, "RJ": 21, "MG": 31, "ES": 27, "PR": 41, "SC": 48, "RS": 51, "DF": 61, "GO": 62, "MT": 65,
       "MS": 67, "BA": 71, "SE": 79, "AL": 82, "PE": 81, "PB": 83, "RN": 84, "CE": 85, "PI": 86, "MA": 98,
       "PA": 91, "AP": 96, "AM": 92, "RR": 95, "AC": 68, "RO": 69, "TO": 63}
DOMAINS = ["example.com", "example.net", "example.org"]
STAT_DELIVERED, STAT_CANCELED, STAT_CREATED = 60, 90, 10
LEGACY_LAST_MONTH = "201712"  # 2018-01 시스템 이관, 그 뒤로는 요약표가 없다


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def _hex_id(rng: random.Random) -> str:
    return f"{rng.getrandbits(128):032x}"


def _person(rng: random.Random, uf: str, k: int) -> tuple[str, str, str]:
    first, last = rng.choice(FIRST), rng.choice(LAST)
    tel = f"+55 {DDD.get(uf, 11)} 9{rng.randrange(10**4):04d}-{rng.randrange(10**4):04d}"
    email = f"{_ascii(first)}.{_ascii(last)}{k}@{rng.choice(DOMAINS)}"
    return f"{first} {last}", tel, email


def _customers(t, rng, withdraw_rate):
    cust, ords = t["cust"].copy(), t["ord"]
    span = ords.merge(cust[["cust_no", "cust_uid"]], on="cust_no").groupby("cust_uid").ord_dtm.agg(["min", "max"])
    first_uf = cust.sort_values("cust_no").groupby("cust_uid").uf.first()
    uids = sorted(cust.cust_uid.unique())
    gone = set(rng.sample(uids, round(len(uids) * withdraw_rate)))
    rows = {}
    for k, uid in enumerate(uids, 1):
        nm, tel, email = _person(rng, first_uf[uid], k)
        first, last = (span.loc[uid, "min"], span.loc[uid, "max"]) if uid in span.index else (None, None)
        join = (first.tz_convert(LOCAL_TZ) - pd.Timedelta(days=rng.randrange(400))).date() if first is not None else None
        deleted = last + pd.Timedelta(days=rng.randrange(30, 300)) if uid in gone and last is not None else pd.NaT
        if uid in gone:
            nm = tel = email = None
        rows[uid] = (nm, tel, email, join, deleted)
    extra = pd.DataFrame.from_dict(rows, orient="index", columns=["cust_nm", "tel_no", "email", "join_dt", "deleted_at"])
    cust = cust.join(extra, on="cust_uid")
    cust["deleted_at"] = pd.to_datetime(cust.deleted_at, utc=True)
    cust["is_test_acct"] = False
    return cust


def _legacy_summary(t):
    o = t["ord"][t["ord"].stat_cd != STAT_CANCELED].merge(t["cust"][["cust_no", "uf"]], on="cust_no")
    o["yyyymm"] = o.ord_dtm.dt.tz_convert(LOCAL_TZ).dt.strftime("%Y%m")
    o = o[o.yyyymm <= LEGACY_LAST_MONTH]
    amt = t["ord_item"].assign(amt=lambda d: d.sale_amt + d.dlv_fee).groupby("ord_no").amt.sum()
    o = o.join(amt, on="ord_no")
    g = o.groupby(["yyyymm", "uf"]).agg(sls_amt=("amt", "sum"), ord_cnt=("ord_no", "nunique")).reset_index()
    g["sls_amt_k"] = (g.sls_amt / 1000).round(1)
    return g[["yyyymm", "uf", "sls_amt_k", "ord_cnt"]].sort_values(["yyyymm", "uf"]).reset_index(drop=True)


def _test_orders(t, rng, n):
    accts = max(1, n // 10)
    cust = pd.DataFrame([{
        "cust_no": _hex_id(rng), "cust_uid": _hex_id(rng), "zip_pfx": "01310", "city_nm": "sao paulo", "uf": "SP",
        "cust_nm": f"QA Tester {a:02d}", "tel_no": "+55 11 90000-0000", "email": f"qa{a:02d}@example.com",
        "join_dt": pd.Timestamp("2016-08-01").date(), "deleted_at": pd.NaT, "is_test_acct": True,
    } for a in range(1, accts + 1)])
    cust["deleted_at"] = pd.to_datetime(cust.deleted_at, utc=True)
    lo, hi = t["ord"].ord_dtm.min(), t["ord"].ord_dtm.max()
    prds, slrs = sorted(t["prd"].prd_id), sorted(t["slr"].slr_id)
    ords, items, pays = [], [], []
    for _ in range(n):
        ord_no = _hex_id(rng)
        at = lo + (hi - lo) * rng.random()
        stat = rng.choice([STAT_DELIVERED, STAT_DELIVERED, STAT_CANCELED, STAT_CREATED])
        ords.append({"ord_no": ord_no, "cust_no": rng.choice(list(cust.cust_no)), "stat_cd": stat, "ord_dtm": at,
                     "aprv_dtm": at + pd.Timedelta(minutes=5), "dlv_crr_dtm": pd.NaT, "dlv_cust_dtm": pd.NaT,
                     "est_dlv_dt": (at + pd.Timedelta(days=10)).date(), "is_test": True})
        total = 0.0
        for seq in range(1, rng.choice([1, 1, 2]) + 1):
            price = 9999.99 if rng.random() < 0.1 else round(rng.uniform(1, 300), 2)
            items.append({"ord_no": ord_no, "item_seq": seq, "prd_id": rng.choice(prds), "slr_id": rng.choice(slrs),
                          "ship_lmt_dtm": at + pd.Timedelta(days=3), "sale_amt": price, "dlv_fee": 0.0})
            total += price
        pays.append({"ord_no": ord_no, "pay_seq": 1, "pay_mthd": "credit_card", "inst_cnt": 1, "pay_amt": round(total, 2)})
    return cust, pd.DataFrame(ords), pd.DataFrame(items), pd.DataFrame(pays)


def _refunds(t, rng, rate):
    paid = t["ord_pay"].groupby("ord_no").pay_amt.sum()
    o = t["ord"][~t["ord"].is_test & t["ord"].ord_no.isin(paid.index)].sort_values("ord_no")
    rows = []
    for r in o[o.stat_cd == STAT_CANCELED].itertuples():
        rows.append((r.ord_no, round(float(paid[r.ord_no]), 2), r.ord_dtm + pd.Timedelta(days=rng.randrange(1, 5)), "CNCL"))
    delivered = list(o[o.stat_cd == STAT_DELIVERED].itertuples())
    for r in rng.sample(delivered, round(len(delivered) * rate)):
        base = r.dlv_cust_dtm if pd.notna(r.dlv_cust_dtm) else r.ord_dtm
        amt = round(float(paid[r.ord_no]) * rng.uniform(0.1, 0.6), 2)
        rows.append((r.ord_no, amt, base + pd.Timedelta(days=rng.randrange(1, 21)), rng.choice(["DMG", "WRONG", "LATE"])))
    df = pd.DataFrame(rows, columns=["ord_no", "rfnd_amt", "rfnd_dtm", "rsn_cd"]).sort_values(["rfnd_dtm", "ord_no"])
    df.insert(0, "rfnd_no", range(1, len(df) + 1))
    return df.reset_index(drop=True)


def _employees(rng):
    rows = [("E001", "HQ_ANALYST", None)]
    rows += [(f"E1{i:02d}", "RGN_MGR", cd) for i, cd in enumerate(REGIONS, 1)]
    rows += [("E201", "CS", None), ("E202", "CS", None)]
    out = []
    for no, role, rgn in rows:
        first, last = rng.choice(FIRST), rng.choice(LAST)
        out.append({"emp_no": no, "emp_nm": f"{first} {last}", "role_cd": role, "rgn_cd": rgn,
                    "email": f"{_ascii(first)}.{_ascii(last)}@example.org"})
    return pd.DataFrame(out)


def apply(tables: dict[str, pd.DataFrame], seed: int = 42, test_orders: int = 300,
          refund_rate: float = 0.03, withdraw_rate: float = 0.02) -> dict[str, pd.DataFrame]:
    rng = random.Random(seed)
    t = {k: v.copy() for k, v in tables.items()}
    t["mon_sls_smry_old"] = _legacy_summary(t)  # 실제 주문만으로, 테스트 주문을 얹기 전에
    t["cust"] = _customers(t, rng, withdraw_rate)
    t["rfnd"] = _refunds(t, rng, refund_rate)
    tc, to, ti, tp = _test_orders(t, rng, test_orders)
    t["cust"] = pd.concat([t["cust"], tc], ignore_index=True)
    t["ord"] = pd.concat([t["ord"], to.astype({"stat_cd": "Int64"})], ignore_index=True)
    t["ord_item"] = pd.concat([t["ord_item"], ti.astype({"item_seq": "Int64"})], ignore_index=True)
    t["ord_pay"] = pd.concat([t["ord_pay"], tp.astype({"pay_seq": "Int64", "inst_cnt": "Int64"})], ignore_index=True)
    t["emp"] = _employees(rng)
    return t
