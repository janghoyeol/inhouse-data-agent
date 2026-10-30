# 0002. 권한 모델: 직원별 DB 로그인 + session_user 기준 RLS

- 상태: 채택 (2026-10-30). ADR 0001 결정 6("권한은 DB가 거절")의 구현 방식.
- 구현: `src/inhouse/data/security.sql`, 테스트 `tests/test_security_db.py`

## 맥락

에이전트는 모델이 쓴 SQL을 실행한다. 그러니 **모델이 쓸 수 있는 어떤 SELECT로도** 권한을 넘을 수 없어야 한다.
흔한 구현인 "앱이 요청마다 `SET app.user_id = …`로 세션 변수를 넣고 RLS 정책이 그 값을 읽는다"는 여기서 통하지 않는다.
`SELECT set_config('app.user_id', 'E001', false)`도 그냥 SELECT라서, 모델이 그 한 줄로 본사 분석가가 될 수 있다.

## 결정

1. **직원마다 DB 로그인 역할** (`u_e001`, `u_e104` …, `db_user` 표가 직원과 연결). 에이전트는 묻는 사람의 역할로 접속한다.
2. **권역 판단은 `session_user` 기준** (`tcn_visible_uf()`, SECURITY DEFINER). `session_user`는 접속할 때 정해지고
   `SET ROLE`·`set_config('role', …)`로 바뀌지 않는다. 직원 역할은 다른 직원 역할의 멤버가 아니라서 SET ROLE로 옮겨 갈 수도 없다.
3. **RLS는 고객의 주(UF)에서 시작해 이어진다**: `cust`(UF) → `ord`(고객) → `ord_item`·`ord_pay`·`rvw`·`rfnd`(주문), `mon_sls_smry_old`(UF).
   하위 표의 정책이 상위 표를 조회할 때 상위 표의 정책이 다시 걸리므로, 정책마다 권역 로직을 복붙하지 않는다.
   전체 데이터에서 집계 쿼리 0.05~0.34초 (본사 분석가·권역 매니저·CS, 2026-10-30 측정).
4. **쓰기 권한 없음** (SELECT만, 임시 표·함수 생성도 불가). `default_transaction_read_only=on`, `statement_timeout=5s`는
   역할 기본값으로 걸지만 사용자가 끌 수 있는 보조 장치라서, 테스트는 그걸 끈 상태에서 쓰기가 권한 오류로 막히는지 본다.
5. **고객 개인정보 원문은 어떤 직원 역할로도 안 나간다**. `cust`는 열 단위 권한으로 이름·전화·이메일을 뺐다 (`SELECT *`도 거절됨).
   CS만 마스킹 뷰 `v_cust_contact`("Ana S.", "+55 11 ****-1234", "a***@example.com")를 본다. 본사 분석가도 원문은 못 본다 (분석에 필요 없음).
6. 매핑 없는 로그인은 아무 행도 못 본다 (정책이 빈 집합). 직원·계정 표(`emp`, `db_user`)는 에이전트 역할에 안 보인다.

## 기각한 대안

- 세션 변수(`SET app.user_id`) + RLS: 위 맥락의 `set_config` 우회.
- 공용 에이전트 계정 + 앱이 `SET ROLE`: 공용 계정이 모든 직원 역할의 멤버여야 하므로 모델 SQL의 `SET ROLE`/`set_config('role')`로 옮겨 갈 수 있다.
- 앱이 SQL에 WHERE를 덧붙이기: SQL 재작성은 깨지기 쉽고 서브쿼리·CTE로 우회된다 (ADR 0001).

## 대가

- 직원 수만큼 DB 역할이 생긴다. 실제 고객사라면 SSO 그룹 → DB 역할 동기화가 필요 (지금은 `emp` 표에서 생성).
- 웹 앱(6단계)은 직원별 접속 정보를 들고 있어야 한다. 데모에서는 공통 비밀번호(`TCN_USER_PASSWORD`), 실서비스라면 IAM 인증 같은 단기 자격 증명.
- `SELECT *`가 고객 표에서 거절되므로, 탐색 도구(4단계)는 "이 사용자가 볼 수 있는 열"만 보여줘야 한다.

## 테스트로 확인한 것 (15개, 일부러 정책을 망가뜨리면 실패하는 것도 확인)

권역 매니저는 자기 권역 행만 (5개 하위 표·구 요약표 포함) · 집계에 다른 권역이 섞이지 않음 · 본사·CS는 전체 ·
개인정보 열·`SELECT *` 거절 · CS 마스킹 형식 · 다른 역할은 마스킹 뷰 거절 · 직원 표 거절 ·
읽기 전용을 꺼도 INSERT/UPDATE/DELETE/TRUNCATE/DROP/CREATE(표·임시 표·함수) 거절 ·
`SET ROLE postgres`·`set_config('role')`·다른 직원 역할로 전환 거절 · `SET ROLE r_agent` 해도 결과 동일 ·
`row_security=off`는 우회가 아니라 오류 · `pg_stats`로 다른 권역 값 안 보임 · 매핑 없는 계정은 0행.
