# Codex 구현·검증 마스터 프롬프트

상태: **DRAFT — local 검증 완료, 외부 release gate 미완료.** 이 문서는 FINAL 제출 승인이나
공식 계약 확정을 뜻하지 않는다.

저장소 루트에서 Codex를 연 뒤 이 파일 전체를 첫 요청에 첨부하거나 참조시킨다. Codex가
자동으로 읽는 루트 `AGENTS.md`도 적용되었는지 첫 작업에서 확인한다. 공식 Codex 권장
형식인 `Goal / Context / Constraints / Done when`을 따르며, 장기 작업은 검증 가능한 작은
단계로 끝까지 진행한다. 실제 사용 순서는 `CODEX_USAGE_GUIDE.md`에 있다.

---

당신은 제10회 2026 미래에셋증권 AI Festival `금융상품 Agent` 팀의 책임 개발자다.
지금부터 문서만 제안하지 말고, 기존 실행 MVP를 직접 점검·수정·검증해 제출 가능한
상태로 완성하라. routine 구현·테스트는 별도 승인 없이 계속하되, 공식 미확정 조건을
임의로 확정하거나 외부 배포·유료 자원 생성·secret 변경은 하지 마라.

## Goal

국내채권·국내 ETF/ETN·해외 ETF/ETN·공모펀드 질문을 HyperCLOVA X가 검증 가능한
typed QueryPlan으로 변환하고, 결정론적 데이터 엔진이 검색·필터·비교·정렬·집계한 뒤,
원본 Excel 행·필드까지 추적되는 근거와 안전한 한국어 답변을 공개 GET API로 반환하는
E2E 시스템을 제출한다.

최적화 우선순위는 다음과 같다.

1. 답변 상품·숫자·순서의 정확성
2. 모든 주요 claim의 field-level evidence
3. 정보 부족 시 구체적 역질문, 데이터 부재 시 명확한 확인 불가
4. 금지된 전망·보장·단정 추천·임의 추정의 완전 차단
5. 네 상품군 전수와 펀드 반복구조의 정확한 모집단
6. HCX-only 준수와 운영 안정성
7. 깨끗한 Docker build부터 실제 HTTP까지 재현성
8. 설명회 이후 계약 변경을 adapter/registry만으로 흡수하는 유연성

## Context: 최상위 사실

### 출처 우선순위

1. `inputs/official_task.pdf`와 `inputs/official_data.zip`
2. 추후 들어올 설명회 녹취·주최 측 서면 공지·팀별 공지
3. 공식 홈페이지·FAQ
4. 팀 설계

모든 판단은 `OFFICIAL_PDF`, `OFFICIAL_DATA`, `OFFICIAL_TEAM_EMAIL`, `OFFICIAL_WEB`,
`PDF_EXAMPLE`, `BRIEFING_CONFIRMED`, `OPEN_QUESTION`, `TEAM_DECISION` 중 하나로 구분한다.
예시·관찰·팀 결정을 공식 요구사항이라고 표현하지 마라.

### 대회 목적과 우리 주제

- 대회: 제10회 2026 미래에셋증권 AI Festival
- 주제: 금융상품 Agent, 공식 영문 표현 `Financial Product Analyst`
- 과제: 네 개의 상이한 금융상품 master를 구조화하고 자연어 질문에 검색·조회·필터·계산·
  비교·정렬·순위·집계·상품군 교차 질의로 답한다.
- 답변은 데이터 근거를 표시해야 한다.
- 확인할 수 없으면 그 사실을 명시하거나, 결과를 바꾸는 누락 조건을 역질문해야 한다.
- 범용 투자자문·개인화 포트폴리오·미래 수익 예측이 과제의 중심이 아니다.

### 평가·제출 핵심

- 비공개 상·중·하 질문을 평가자가 GET API로 호출한다.
- 평가축: 문제정의, 기술완성도·성능, 창의성·확장성, 답변 정확성·완결성, 현업 활용성·리스크.
- 제출물: 소스+재현환경+README, 기술제안서, API URL+요청/응답 JSON 명세.
- PDF의 `/answer`, `question_id`, `question`, 다섯 response field는 `PDF_EXAMPLE`이며
  설명회 전에는 고정 계약으로 단정하지 않는다.
- 현재 adapter의 `clarification_token`과 `clarification_response`는 `TEAM_DECISION` 선택
  extension이며 반드시 함께 보내거나 함께 생략한다. 평가기가 이를 허용하는지는
  `OPEN_QUESTION`이다.
- 제출 마감은 2026-09-06, 홈페이지의 23:59 표기를 운영 기준으로 쓰되 내부 freeze는
  2026-09-05로 둔다.
- 마감 후 결과를 바꾸는 commit·push·deploy·prompt/data/code 변경은 실격 조건이다.
- PDF의 API 기간 09.07~09.20과 평가기간 09.07~09.30의 차이는 `OPEN_QUESTION`; 해결 전
  09.30까지 동일 release를 유지하는 쪽으로 운영한다.

### 설명회

- 2026-08-06 목요일 13:00~14:30 금융상품 Agent 세션
- 네이버 그린팩토리 2층 CONNECT HALL, 경기도 성남시 분당구 불정로 6
- 네이버 1784가 아님
- 팀당 1인, 주차 불가
- 설명회 녹취가 들어오면 `docs/08_BRIEFING_QUESTIONS_AND_DIFF_PROCESS.md` 절차로 원문
  보존→교정전사→요구문장 추출→source diff→contract/registry/test 갱신 순서로 반영한다.

## Context: 공식 데이터 전체 현황

### 불변 원본

- PDF SHA-256: `3717441e091958b7214db710e0e4b9b8ae15ac6c205cad6e51721214798eb3de`
- ZIP SHA-256: `c3809aca73396f57242ded0188fa06a3d271bd4ad65010e53d5533efc7c18163`
- ZIP 내부 XLSX 8개: datarows 4개 + schema/sample 4개
- datarows 총 145,393행, 원본 field 207개, exact duplicate row 0
- 원본 파일명은 Unicode NFD일 수 있으므로 manifest로 해석하고 첫 glob 결과에 의존하지 않는다.
- source 파일은 절대 수정하지 않는다.

### 논리 상품과 serving

| scope | raw rows | 논리 상품/listing | quarantine | serving |
|---|---:|---:|---:|---:|
| 국내채권 PRBD01N001 | 42,394 | 42,394 | 0 | 42,394 |
| 국내 ETP PREF01N001 | 1,734 | 1,734 | 1 | 1,733 |
| 해외 ETP PREF02N001 | 5,646 | 5,646 | 8 | 5,638 |
| 펀드 PRFD01N001 | 95,619 attribute rows | 11,139 `itm_no` | 1 | 11,138 products + 95,618 attributes |
| 합계 | 145,393 | 60,913 | 10 | 60,903 |

### PK·alias

- 채권 PK: `PD_NO`, 42,394/42,394 고유
- 국내 ETP PK: `pd_itm_no`; `pd_itm_no_ma`는 별도 alias
- 해외 ETP PK: `pd_itm_no`; `pd_itm_no_ma`, ISIN, Lipper ID는 alias
- 해외 ISIN은 공란 9, distinct 5,587, duplicate excess 50이므로 PK 금지
- 펀드 product PK: `itm_no`; attribute bridge PK: `itm_no + prfd_attr_cd`
- 펀드 raw 95,619행을 직접 COUNT/AVG/SUM하면 attribute가 많은 상품이 과대가중되므로
  상품 계산은 deduplicated `fund_product`, 속성 조건은 `EXISTS fund_attribute`를 사용한다.

분모 용어를 고정한다. `raw row`는 공식 파일의 물리 행, `logical product/listing`은 상품키로
중복 제거한 격리 전 상품, `serving product`는 격리 제외 논리 상품이다.
`metric_policy_v1.csv`의 `raw_denominator`는 격리 전 논리 상품/listing 분모이므로 펀드에서
11,139이며, 물리 attribute 95,619행은 `fund_attribute` 분모로 따로 다룬다.

### 격리·부분보존

- 국내 ETP Excel row 1,155: `pd_itm_no=KR`, `pd_nm=.` → raw 보존, quarantine
- 해외 ETP: XW(row1956), BTCK.K(2110), BAY(2219), BZZ(2350), AV(2439),
  ONX(2639), PINC.K(3684), OWN(5119) → raw 보존, quarantine
- 펀드 Excel row 84,563: `itm_no='"'`, 열 밀림 손상 → 복구 추정 금지, quarantine
- TBF(row4323), EMOP.K(row5312)는 PARTIAL flag로 serving 유지
- KRG597100145(row1381)는 `pd_lstg_dt=10001231` suspect date지만 identity/status가 있어
  PARTIAL로 serving 유지

### 중요한 metric 품질

- 채권 BUY_YIELD·BUYABLE_QUANTITY: 881/42,394; quantity 양수 325, zero 556
- 채권 AVG_ANNUAL_TAX_YIELD: 값 있는 881건 전부 0 → 순위 금지
- 채권 CRD_GRD: 24,750/42,394; rating order 확정 전 순위 금지
- 국내 ETF 1Y return: source-present 986, quality-valid 951, 공통 최신 원천일
  `2026-06-15` 기준 rankable 940; 이상치 원문 유지
- 국내 총보수: ETF 유효 217, zero 150; zero/단위 의미 확정 전 낮은 순위 금지
- 국내 ETN AUM: 값 있는 409건 전부 zero → 크기 순위 금지
- 국내 실시간 관련 원천 field는 공란이며 snapshot을 실시간으로 표현 금지
- 해외 1D return: serving ETF 5,329건 전부 zero → 순위 금지
- 해외 1M/3M/6M/1Y/YTD return과 위험등급 field는 없음
- 해외 AUM: 통화/단위 context 필수; 국내·펀드 AUM과 혼합 금지
- 해외 discrepancy: 값 3건뿐 → 순위 금지
- 펀드 full serving은 11,138개지만 기본 API 상품 universe는 공모 11,115개다. full 수치와
  API 기본 수치를 답변·분모에서 섞지 않는다.
- API 기본 공모 universe의 위험등급 보유 8,564, 결측 2,551; 판매중 8,445,
  판매완료 2,670
- 펀드 1Y return은 전체 정상 7,017/11,138, 판매중 6,936/8,445
- 펀드 보수·설정일·NAV·보유종목·환매조건·개별 as-of field는 없음
- 펀드 return에는 큰 음수/양수 이상치가 있으므로 삭제·clipping하지 않고 경고한다.

`registry/canonical_fields_v1.csv`는 공식 207/207 field를 전수 매핑한다.
`registry/metric_policy_v1.csv`는 59개 metric(채권 19, 국내 ETP 16, 해외 ETP 12, 펀드 12)의
raw/present/valid/serving/type-specific/
rankable 분모를 분리한다. `registry/synonyms_ko_v1.csv`는 146개 한국어 표현,
`registry/quarantine_rules_v1.csv`는 13개 격리·부분보존·drift 규칙을 담는다.

## Context: 현재 구현 상태

이미 구현되어 있으므로 같은 기능을 새로 만들지 말고 검사 후 필요한 부분만 고친다.

기존 계층을 우회하는 새 저장소·새 서비스·새 DB·새 QueryPlan·새 planner·새 API를 병렬로
만들지 않는다. 요구사항이 기존 경계로 해결되지 않는다는 증거가 있을 때만 ADR과 회귀
테스트를 먼저 추가한 뒤 최소 범위로 확장한다.

- `etl/source.py`: outer PDF/ZIP 및 inner 8 XLSX hash·dimension fail-closed 검증
- `etl/build.py`: raw/clean/canonical/serving DuckDB, Parquet export, source locator, row hash,
  fund attribute bridge, quarantine, reconciliation; metric long table은 v1 policy를 직접 읽음
- `data/serving/mirae_agent.duckdb`: 전체 source로 실제 생성된 serving DB
- `app/domain/models.py`: QueryPlan 1.1, explicit clarification, EvidenceBundle 1.1
- `app/planner/hcx.py`: Native Structured Outputs를 쓰는 HCX adapter, Bearer auth, finish/status 검증,
  429/5xx bounded retry, local Pydantic validation
- `app/config.py`: 현재 `HCX-007`만 허용하는 TEAM_DECISION baseline; 설명회에서 다른 ID가
  지정되면 config 기본값·validation·manifest·문서·테스트를 함께 변경
- `app/planner/deterministic.py`: dev/test용 작은 비-LLM parser; 제출 LLM fallback이 아님;
  scope별 교차 count, registry-backed return period, bounded catalog filter, exact-target explain
- `app/execution/registry.py`: v1 canonical/metric registry 기반 allowlist·비교가능성·사용 가능
  수익률 기간·fail-closed policy
- `app/execution/engine.py`: parameterized DuckDB 실행, Decimal, stable tie, coverage, evidence,
  scope별 distinct count, `sum/avg/min/max`, primary INNER·secondary LEFT rank
- `app/clarification.py`: missing slots/options/preserved plan/HMAC token/후속 질의 결합
- `app/safety.py`: 전망·단정 추천·실시간·결측 0 치환·injection 차단
- `app/rendering.py`: 요청한 모든 metric·누락값과 모든 blocking limitation을 포함하는
  evidence-only 한국어 renderer; 정확한 상품의 원본 전략·benchmark 설명; 공개 answer
  30,000자 계약을 넘지 않는 결정론적 줄 단위 축약
- `app/service.py`: 전체 orchestration; 광범위 lookup의 실행 전 cardinality 역질문,
  clarification 결과 비우기, 500,000자 context 상한, 10,000자 token 상한을 fail-closed로 보장
- `app/main.py`: GET `/answer`, health, no-store, provisional five-field response
- `tests/`: unit, HCX mock, ETL, API contract, 40 gold + 10 policy assertion runner
- `Dockerfile`: multi-stage builder에서 source 검증·full data build·compliance scan; runtime은
  최소 `requirements-runtime.txt`, app·registry·검증된 DB만 포함하고 원본 source·ETL 제외
- `scripts/`: verify/build/gold/smoke/compliance/release manifest

현재 실증값:

```text
raw rows                 145,393
logical products          60,913
serving products          60,903
quarantine                    10
fund attributes           95,618
metric policy rows             59
serving metric evidence 1,156,332
gold/policy fixtures           50
pytest collected               158
pytest passed                  158
fixture failures                 0
real HTTP E2E                  15/15
HTTP load                    100/100 (concurrency 10, failure 0, p95 131.75ms)
runtime scan              28 files/0 findings
```

`serving metric evidence`는 59개 정책 metric과 상품별 `product.id`, `product.name` identity
근거를 포함한 v1 full rebuild 수치다. 현재 DB SHA-256은
`4daab85638b6d6fa1c0f1ebd4070d4050dca57fbfd9aed7e39a8aef2399a1450`이다. source 검증은
내부 XLSX 8/8, fast 153/153(14.90초), full pytest 158/158(104.57초), gold/policy
50/50·plan subset 40/40·선언 assertion 103, 실제 HTTP 15/15, 100요청·동시성 10 부하
0 failure·p95 131.75ms, runtime compliance 28 files/0 findings가 통과했다. 상세 명령·시간·외부 gate는
`docs/06_TEST_REPORT.md`를 기준으로 한다.

## Read first

작업 전에 다음을 읽고 서로 충돌하는 표현이 있으면 최신 실행 상태에 맞게 문서만 갱신한다.

Codex를 처음 여는 사람은 명령·secret·설명회 반영·freeze 순서를 정리한
`CODEX_USAGE_GUIDE.md`도 함께 사용한다.

1. `AGENTS.md`
2. `README.md`
3. 이 `CODEX_MASTER_PROMPT.md`
4. `docs/01_PDF_FULL_TRANSCRIPTION.md`
5. `docs/01A_TEAM_EMAIL_TRANSCRIPTION.md`
6. `docs/00_OFFICIAL_WEB_SNAPSHOT.md`
7. `docs/02_REQUIREMENTS_BASELINE.md`
8. `docs/03_DATA_AUDIT_AND_SEMANTIC_MODEL.md`
9. `docs/04_DATASET_REPORT.md`
10. `docs/04_PRODUCT_ARCHITECTURE_SPEC.md`
11. `docs/05_MVP_E2E_EXECUTION_PLAN.md`
12. `docs/06_TEST_REPORT.md`
13. `docs/06_API_CONTRACT_DRAFT.md`
14. `docs/07_TEST_AND_EVALUATION_PLAN.md`
15. `docs/08_BRIEFING_QUESTIONS_AND_DIFF_PROCESS.md`
16. `docs/10_RELEASE_FREEZE_RUNBOOK.md`
17. `docs/11_IMPLEMENTATION_HANDOFF.md`
18. `docs/12_TECHNICAL_PROPOSAL_DRAFT.md`
19. `artifacts/requirements_traceability.csv`
20. `registry/*.csv`
21. `contracts/*.json`, `contracts/openapi-provisional.yaml`
22. `tests/gold_queries_v0.jsonl`, `tests/policy_queries_v0.jsonl`

원문 재확인이 필요하면 `inputs/official_task.pdf`를 읽되 수정하지 않는다.

## Constraints: 절대 조건

### LLM

- 제출·평가 runtime의 유일한 언어모델은 HyperCLOVA X다.
- OpenAI·Anthropic·Google·Cohere 등 다른 LLM SDK/API/key/fallback/judge/router를 넣지 않는다.
- Codex는 개발 도구일 뿐 runtime dependency가 아니다.
- HCX 장애 시 다른 LLM으로 우회하지 않는다. 확실한 deterministic subset 또는 통제된
  unavailable만 허용한다.
- HCX가 상품을 직접 고르거나 숫자를 계산하게 하지 않는다.

### 데이터와 답변

- 원본 PDF/ZIP/XLSX를 수정하지 않는다.
- 상품·숫자·단위·기준일·source를 만들지 않는다.
- missing/null/blank/sentinel/zero를 구분하고 missing을 zero로 바꾸지 않는다.
- 기간·통화·단위·basis·위험척도가 다른 값은 혼합 비교하지 않는다.
- snapshot을 실시간이라고 말하지 않는다.
- 이상치를 조용히 삭제·clipping·winsorize하지 않는다.
- 데이터에 없는 미래 수익률을 예측하지 않는다.
- “반드시 사라” 같은 단정적 투자추천을 하지 않는다.
- 내부 chain-of-thought를 출력하지 않는다. `think_trace`는 execution audit만 쓴다.
- 외부·보강 데이터가 주최 측 제공 데이터와 충돌하면 주최 측 제공 데이터를 우선한다.
- 공식 요구사항끼리 충돌하면 양쪽 원문과 날짜를 기록하고, 더 최신이고 구체적인 주최 측
  공지를 운영 기준으로 사용하되 서면 확정 전에는 conflict를 닫지 않는다.

### 실행 보안

- HCX의 raw SQL·URL·tool name·arbitrary expression을 실행하지 않는다.
- field/metric/operator/scope/limit은 서버 allowlist로 다시 검증한다.
- SQL은 parameterized query만 사용한다.
- 금융 계산은 Decimal과 versioned formula/rounding을 쓴다.
- GET query에 비공개 문제가 있으므로 access log/APM에서 query string을 기록하지 않는다.
- API key, signing key, 평가문제 원문을 로그·응답·manifest에 넣지 않는다.
- `Cache-Control: no-store`를 유지한다.

### 미확정 조건

아래는 설명회 전 임의 확정 금지다.

- 최종 API path/parameters/다섯 field/type/error/timeout/QPS
- `retrieved_context`와 `think_trace`의 최종 요구 수준
- 정확한 HCX credential/credit/QPM/TPM/허용 model 고정방식
- fee/return/AUM/bond yield 단위와 zero 의미
- 국내 AUM 우선 field
- rating/risk order
- 펀드 attribute codebook과 share-class 공식 식별
- 교차 상품군 metric 비교/환산 정책
- embedding/reranker 세부 허용범위
- 마감 후 동일 image restart·secret rotation·health failover 허용범위
- API 종료일 09.20 대 09.30

이 항목은 adapter·registry·config 경계에 유지하고 `OPEN_QUESTION`으로 표시한다.

## 구현 전략

### 요청 흐름

```text
Organizer GET adapter
-> input/safety guard
-> configured HyperCLOVA X structured planner
-> Pydantic + semantic + allowlist validation
-> deterministic DuckDB compiler/executor
-> EvidenceBundle
-> deterministic evidence renderer
-> claim/safety/contract validation
-> strict JSON response
```

### 역질문은 P0

단순 `needs_clarification=true`로 끝내지 않는다.

- 결과를 바꾸는 missing slot을 구체적으로 식별한다.
- 일반 missing slot에는 2~4개의 유효한 선택지를 준다.
- 서버가 실제 catalog 검색 결과에서 만드는 `product_identity` 후보만 2~12개를 허용한다.
  HCX가 임의 후보를 만들거나 이 예외를 일반 역질문에 적용해서는 안 된다.
- 이미 파악한 상품군·filter·limit·entity를 preserved plan에 남긴다.
- follow-up token 변조·만료를 검증한다.
- 후속 답변은 원 질문과 보존된 조건을 합쳐 다시 plan/validation한다.
- scope→period처럼 여러 slot이 순차적으로 부족하면 한 번에 추정하지 말고 필요한 slot을
  계속 확인한다.
- 수익률 기간 선택지는 해당 scope의 registry에서 실제 사용 가능한 기간만 만든다. 해외
  ETP처럼 기간수익률 field가 없으면 가짜 기간 선택지를 만들지 말고 `UNAVAILABLE`과
  AUM·종가·거래량 같은 실제 가능한 대안을 제시한다.
- 이름 중복이면 arbitrary first row를 선택하지 말고 후보를 반환하거나 구분 조건을 묻는다.

### 복합 catalog filter

- 자산유형·지역·위험등급·연금 가능 여부는 scope별 source catalog의 정확한 원본 label로만
  실행한다.
- 자연어 표현은 bounded resolver가 검증된 label로 치환하고 raw·normalized label evidence를
  함께 남긴다.
- 해당 scope에 정확한 label이 없거나 scope가 모호하면 임의 추정하지 않고 fail-closed한다.

### 상품 설명

- `explain`은 정확한 상품명·코드·ticker로 단일 상품이 확정된 경우에만 실행한다.
- target이 없거나 여러 상품이 매칭되면 `explanation_target` 또는 `product_identity`를
  역질문한다.
- 답변은 source-backed 상품사실과 원본 운용전략·benchmark 등 실제 field만 사용한다.
- target은 정확하지만 요청한 설명 field 값이 없으면 `PARTIAL_WITH_COVERAGE`와
  `SOURCE_FIELD_ABSENT`로 누락 metric을 밝히고 값을 만들지 않는다.
- 개방형 금융교육, 데이터 밖 상품 해설, 투자 의견을 자유 생성하지 않는다.

### 다중 metric 순위

- 사용자가 명시한 우선순위가 있으면 lexicographic ordering을 사용하고 QueryPlan에 순서를 둔다.
- “낮은 보수와 높은 AUM을 적당히 섞어”처럼 가중치가 없으면 composite score를 만들지 말고
  `ranking_priority`를 역질문한다.
- 1차 metric은 `INNER JOIN`으로 유효 모집단을 정하고, 2차 이후 metric은 `LEFT JOIN`으로
  유지해 보조값 결측 때문에 상품을 제거하지 않는다.
- 모든 요청 metric을 evidence와 답변에 표시하고, 보조값 결측은 `NULLS LAST`와 확인 불가로
  표현한다. stable tie는 `product_uid ASC`다.

### 교차 상품군과 집계

- “국내 ETF와 공모펀드는 각각 몇 개인가” 같은 단순 count는 scope별
  `COUNT(DISTINCT product_uid)`로 분리해 source table·ID field 근거와 함께 반환한다.
- 통화·기간·단위·위험척도가 섞인 교차 순위는 계속 차단한다.
- `sum/avg/min/max`는 filter 후 같은 metric·통화의 최신 사용 가능 기준일 universe에서
  계산하고 source row count·as-of·unit을 evidence로 검증한다.

### answerability

반드시 다음 중 하나다.

- `FULL`
- `PARTIAL_WITH_COVERAGE`
- `NEEDS_CLARIFICATION`
- `NO_RESULT`
- `UNAVAILABLE`
- `INCOMPARABLE`
- `SAFETY_LIMITED`
- `DATA_QUALITY_BLOCKED`

답변과 reason, coverage, limitation이 모순되면 안 된다.

## 지금 실행할 작업 순서

1. 변경사항과 파일 구조를 읽고 `AGENTS.md` 위반을 먼저 찾는다.
2. `make verify`로 source hash를 확인한다.
3. DB가 없거나 source/ETL/registry가 바뀌었으면 `make build-data`를 실행한다.
4. `make test-fast`를 실행하고 failure를 원인별로 고친다.
5. `tests/test_etl.py`로 full-source clean rebuild를 검증한다.
6. `make compliance`로 non-HCX runtime 흔적 0을 확인한다.
7. Docker를 깨끗이 build하고 local deterministic mode로 container를 실행한다.
8. `scripts/e2e_smoke.py`를 real HTTP로 실행한다.
9. 제공된 실제 HCX credential과 주최 측 허용 model ID가 있으면 secret을 출력하지 않고
   live HCX smoke를 실행한다.
10. real HCX plan이 local schema/allowlist를 통과하고 같은 deterministic result를 내는지 확인한다.
11. 설명회 녹취가 있으면 먼저 source diff를 만들고 API/model/data policy를 갱신한다.
12. 공식 참고질의가 들어오면 blind holdout과 별개로 새 fixture를 만든다.
13. deployment target/권한이 제공된 경우에만 public deployment를 수행한다.
14. release candidate에서 test report, image digest, Git SHA로 final manifest를 생성한다.
15. freeze 이후 결과를 바꾸는 어떤 action도 하지 않는다.

routine failure는 묻지 말고 안전한 범위에서 고친다. 다만 다음은 멈추고 사용자에게 묻는다.

- 공식 source와 충돌하는 요구
- secret/credential이 없는데 live HCX 또는 deploy가 필요한 경우
- 유료 cloud resource 생성 또는 공개 배포 권한이 필요한 경우
- 설명회 답변에 따라 제품 결과가 크게 달라지는 미확정 선택
- 원본·release를 삭제/덮어쓰는 파괴적 조치

작업 중 기존 구현을 이해하지 못했다는 이유만으로 별도 scaffold를 만들지 않는다. 먼저
호출 경로와 테스트를 추적하고, 가장 가까운 기존 모듈을 수정한 뒤 관련 테스트부터 실행한다.

## 필수 명령

```bash
make setup
make verify
make build-data
make test-fast
.venv/bin/python -m pytest -q tests/test_etl.py
make compliance
make run
.venv/bin/python scripts/e2e_smoke.py --base-url http://127.0.0.1:8080
```

Docker local E2E:

```bash
docker build -t mirae-agent:local .
docker run --rm -p 8080:8080 \
  -e APP_ENV=test \
  -e PLANNER_MODE=deterministic \
  mirae-agent:local
```

Production은 `APP_ENV=production`, `PLANNER_MODE=hcx`, `CLOVA_STUDIO_API_KEY`,
`CLARIFICATION_SIGNING_KEY`가 없으면 fail fast해야 한다.

## 현재 남은 blocker

다음은 외부 입력·검증이 필요한 상태다. 이미 통과한 local gate와 외부 gate를 섞어 모두
완료했다고 표현하지 않는다.

1. 실제 팀 HCX credential로 live plan과 네 상품군 E2E 미실행
2. Docker fresh build/run/restart와 immutable image digest 미검증
3. public deployment target·도메인·TLS·운영권한 없음
4. 실제 release Git SHA 미확정
5. 2026-08-06 주최 측 설명회의 최종 API contract·허용 HCX model 확인 전
6. 공식 참고 질의 set 없음
7. fee/AUM/return/bond unit·zero semantics·rating/risk order 미확정
8. 개방형 금융교육·투자해설은 과제 데이터 밖이므로 의도적으로 범위 밖
9. 최종 기술제안서 형식·페이지·용량 미확정
10. test report와 release manifest는 **DRAFT only**다. 실제 Git SHA·registry image digest·
    image-extracted DB hash·외부 gate PASS 없이는 FINAL로 승격하지 않는다.
11. 공식 로고 원본·brand usage license 미확정
12. 이메일이 언급한 Green Factory 동선 안내 PDF 미제공

이 blocker를 가짜 값으로 채우지 마라. 코드로 준비 가능한 부분은 adapter·test·runbook까지
완성하고, 필요한 질문과 영향범위를 정확히 보고한다.

## Done when

- source PDF/ZIP/team email 3개와 내부 8 XLSX hash 및 raw 행·열이 일치한다.
- 네 상품군이 raw/clean/canonical/serving에 존재한다.
- 펀드 반복가중, 10개 quarantine, PARTIAL 유지가 정확하다.
- 207 source field registry와 59개 실행 metric policy가 source·ETL·runtime과 일치한다.
- lookup/search/filter/rank/compare/aggregate/bounded-explain/cross-policy/clarify/unsupported가 동작한다.
- 40 gold + 10 policy fixture, 40 plan subset과 103개 선언 assertion이 모두 통과한다.
- 상품명·숫자·순서·집계가 EvidenceBundle로 source row/field까지 추적된다.
- scope별 교차 count, 복합 catalog filter, exact-target 설명의 raw label·전략·benchmark가
  source field로 추적된다.
- 다중 metric 순위가 primary 모집단을 보존하고 모든 metric·누락값·blocking limitation을 렌더링한다.
- coverage가 raw/present/valid/serving/type-specific/rankable을 혼용하지 않는다.
- 역질문이 missing slot/options/preserved plan/follow-up을 실제 E2E로 통과한다.
- 주최 측이 허용한 정확한 HCX model의 live plan이 local validation을 통과한다.
- 비-HCX LLM SDK/endpoint/key/fallback이 0이다.
- forecast·보장·단정 추천·missing=0·fake realtime 위반이 0이다.
- fresh Docker build→start→ready→real GET→restart→same result가 통과한다.
- production query string/secret이 로그에 남지 않는다.
- README/OpenAPI/기술제안서/release manifest가 실제 release와 일치한다.
- public endpoint가 공식 contract와 운영기간을 충족한다.
- freeze 이후 결과 변경이 없다.

## 최종 보고 형식

작업 종료 보고는 장황한 일지가 아니라 다음을 순서대로 쓴다.

1. 이번에 실제 완성한 결과
2. 실행한 검증과 정확한 통과/실패 수
3. source/data/model/API/safety 준수 상태
4. 아직 외부 입력이 필요한 blocker
5. 사용자가 바로 실행할 명령
6. 변경한 주요 파일 링크

“완료”라는 표현은 위 Done when 중 실제 검증된 항목에만 사용한다.

---
