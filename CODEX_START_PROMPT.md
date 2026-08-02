# Codex 시작 프롬프트 (호환용 바로가기)

> 현재 실제 구현 단계의 최상위 프롬프트는 `CODEX_MASTER_PROMPT.md`입니다. 이 파일의
> 과거 “대규모 코딩을 시작하지 말라”는 사전설계 지시는 폐기되었습니다. 새 Codex 작업에는
> `CODEX_MASTER_PROMPT.md` 전체를 사용하십시오.

아래 내용을 새 Codex 작업의 첫 요청으로 사용합니다.

---

당신은 제10회 2026 미래에셋증권 AI Festival `금융상품 Agent` 팀의 구현 담당자다.
이 저장소의 첨부 PDF와 데이터 ZIP이 최상위 기준이다.

## Goal

국내채권·국내 ETP(ETF/ETN)·해외 ETP(ETF/ETN)·공모펀드에 대한 자연어 질문을 HyperCLOVA X가
검증 가능한 typed QueryPlan으로 변환하고, 결정론적 데이터 엔진이 검색·필터·비교·
정렬·집계한 뒤, 원본 field까지 추적 가능한 근거와 안전한 한국어 답변을 공개 GET
API로 반환하는 E2E 시스템을 구현하라.

## Read first

작업 전에 다음 파일을 순서대로 전부 읽어라.

1. `AGENTS.md`
2. `00_START_HERE.md`
3. `MASTER_BRIEFING.md`
4. `VALIDATION_REPORT.md`
5. `docs/01_PDF_FULL_TRANSCRIPTION.md`
6. `docs/01A_TEAM_EMAIL_TRANSCRIPTION.md`
7. `docs/00_OFFICIAL_WEB_SNAPSHOT.md`
8. `docs/02_REQUIREMENTS_BASELINE.md`
9. `docs/03_DATA_AUDIT_AND_SEMANTIC_MODEL.md`
10. `docs/04_DATASET_REPORT.md`
11. `docs/04_PRODUCT_ARCHITECTURE_SPEC.md`
12. `docs/05_MVP_E2E_EXECUTION_PLAN.md`
13. `docs/06_TEST_REPORT.md`
14. `docs/06_API_CONTRACT_DRAFT.md`
15. `docs/07_TEST_AND_EVALUATION_PLAN.md`
16. `docs/08_BRIEFING_QUESTIONS_AND_DIFF_PROCESS.md`
17. `docs/09_BRAND_AND_ASSET_POLICY.md`
18. `docs/10_RELEASE_FREEZE_RUNBOOK.md`
19. `contracts/query-plan-v1.schema.json`
20. `contracts/provisional-api-request.schema.json`
21. `contracts/provisional-api-response.schema.json`
22. `contracts/evidence-bundle-v1.schema.json`
23. `contracts/release-manifest.schema.json`
24. `contracts/openapi-provisional.yaml`
25. `artifacts/source_manifest.json`
26. `registry/canonical_fields_v1.csv`
27. `registry/metric_policy_v1.csv`
28. `registry/synonyms_ko_v1.csv`
29. `registry/quarantine_rules_v1.csv`
30. `artifacts/requirements_traceability.csv`
31. `artifacts/release_manifest.template.json`
32. `tests/gold_queries_v0.jsonl`
33. `tests/policy_queries_v0.jsonl`

원본 사본은 `inputs/official_task.pdf`, `inputs/official_data.zip`에 있다. 수정하지 말고,
`scripts/verify_sources.py`를 가장 먼저 실행해 manifest hash를 확인하라.

## Source hierarchy

1. organizer PDF and data ZIP
2. later briefing transcript and organizer notices
3. official website and FAQ
4. team decisions

`OFFICIAL_PDF`, `OFFICIAL_DATA`, `OFFICIAL_TEAM_EMAIL`, `OFFICIAL_WEB`,
`PDF_EXAMPLE`, `BRIEFING_CONFIRMED`, `OPEN_QUESTION`, `TEAM_DECISION`을 섞지 마라.
외부·보강 데이터가 주최 측 데이터와 충돌하면 주최 측 데이터를 우선한다. 공식 요구사항
사이의 충돌은 양쪽 원문·날짜를 기록하고 최신·구체 공지를 운영 기준으로 쓰되, 서면 확인
전에는 conflict를 닫지 마라.

## Non-negotiable boundaries

- 평가 runtime의 언어모델은 HyperCLOVA X만 사용한다.
- 다른 LLM API·fallback·judge·router를 넣지 않는다.
- Codex는 개발 도구일 뿐 runtime dependency가 아니다.
- 데이터에 없는 상품·수치·단위·기준일을 생성하지 않는다.
- 근거 없는 수익률 전망과 단정적 투자추천을 생성하지 않는다.
- NULL을 0으로 처리하지 않는다.
- 기간·통화·단위·위험척도가 다른 metric을 억지로 비교하지 않는다.
- 내부 chain-of-thought를 외부에 노출하지 않는다.
- 원본 XLSX·ZIP·PDF를 변경하지 않는다.
- 마감 이후 commit·push·deploy·prompt/data/code/artifact 변경을 하지 않도록 immutable release를 만든다.
- 이미 구현된 `app/`, `etl/`, `registry/`, `contracts/`, `tests/`를 먼저 추적·재사용한다.
  같은 목적의 새 저장소·새 DB·새 planner·새 API를 병렬로 만들지 않는다.

## Product architecture

다음 흐름을 유지하라.

```text
GET compatibility adapter
-> input guard
-> HyperCLOVA X planner
-> QueryPlan JSON Schema validator
-> allow-listed deterministic compiler
-> DuckDB/Parquet executor
-> Evidence Bundle
-> deterministic evidence renderer
-> claim/safety/contract validator
-> strict JSON response
```

FastAPI + DuckDB + Parquet + 단일 오케스트레이터를 기본 선택으로 사용하되, 실제
저장소나 설명회 후 조건이 이를 반박하면 ADR을 기록하고 변경하라.

## Data rules

- 공식 원본 행은 145,393개다.
- 채권 `PD_NO`가 고유하다.
- 국내·해외 ETP는 `pd_itm_no`가 고유하다.
- 해외 ISIN은 공란·중복이 있어 PK로 사용하지 않는다.
- ETP 파일에 ETF와 ETN이 혼재하므로 내부 유형을 분리한다.
- 공모펀드는 95,619개 상품이 아니다.
- 공모펀드는 `itm_no` 11,139개와 `prfd_attr_cd` 다중 tag 구조다.
- 펀드 손상행 Excel row 84,563을 raw 보존 후 quarantine한다.
- fund count·평균·AUM은 deduplicated `fund_product` view에서 계산한다.
- 현재 실행 source of truth는 canonical field 207행과 v1 metric policy 59행이다.
- v1 full rebuild의 serving metric evidence는 1,156,332행이다.
- sample sheet의 `axis_*`를 전체 ground truth로 사용하지 않는다.

## Clarification behavior

- 결과를 바꾸는 scope·기간·상품식별·순위우선순위가 없으면 임의 추정하지 않는다.
- 일반 역질문은 현재 scope와 실제 지원 metric에 유효한 선택지 2~4개를 반환한다.
- 동일·유사 상품명은 서버가 실제 catalog에서 찾은 후보만 최대 12개까지 반환한다.
- 이미 확인한 조건은 preserved plan과 서명된 token으로 보존하고, 후속 답변과 합쳐 다시
  schema·allowlist를 검증한다.
- 데이터에 field가 없거나 단위·통화·기간이 비교 불가능하면 가짜 선택지를 만들지 말고
  `UNAVAILABLE` 또는 `INCOMPARABLE`과 가능한 대안을 설명한다.
- 수익률 기간 선택지는 registry의 해당 scope에 실제 존재하고 사용 가능한 기간만 제시한다.
  해외 ETP에는 가짜 장기수익률 기간을 묻지 말고 AUM·종가·거래량 대안을 표시한다.

## Bounded execution behavior

- 단순 교차 상품군 count는 scope별 `COUNT(DISTINCT product_uid)`로 분리한다. 호환되지 않는
  통화·기간·단위·위험척도 교차 순위는 계속 차단한다.
- 자산유형·지역·위험등급·연금 가능 filter는 scope별 실제 source label로만 실행하고,
  정확한 catalog 값이 없으면 fail-closed한다.
- `explain`은 정확한 단일 상품 target이 있을 때 source-backed 상품사실·원본 전략·benchmark만
  설명한다. target이 없으면 역질문하고 개방형 금융교육·투자 의견은 생성하지 않는다.
- 다중 metric 순위는 명시 우선순위를 요구한다. primary metric이 유효 모집단을 정하고,
  secondary metric 결측 상품은 제거하지 않고 `NULLS LAST`로 유지한다. 모든 요청 metric과
  모든 blocking limitation을 답변에 표시한다.
- `sum/avg/min/max`는 filter 후 같은 metric·통화의 최신 사용 가능 기준일로 계산하고
  source row count·as-of·unit evidence를 남긴다.

## Work sequence

1. 저장소와 현재 변경사항을 진단한다.
2. source hash를 검증한다.
3. living ExecPlan을 만들거나 갱신한다.
4. 기존 raw->clean->canonical->serving ETL과 v1 registry를 재사용한다. DB가 없거나
   source·ETL·registry가 변경된 경우에만 일상 rebuild하고, release gate에서는 clean rebuild한다.
5. 공식 행·열·key·quarantine reconciliation과 DB↔registry 일치를 검증한다.
6. 기존 lookup/search/rank/compare/aggregate/Evidence/answerability 경로를 회귀검증한다.
7. HCX planner와 provisional GET adapter의 현재 계약을 검증한다.
8. full pytest·gold·safety·fault·load·blind test를 실행한다.
9. clean Docker build부터 real HTTP GET·restart까지 E2E를 실행한다.
10. 공식 확정 뒤 public deployment, README, API spec, technical proposal을 완성한다.
11. release manifest와 freeze procedure를 검증한다.

설명회 녹취가 아직 없으면 `OPEN_QUESTION`을 임의 확정하지 마라. API adapter, HCX
model ID, unit·zero semantics처럼 영향이 큰 부분은 교체 가능한 경계로 유지하라. 현
`HCX-007` 고정은 `TEAM_DECISION`이며 주최 측 공식 지정으로 표현하지 마라.

## First task

이 파일은 과거 호환용입니다. `CODEX_MASTER_PROMPT.md`를 읽고 그 파일의 “지금 실행할
작업 순서”에 따라 기존 MVP를 검증·수정·완성하십시오. routine 구현과 테스트는 계속
진행하고, 공식 미확정 조건·외부 배포·secret 권한만 사용자에게 확인하십시오.

## Done when

- 네 상품군이 실제 ETL과 serving view에 존재한다.
- source hash와 공식 행·열이 일치한다.
- 펀드 반복가중과 손상행이 통제된다.
- real HCX가 valid QueryPlan을 만든다.
- lookup/search/rank/compare/aggregate/clarify/unsupported가 동작한다.
- 제한적 cross-scope 질의가 비교가능성 정책 안에서 동작하고, 지원되지 않는 혼합 순위는
  fail-closed한다.
- 모든 상품명·숫자 claim이 evidence로 추적된다.
- 역질문 선택지가 실제 해당 scope·metric에서 가능하고 follow-up E2E가 통과한다.
- exact-target explain, scope별 교차 count, bounded catalog filter, 다중 metric NULL 보존,
  `sum/avg/min/max`가 source evidence와 함께 통과한다.
- forbidden forecast·definitive recommendation test가 통과한다.
- public GET API가 공식 확정 contract를 만족한다.
- fresh Docker build->run->real HTTP E2E가 통과한다.
- 제출 runtime에 다른 LLM 호출·키·SDK가 없다.
- README, technical proposal, API spec, release manifest가 실제 release와 일치한다.
- Docker, live HCX, public TLS처럼 외부 환경이 필요한 gate는 실제로 검증한 경우에만 완료로
  표시하고, 그렇지 않으면 필요한 입력과 blocker를 정확히 남긴다.

---
