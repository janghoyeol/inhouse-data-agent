"""테스트용 작은 Olist 원본: 실제 CSV와 같은 열 이름·형식으로 몇 행만."""
import pandas as pd


def mini_raw() -> dict[str, pd.DataFrame]:
    customers = pd.DataFrame({
        "customer_id": ["c1", "c2", "c3", "c4"],
        "customer_unique_id": ["u1", "u1", "u2", "u3"],  # u1은 두 번 주문한 같은 사람
        "customer_zip_code_prefix": ["01037", "01037", "80010", "69005"],
        "customer_city": ["sao paulo", "sao paulo", "curitiba", "manaus"],
        "customer_state": ["SP", "SP", "PR", "AM"],
    })
    orders = pd.DataFrame({
        "order_id": ["o1", "o2", "o3", "o4"],
        "customer_id": ["c1", "c2", "c3", "c4"],
        "order_status": ["delivered", "canceled", "delivered", "shipped"],
        # o1: 표준시(UTC-3), o3: 2017-12 브라질 서머타임(UTC-2)
        "order_purchase_timestamp": ["2017-10-02 10:56:33", "2017-11-20 08:00:00",
                                     "2017-12-01 10:00:00", "2018-08-01 23:30:00"],
        "order_approved_at": ["2017-10-02 11:07:15", None, "2017-12-01 10:10:00", "2018-08-02 00:10:00"],
        "order_delivered_carrier_date": ["2017-10-04 19:55:00", None, "2017-12-03 09:00:00", "2018-08-03 10:00:00"],
        "order_delivered_customer_date": ["2017-10-10 21:25:13", None, "2017-12-08 15:00:00", None],
        "order_estimated_delivery_date": ["2017-10-18 00:00:00", "2017-12-05 00:00:00",
                                          "2017-12-20 00:00:00", "2018-08-20 00:00:00"],
    })
    items = pd.DataFrame({
        "order_id": ["o1", "o1", "o2", "o3", "o4"],
        "order_item_id": [1, 2, 1, 1, 1],
        "product_id": ["p1", "p2", "p1", "p2", "p1"],
        "seller_id": ["s1", "s1", "s2", "s2", "s1"],
        "shipping_limit_date": ["2017-10-06 11:07:15"] * 5,
        "price": [58.90, 10.00, 100.00, 200.00, 30.00],
        "freight_value": [13.29, 5.00, 20.00, 15.00, 7.50],
    })
    payments = pd.DataFrame({
        "order_id": ["o1", "o1", "o2", "o3", "o4"],
        "payment_sequential": [1, 2, 1, 1, 1],
        "payment_type": ["credit_card", "voucher", "boleto", "credit_card", "debit_card"],
        "payment_installments": [8, 1, 1, 3, 1],
        "payment_value": [70.00, 17.19, 120.00, 215.00, 37.50],
    })
    reviews = pd.DataFrame({
        "review_id": ["r1", "r1", "r2"],  # 실제 데이터처럼 review_id 중복
        "order_id": ["o1", "o3", "o3"],
        "review_score": [4, 5, 1],
        "review_comment_title": [None, "otimo", None],
        "review_comment_message": [None, "chegou rapido", "nao gostei"],
        "review_creation_date": ["2017-10-11 00:00:00", "2017-12-09 00:00:00", "2017-12-10 00:00:00"],
        "review_answer_timestamp": ["2017-10-12 10:00:00", "2017-12-09 12:00:00", "2017-12-11 08:00:00"],
    })
    products = pd.DataFrame({
        "product_id": ["p1", "p2"],
        "product_category_name": ["perfumaria", None],
        "product_name_lenght": [40, None],
        "product_description_lenght": [287, None],
        "product_photos_qty": [1, None],
        "product_weight_g": [225, 500],
        "product_length_cm": [16, 20],
        "product_height_cm": [10, 5],
        "product_width_cm": [14, 15],
    })
    sellers = pd.DataFrame({
        "seller_id": ["s1", "s2"],
        "seller_zip_code_prefix": ["13023", "80010"],
        "seller_city": ["campinas", "curitiba"],
        "seller_state": ["SP", "PR"],
    })
    geolocation = pd.DataFrame({
        "geolocation_zip_code_prefix": ["01037", "01037", "80010"],
        "geolocation_lat": [-23.5456, -23.5460, -25.43],
        "geolocation_lng": [-46.6393, -46.6390, -49.27],
        "geolocation_city": ["sao paulo", "são paulo", "curitiba"],
        "geolocation_state": ["SP", "SP", "PR"],
    })
    translation = pd.DataFrame({
        "product_category_name": ["perfumaria", "beleza_saude"],
        "product_category_name_english": ["perfumery", "health_beauty"],
    })
    return {
        "customers": customers, "orders": orders, "order_items": items, "order_payments": payments,
        "order_reviews": reviews, "products": products, "sellers": sellers,
        "geolocation": geolocation, "category_translation": translation,
    }
