# inhouse-data-agent — 프로젝트 브리핑

- 개발자: 장호열 (`janghoyeol`). FDE 포트폴리오 프로젝트 A (B: `janghoyeol/korea-data-mcp` 완료, C: 실제 고객 1곳 도입은 이후).
- 목표: 공개 데이터·조회 전용·사용자 한 명이던 기존 프로젝트의 빈칸(사내 데이터·권한·지저분한 스키마·지표 정의)을 채운다.
- 협업 방식·원칙은 trend-agent CLAUDE.md와 같음: 설계 선택지 먼저 합의, ADR 기록, 테스트 먼저, "됐다" 전에 실제 키로 라이브 확인, 키는 환경변수로만.
- 옮겨 오는 원칙: 숫자마다 출처(= 실행한 SQL·쿼리 ID·실행 시각), 집계는 SQL이 하고 모델은 암산하지 않음, 고정 정답셋으로 측정.
- 가상 고객사다. 실존 회사 이름·브랜드를 쓰지 않는다.

## 진행 상황
- [x] ADR 0001 (1단계: SQL) 채택 2026-10-30: 이커머스 · Olist(CC BY-NC-SA 4.0, 원본 CSV는 레포에 안 넣음) + 합성 덧층 · PostgreSQL · 탐색 도구 + 지표 사전 · 웹 먼저
  - 가상 고객사 이름: **Tucanova** (투카노바)
- [x] 1. 고객 요구사항(`docs/customer-brief.md`) · Olist 내려받기(해시)·변환·합성 덧층·Postgres 적재(41초) · 함정 12개 실측(`docs/schema-notes.md`) · Compose · CI
  - 테스트 20개 (오프라인 15 + 적재 통합 5, `TEST_DATABASE_URL`). 이 세션 컨테이너에선 이미지 pull이 막혀 Compose 실제 실행은 못 했고 로컬 PG16으로 검증
- [x] 2. 권한 (ADR 0002): 직원별 DB 로그인 역할, `session_user` 기준 RLS(고객 UF → 주문 → 하위 표), 개인정보 열 권한 + CS 마스킹 뷰, 쓰기 권한 없음
  - 테스트 15개 (우회 시도 포함), 정책을 일부러 망가뜨린 4가지 변형 모두 테스트가 잡음. 전체 데이터 집계 0.05~0.34초
  - 로컬 PG는 TCP에 scram 인증 (CI와 같게). 직원 역할 비밀번호 `TCN_USER_PASSWORD`
- [x] 3. `run_sql` 안전장치 (ADR 0003): sqlglot 검사(안내 층) → 직원 역할·읽기 전용 트랜잭션·SET LOCAL 타임아웃·서버 커서 행 상한 → JSONL 감사 로그·쿼리 ID
  - 테스트 12개 (검사 3 + 실행 9), 총 47개. runner 변형 4개(읽기 전용·타임아웃·행 상한·검사 건너뛰기) 모두 테스트가 잡음
  - DB 역할은 클러스터 공용 → 같은 클러스터에서 통합 테스트하면 직원 비밀번호가 바뀜. 콜드 캐시 첫 쿼리 1.6초(따뜻하면 0.1초)
- [x] 4. 지표 사전(`src/inhouse/agent/metrics.yaml`: 공통 규칙 12 · 지표 11 · 열 메모) + 도구 4개(`tools.py`: list_tables·describe_table·lookup_metric·run_sql, 전부 직원 역할로)
  - 지표 SQL을 pandas 독립 계산과 맞춰 검산, 지표 변형 2개(환불 이중 차감·cust_no 재구매)를 테스트가 잡음. 테스트 총 67개. 전체 데이터 지표 0.1~0.9초(따뜻할 때)
  - 지표 정의는 사용자(가상 데이터팀장 역할)와 합의 → 아래 '지표 11개 전부 합의'
  - 로컬 테스트는 별도 클러스터(5434, `TEST_DATABASE_URL=postgresql://postgres:pgtest@127.0.0.1:5434/t`)로. 5433은 본 데이터(직원 비밀번호 tucanova-local)
- [x] 지표 11개 전부 합의(2026-10-30, 초안대로): status agreed, decisions에 질문 → 결정 기록
- [~] 5. 정답셋·평가
  - [x] 에이전트 루프(`agent.py`, Haiku 5.5 기본, 직접 루프로 도구 호출 기록, 시스템 프롬프트 캐시), 질문 50개(`eval/questions.yaml`, 5분류×10, 거절 3), 채점(`eval/match.py`: 값 기준·인용 여부 구분), `inhouse.eval.run`, `inhouse.eval.check_gold`
  - [x] 라이브 4문항(Haiku 5.5, 합계 $0.0042): 매출(본사·남동부) 지표 SQL과 같은 값·인용, 블프는 합의된 '주문 수' 정의로 1,166(취소 포함 1,176은 정의 밖), 전화번호 요청 거절
  - [ ] 정답 SQL 47개 — 호열 작성 (`eval/README.md`), Claude가 check_gold로 검산
  - [ ] Haiku/Sonnet 첫 측정 (Haiku 50문항 약 $0.05, Sonnet 약 $1 예상)
- [ ] 6. 웹 화면(사용자 전환) → CI → showcase 카드
- [ ] 2단계(별도 ADR): 사내 문서 RAG + 쓰기 작업 승인 흐름
