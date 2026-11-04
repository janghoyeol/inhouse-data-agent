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
  - B안 합의(2026-10-30): A·B·C 30개는 Claude 초안(`gold_by: claude-draft`, 호열 검토), D·E 17개는 호열 작성 (채점 독립성은 함정·권한에서 지킴)
  - [x] 초안 30개 작성·실행 확인. A01·A04 질문을 답이 하나가 되게 고침. 임시 측정 Haiku 32/33, 문항당 $0.0015 (`eval/RESULTS.md`)
  - [x] 채점 버그 2개 수정(순위는 앞 N행, 월 vs 1일). B05는 실행마다 흔들림 → 정식 측정은 문항당 3회
  - [x] D01을 같이 단계별로 작성 (`gold_by: together`): 단순 SQL에서 시작해 함정(T2 날짜·T1 테스트·주문 수 정의)을 하나씩 피함 1,068 → 1,176 → 1,166
  - [ ] **다음 세션 첫 순서: 호열 PC에 로컬 DB 띄우기** — OS(Windows/Mac) 묻기 → Docker Desktop 설치 → `docker compose up -d db` + `docker compose run --rm loader`
        (2026-11-02 Compose 첫 실제 검증: db 컨테이너 healthy, loader 이미지 빌드·Kaggle 내려받기·적재 37.5초, u_e001로 localhost:5432 접속해 D01 1,068→1,176→1,176→1,166, u_e104 808, tel_no 권한 거절. 샌드박스라 loader는 프록시 때문에 host 네트워크로 실행 — Compose 내부 네트워크(db 호스트명)만 미검증) → DBeaver로 접속(postgres / 직원 역할 u_e001 등, 비밀번호 tucanova-local) → D01 1~4단계를 직접 돌려 보기
        호열은 SQL·DB 접속이 처음에 가까움: 용어(DB·SQL·정답 SQL)부터, 파일은 GitHub 링크로 안내
  - 호열 선호 (2026-11-02): 오타는 지적하지 않는다(오타 때문에 오류 나서 물어볼 때만). 문항을 넘어갈 때마다 다음 질문 문장을 다시 준다
    진행: D01 together · D02 hoyeol 94,990 · D03 hoyeol 406,900 · D04 hoyeol 610(질문을 '코드가 아예 없는'으로 고침) · D05 hoyeol 6,863 · D06 together 1,922(count 괄호 안이 세는 대상) · D07 hoyeol 4(표 3개 JOIN) · D08 hoyeol 73 · D09 hoyeol 1,922.41(팬아웃·서브쿼리) · D10 hoyeol 157(LEFT JOIN 짝 없음) · E01 hoyeol 3,883,073.11(u_e104로 접속, 권역 조건 없이) · E02 hoyeol 7,393(u_e105) · E03 hoyeol 1,779(u_e101) · E05 hoyeol 마스킹 연락처(u_e201, v_cust_contact) · E07 together 남동부 3,883,073.11(group by 처음, FROM 안 임시 표 2개 LEFT JOIN) · E08 hoyeol NULL(탈퇴 고객, u_e201) · E10 together 2.53(u_e102, 사람별 group by → filter 비율, 정수 나눗셈) — **D·E 17문항 전부 완료** (2026-11-03 하루 6문항). 호열: '산수(비율·나눗셈) 부분은 더 공부해야겠다' → 다음 연습 문제에 비율·집계 문제를 넣을 것. 호열은 DBeaver에 권한별 연결 6개(전체·북부·북동부·남동부·남부·CS) 만들어 둠. WITH는 아직 어려워함 → 강요하지 말 것 — DB 지도 https://claude.ai/artifact/3wmFVkooahx9KuaS2eTyS9
  - 호열 페이스 (2026-11-02 합의): 하루 10~11문항. E 6개 끝나면 → A·B·C 초안 30개 검토 → **연습용 문제를 더 만든다** (호열 요청)
    매 세션 시작: 로컬 DB(Docker Desktop 켜기 → `docker compose up -d db`, 데이터는 볼륨에 남아 있음) → DBeaver → 남은 문항부터
  - [ ] **그다음: 호열과 정답 SQL을 처음부터 천천히.** 호열 요청: "세심하게, 학습이 필요한 부분" → 정답을 먼저 주지 말고
        D01처럼 단순 SQL → 결과 확인 → 함정 하나씩 짚는 방식. 필요하면 표·열 설명부터 (호열이 GitHub 파일 열람이 불편할 수 있음 → 대화에 요약)
  - [x] D·E 17개 작성 완료 (2026-11-03) → 정답셋 50문항 중 결과형 47개 정답 SQL 다 채움
  - [~] 연습 문제 `eval/practice.yaml` (정답셋과 별개, 정답 포함): 1차 P01~P05 비율·평균·나눗셈 (2026-11-03 출제)
    진행: P01 97.26 ✅(취소 포함 분모 판단 정확) · P02 1.14 ✅(주문별 group by → avg, 첫 시도는 avg(item_seq)·기간 복붙 실수) — 다음은 P03(avg·order by)부터
  - [x] A·B·C 초안 30개 Claude 교차 검산 (2026-11-04, 호열 요청 '초안 검토는 일단 너가'): `scripts/crosscheck_drafts.py` 다른 모양 SQL로 30/30 일치(UTC·KST 접속 둘 다), 순위 경계 확인, B05 3위가 팬아웃 시 바뀜 → 주석. `reviewed: crosscheck`, `eval/REVIEW.md`
  - [ ] (선택) 호열: A·B·C 초안 사람 검토 → 끝나면 gold_by 표시 변경
  - [x] 정식 측정 (2026-11-04, 50문항×3회, effort medium): Haiku 5.5 147/150(98%) $0.0016/문항 · Sonnet 5.5 147/150(98%) $0.0218/문항 → 기본 Haiku (`eval/RESULTS.md`)
    - 실패 8건 모두 읽음: 채점 규칙 2(D09 부호 → abs, E07 꼬리표 열 → 한 줄 정답은 숫자만, `inhouse.eval.regrade`로 재채점) · 질문 애매 3(Sonnet B05 '미분류' → 측정 후 질문 수정) · 출처 원칙 위반 3(Haiku B05·D03, 틀림 유지)
    - run.py `--repeat --workers`, 결과 행도 기록(재채점용)
- [x] 6. 웹 화면 (`inhouse.web.app`, FastAPI + 정적 페이지): 같은 질문을 두 직원에게 나란히 → 권한별 답 비교, 답 속 쿼리 ID → 실행 SQL·결과 펼침, 직원은 PERSONAS에서만(임의 DB 계정 거절), 하루 비용 상한(DAILY_COST_CAP_USD), 감사 로그 API
  - 테스트 6개(가짜 에이전트), 총 87개. Compose `web` 서비스. 캡처 `docs/demo/*.webp` (본사 vs 남동부 매출, 본사 vs CS 연락처)
  - [x] 공개 배포 https://inhouse-data-agent.onrender.com (2026-11-04 호열 결정 '공개 배포로'): DB = Neon 무료(Render 무료 DB는 30일 뒤 삭제), 웹 = Render 무료 Blueprint(`render.yaml`, 싱가포르)
    - [x] security.sql을 비슈퍼유저 관리자(Neon)용으로: 기존 역할 속성 ALTER 대신 검사. neon_owner(CREATEROLE·BYPASSRLS) 흉내 DB로 테스트 통과
    - [x] IP별 질문 상한(분당 6·하루 40), /api/audit 기본 끔(ENABLE_AUDIT_API), /healthz. 테스트 90개
    - [x] 호열: Neon 프로젝트 tucanova(싱가포르) 적재 → Render Blueprint. 첫 배포는 ANTHROPIC_API_KEY 줄을 저장 안 해 500 → 키 없으면 시작 실패·오류는 이유 담은 502로 고침(테스트 92개)
    - [x] 라이브 확인: u_e001 6,031,684.57 · u_e104 3,883,073.11(로컬과 같음, 질문당 $0.002·7~8초) · u_e201 전화번호 거절 · /api/audit 404
    - [x] showcase 카드: 깨우기 배지·사이트 방문 버튼, README 주소
    - 호열이 대화에 API 키를 붙여 넣은 적 있음 → 폐기·재발급 안내함. 키는 대화에 받지 않는다
- [x] README를 사례 연구로 (고객 문제 → 데이터·함정 → 설계 결정 → 측정 → 배운 것), showcase 카드
- [~] 2단계 ADR 0004 채택 (2026-11-04, 결정 6개 추천안대로): CS 환불 처리 한 흐름 = 문서 검색(2a) → 환불 제안·사람 승인(2b)
  - 가상 문서 약 30개(함정: 개정 전후·충돌·권한 밖·동의어) · Postgres 키워드 검색(2글자, 측정 후 임베딩) · 문서 조각도 RLS · 인용 문장·시행일 코드 검증 · 에이전트는 propose_refund만, 승인은 사람
  - [x] 2a-1 (2026-11-04): customer-brief 2단계 절(CS 팀장 Renata Oliveira) · 문서 30개 `src/inhouse/data/docs/*.md`(머리말 doc_id·version·status·effective·audience) · 함정 DT1~DT12 `docs/doc-notes.md`
    - 규정 숫자 초안(호열 검토 대기): 환불 기한 v1 7일(2016-09~2017 주문)·v2 15일(2018~ 주문)·v3 초안 30일 · LATE 1~6일 배송비, 7일+ 배송비+20% · 승인 한도 v1 300/1,500 · v2(2018-07~ 승인일) CS팀장 500·권역 2,000·본사 무제한 · 72시간 만료
    - 열람: tag all·cs·mgr·hq·rgn:XX·hr. u_e001 27 · 권역 16~18 · CS 22 (판 기준)
  - [ ] 2a-2 문서 표 + 조각 + RLS + 적재 + 권한 테스트
