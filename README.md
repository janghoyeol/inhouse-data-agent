# inhouse-data-agent

**가상 고객사** Tucanova의 사내 DB에 자연어로 묻고, 사용자 권한 안에서, 숫자마다 그 숫자를 만든 SQL을 출처로 붙여 답하는 데이터 에이전트. (진행 중: 데이터 층·권한 완료)

- 권한은 프롬프트가 아니라 DB가 강제한다: 직원별 DB 역할, `session_user` 기준 행 단위 보안, 개인정보 열 권한·마스킹 뷰, 쓰기 권한 없음.
  모델이 쓸 법한 우회 SQL(`SET ROLE`, `set_config`, `row_security=off`, `pg_stats`)까지 테스트로 막는다 ([ADR 0002](docs/decisions/0002-permission-model.md)).
- "매출"처럼 고객마다 뜻이 다른 단어는 지표 사전으로 고정하고, 정답 SQL 50문항으로 정확도를 측정한다.
- 고객 요구사항: [docs/customer-brief.md](docs/customer-brief.md) · 스키마와 함정: [docs/schema-notes.md](docs/schema-notes.md) · 설계: [docs/decisions](docs/decisions/)

## 데이터

Tucanova는 가상 회사다. 주문 데이터는 **Olist가 공개한 브라질 이커머스 데이터**를 이 회사의 데이터로 가정해 쓰고,
Olist에 없는 것(고객 개인정보·테스트 주문·환불·조직·구 시스템 요약표)만 시드 고정 합성 데이터로 얹는다. Olist와는 관계가 없다.

- 원본: [Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) — **CC BY-NC-SA 4.0**
- 라이선스 조건(가공 데이터 재배포 시 같은 라이선스) 때문에 원본·가공 CSV는 레포에 넣지 않는다. 아래 스크립트가 내려받아 해시를 확인한다.
- 합성 개인정보의 이메일은 예약 도메인(`example.com/net/org`)만 쓴다.

## 실행

```bash
docker compose up -d db                       # PostgreSQL 16
docker compose run --rm loader                # Olist 내려받기(해시 확인) → 변환 + 합성 → 적재 (약 1분)
```

Docker 없이:

```bash
pip install -e .
python -m inhouse.data.fetch                                  # data/raw/
DATABASE_URL=postgresql://... TCN_USER_PASSWORD=... python -m inhouse.data.load   # 직원 역할 비밀번호
python -m unittest discover -s tests -t .                     # 오프라인 테스트
TEST_DATABASE_URL=postgresql://.../빈DB python -m unittest discover -s tests -t .   # + 적재·권한 통합 테스트 (그 DB의 표를 지움, 슈퍼유저)
```
