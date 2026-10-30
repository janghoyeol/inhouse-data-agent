# 정답셋 쓰는 법

`questions.yaml`의 `gold_sql: null`을 정답 SQL로 채운다. 질문은 50개(단순·조인·지표·함정·권한 각 10), 그중 거절 문항 3개(E04·E06·E09)는 정답 SQL이 없다.

## 규칙

- **정답 SQL은 질문하는 직원(`db_user`)의 권한으로 실행된다.** 권역 매니저 문항의 정답은 자기 권역 숫자다. 일부러 WHERE로 권역을 거를 필요 없다 (DB가 거른다).
- 지표 문항은 `src/inhouse/agent/metrics.yaml`의 합의된 정의를 따른다. 예시 SQL을 복사해 기간만 바꿔도 된다.
- 함정 문항은 `checks`에 적힌 함정(T1~T14, `docs/schema-notes.md`)을 피한 SQL이어야 한다.
- 결과 모양은 자유다. 채점은 **값**으로 한다:
  - 값 하나(1행 1열)면 에이전트가 실행한 쿼리 결과 어딘가에 그 값이 있으면 맞음
  - 표면 행 수가 같고, 정답의 열마다 같은 값 묶음을 가진 열이 에이전트 결과에 있으면 맞음 (열 이름·순서·추가 열 무관)
  - 순위 질문은 `compare: {order_matters: true}`, 비율은 `compare: {rate: true}` (0.028과 2.8%를 같게 봄)
  - 숫자는 상대 오차 0.05% 또는 0.005 안이면 같다고 본다 (반올림 차이 허용)
- 여러 줄 SQL은 YAML 블록으로:

```yaml
  - id: C01
    ...
    gold_sql: |
      SELECT ...
```

## 확인

```bash
DATABASE_URL=postgresql://127.0.0.1:5433/tucanova TCN_USER_PASSWORD=... python -m inhouse.eval.check_gold C01,C02
```

## 채점 실행

```bash
DATABASE_URL=... TCN_USER_PASSWORD=... ANTHROPIC_API_KEY=... python -m inhouse.eval.run --model claude-haiku-5-5
```

결과는 `eval/results/`에 문항별 JSONL과 요약 Markdown으로 남는다. 정답 SQL이 없는 문항은 건너뛴다.
