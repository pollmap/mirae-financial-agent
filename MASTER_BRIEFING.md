# 미래에셋증권 금융상품 Agent — 전체 현황·요구사항·개발 브리핑

기준일: 2026-08-03  
단계: 2026-08-06 오프라인 설명회 전 실행 MVP·v1 local DRAFT gate 통과, 외부 gate 대기  
최상위 기준: 동봉된 과제 PDF와 데이터 ZIP

> 최신 수치·검증 상태는 `README.md`와 `docs/06_TEST_REPORT.md`를 기준으로 하고,
> 개발 인계 절차는 `CODEX_USAGE_GUIDE.md`, `CODEX_MASTER_PROMPT.md`,
> `docs/11_IMPLEMENTATION_HANDOFF.md`를 함께 확인하십시오.

## 1. 결론부터

우리가 만들 것은 범용 금융 챗봇이나 개인화 투자추천기가 아닙니다. 국내채권·국내
ETF·해외 ETF·공모펀드에 관한 자연어 질문을 이해하고, 주최 측 구조화 데이터에서
상품을 검색·필터·비교·계산·집계한 뒤, 원본 행과 필드로 추적되는 근거 및 한계를
한국어로 설명하는 `Financial Product Analyst`입니다.

가장 합리적인 구현은 다음과 같습니다.

```text
평가 GET
→ 입력 검증·로그 비식별화
→ HyperCLOVA X가 typed QueryPlan 생성
→ JSON Schema + Metric Registry 검증
→ DuckDB가 결정론적으로 검색·계산
→ Evidence Bundle 생성
→ 근거형 답변 작성
→ 모든 factual claim·금지표현 검증
→ PDF 예시 호환 JSON 반환
```

HCX는 질문을 typed plan으로 구조화합니다. 실제 상품 선택·순위·숫자 계산과 현재 답변
rendering은 SQL·Decimal·deterministic template이 담당합니다. 이 경계가 정량 정확도,
정성평가, 금융 리스크를 동시에 잡는 핵심입니다.

## 2. 대회의 목적과 우리 주제

`OFFICIAL_PDF`

- 제10회 2026 미래에셋증권 AI Festival의 금융상품 Agent 주제
- 공식 영문 역할명: `Financial Product Analyst`
- 서로 다른 네 금융상품 마스터를 분석·구조화
- 자연어에서 상품군과 복합조건을 해석
- 조회·검색·필터·비교·정렬·순위·집계·계산 수행
- 상품군을 넘나드는 질문 처리
- 데이터 근거와 참조 데이터를 포함한 답변
- 확인할 수 없는 질문은 불가 사유 또는 필요한 조건 역질문

## 3. 절대 준수할 조건

### 모델

- 평가 runtime의 LLM은 HyperCLOVA X 계열만 사용합니다.
- 다른 LLM을 runtime·fallback·router·judge·summarizer로 사용하면 평가대상에서 제외됩니다.
- 현 코드의 `HCX-007` 기본값은 Native Structured Outputs를 위한 `TEAM_DECISION`이며 주최 측
  공식 model 지정으로 표현하지 않습니다. 정확한 허용 ID는 설명회 전 `OPEN_QUESTION`입니다.
- Codex는 개발 도구로만 쓰고 제출 image·환경·설정에서 다른 LLM SDK·endpoint·key를
  제거합니다. 개발과정 Codex 허용 여부는 설명회에서 확인합니다.

### 답변

- 상품명·숫자·날짜·단위·설명은 주최 데이터 또는 명시된 외부 근거에 존재해야 합니다.
- 참조 데이터가 보여야 합니다.
- 확인 불가한 것은 확인 불가라고 말하거나 결과를 바꾸는 핵심조건을 묻습니다.
- 데이터 없는 수익률 전망과 단정적 투자추천을 생성하지 않습니다.
- 결측을 0으로 바꾸거나 서로 다른 기간·단위·통화·위험척도를 임의 통합하지 않습니다.
- 2026-07-11 스냅샷을 실시간으로 표현하지 않습니다.

### 제출·운영

- 주최 측 GitHub Organization의 private repository에 제출합니다.
- 필수 3종: 소스·재현환경·README, 기술제안서, endpoint URL·요청/응답 JSON 명세
- 평가자가 Public 망의 팀 API에 GET 요청을 보냅니다.
- PDF API 활성 표기는 09.07~09.20, 전체 예선평가는 09.07~09.30입니다.
- PDF 마감은 09.06, 공식 공개 홈페이지는 09.06 23:59로 보완합니다.
- 마감 후 commit/push/server deploy 등 코드·결과물 변경이 발견되면 `실격 처리`입니다.
- NCP credit 한도 초과비용은 주최 측이 보전하지 않습니다.

## 4. 평가

### 정량

- 비공개 상·중·하 난이도 질문
- 평가 담당자가 GET endpoint 호출
- 반환 답변 평가

### 정성

1. 문제정의
2. 기술완성도·성능
3. 창의성·확장성
4. 답변의 정확성·완결성
5. 현업 활용성·리스크 관리

정량·정성을 합쳐 6팀이 결선에 진출하고, 결선은 PT와 라이브 시연입니다.

## 5. 데이터 전체 현황

원본 8 XLSX와 PDF 공식 수가 정확히 일치합니다.

| 데이터 | 원본 행 | 필드 | 핵심 serving 단위 |
|---|---:|---:|---|
| 국내채권 PRBD01N001 | 42,394 | 40 | `PD_NO` 42,394 고유 |
| 국내 ETP PREF01N001 | 1,734 | 73 | ETF 1,202 + ETN 532; placeholder 1 |
| 해외 ETP PREF02N001 | 5,646 | 49 | ETF 5,587 + ETN 59; placeholder형 8 |
| 펀드 PRFD01N001 | 95,619 | 45 | `itm_no` 11,139; 전체 정상 상품 11,138 |

총 원본은 145,393행, 207필드, 완전 중복행 0입니다.

### 국내채권

- `PD_NO`는 전 행 고유
- 장내 24,749, 장외 17,645
- 매수수익률·매수가능수량 coverage 881/42,394
- 매수가능수량 양수 325, 0값 556
- `AVG_ANNUAL_TAX_YIELD` 유효 881건 전부 0이므로 순위 사용 차단
- 신용등급은 약 41% 결측; 공식 정렬표 전에는 “좋은 순” 차단
- 원본 잔존일과 만기일이 충돌할 수 있어 둘을 분리하고 만기일 재계산값을 별도 보존

### 국내 ETP

- 데이터명은 ETF지만 실제로 ETN 532개 포함
- 품질이상 placeholder 1행 격리; usable ETF 1,201
- 1년 수익률은 ETF-only source-present 986/1,201, quality-valid 951/1,201,
  공통 최신일 2026-06-15 rankable 940/1,201로 구분
- 총보수는 217건만 있고 0값 150, ETN은 값 없음
- 기초지수 58건
- 추적오차·괴리율·분배 관련 일부 열은 전부 0/공란이어서 의미 확인 전 사용 차단

### 해외 ETP

- `pd_itm_no` 고유; ISIN은 공란 9·중복 초과행 50이라 PK가 아님
- placeholder형 8행 격리, 부분행 2개는 품질 flag와 함께 유지
- raw 기준 총보수 0값 363, raw ETF-only 0값 312; serving ETF 5,579개 중 0값 304
- AUM은 raw present 5,459, serving ETP 5,451, serving ETF 5,395
- NAV는 serving ETP 682, 괴리율은 serving ETP 3
- 1일 수익률은 raw present 5,388; serving ETF present 5,329건이 전부 0이라 순위 불가
- 1개월·3개월·6개월·1년·YTD 수익률과 위험등급 source field가 없음

### 펀드

- 95,619행은 상품 95,619개가 아니라 상품×속성 tag 반복
- 전체 정상 `fund_product` 11,138개; API 기본 모집단은 공모 11,115개
- raw attribute 95,619, serving bridge 95,618, 손상행 1 quarantine
- 전체 serving은 공모 11,115·사모 15·공사모 구분 결측 8
- 공모 기본 모집단의 판매중 8,445·판매완료 2,670
- 전체 serving 순자산 coverage 9,290/11,138
- 공모 위험등급 valid 8,564/11,115·missing 2,551; 전체 serving 위험등급은
  8,565/11,138
- 보수·설정일·보유종목·환매조건·row별 기준일은 원본에 없음
- `prfd_attr_cd` 228종 공식 codebook이 없어 의미를 임의 추정하지 않음

## 6. 제품 설계

### 데이터 계층

1. raw: 원본 바이트·파일명·sheet·Excel row 보존
2. clean: 타입 parse·Unicode·sentinel·quality flag; 값 수정 없음
3. canonical: 공통 product catalog + 상품군별 detail
4. serving: DuckDB/Parquet view + source locator + Metric Registry

현재 source of truth는 canonical field 207행과 v1 metric policy 59행입니다. ETL과 runtime이
같은 v1 policy를 읽고, v1 full rebuild의 serving metric evidence는 1,156,332행입니다.
`raw row`는 물리 행, metric policy의 `raw_denominator`는 격리 전 논리 상품/listing이므로
펀드 attribute 95,619행과 logical product 11,139개를 혼용하지 않습니다.

### QueryPlan

- intent: lookup/search/rank/compare/aggregate/explain/clarify/unsupported
- scopes: bond/domestic_etp/overseas_etp/fund
- entity·filter group·metric·aggregation·sort·group_by·limit
- 구조는 JSON Schema, 의미는 registry semantic validator로 검증
- raw SQL·URL·tool name·임의수식 생성 금지
- invalid Structured Output/semantic plan은 임의 repair 없이 controlled unavailable로 fail-closed
- 429·5xx의 bounded transport retry는 의미적 repair와 별개

일반 역질문은 2~4개 선택지를 사용합니다. 동일·유사 상품명처럼 서버가 catalog에서
생성하는 `product_identity` 후보만 2~12개까지 허용합니다.

`explain`은 정확한 상품명·코드·ticker로 단일 상품 target이 확정될 때만 실행합니다. target이
없거나 여러 개면 역질문하고, 원본 상품사실과 source-backed 운용전략·benchmark를
deterministic renderer로 설명합니다. 원본에 없는 개방형 금융교육·투자해설을 자유 생성하지
않습니다.

### Evidence와 답변

모든 factual claim은 evidence ID를 가져야 합니다. Evidence에는 dataset, file, sheet,
Excel row, field, raw/normalized value, unit, as-of 상태, row hash가 포함됩니다. 펀드처럼
값은 있지만 개별 기준일이 없는 경우 날짜를 추정하지 않고 `as_of_date=null`,
`as_of_status=DATASET_SNAPSHOT_ONLY`로 표시하며 “개별 기준일 미제공”을 밝힙니다.
요청한 필드·값 자체가 원본에 없을 때만 답변 가능성을 `UNAVAILABLE`로 처리합니다.

## 7. MVP 우선순위

### P0 — 제출 가능한 E2E

아래 1~8·10과 로컬 API 경로는 구현·검증되었습니다. v1 DB 기준 fast pytest
153/153(14.90초), full pytest 158/158(104.57초), 50 fixture 50/50
(40 plan subset·103 선언 assertion), 실제 HTTP 15/15가 통과했습니다. 9번 Docker gate와
실제 HCX key E2E·public TLS·Git/image digest·8월 6일 주최 측 contract는 아직 외부 gate입니다.
현재 상태는 로컬 검증 `DRAFT`이며 `FINAL` 또는 production-ready로 부르지 않습니다.

1. 원본 hash·행·열·schema fail-closed gate
2. 네 상품군 ETL·serving view
3. ID/name lookup
4. 복합조건 search/filter
5. rank·compare·aggregate
6. Evidence Bundle·Answerability
7. HCX typed planner
8. provisional GET adapter
9. clean Docker→실제 HTTP→restart→same result
10. 40 gold + 교차·안전 fixture

### P1 — 점수와 운영성

- alias·fuzzy search
- claim validator
- fault/load·latency 측정
- public 배포·credit 알림·log redaction
- 제안서·데모·release freeze rehearsal

### 설명회에서 확정한 뒤 고정할 것

- 정확한 API 고정 contract
- 주최 측 허용 HCX model ID·quota·credit
- UI
- 외부 데이터
- 개인화·포트폴리오·실시간 시세
- 대규모 multi-agent·GraphDB

UI와 외부 데이터보다 네 상품군 전체 E2E·근거·정확도·운영 고정이 먼저입니다.

## 8. 설명회에서 반드시 확정할 것

- `/answer`·2개 필수 query parameter·5 response field가 고정인지 예시인지
- 팀 확장인 `clarification_token`/`clarification_response` pair를 평가기가 허용하는지
- `retrieved_context`, `think_trace` 형식·길이
- timeout·QPS·concurrency·retry·status·인증
- 실제 API 운영 종료일 09.20/09.30
- freeze 이후 동일 image restart·failover 허용범위
- 주최 측 허용 정확한 HCX model ID·credential·credit·rate limit
- Codex 개발 사용 허용
- 참고 질의·채점 형식·오차·동률·NULL 정책
- ETF 질의의 ETN 포함 여부와 펀드 상품단위
- 지표 단위·0·sentinel·등급순서·통화
- 펀드 attribute codebook·share class·cross-source 중복
- 공식 로고 사용 허용

## 9. 현재 구현·검증 현황

### 구현 완료

- PDF 8페이지·팀 이메일·공식 웹 snapshot과 source priority 기준선
- PDF/ZIP/내부 XLSX 8개 SHA·dimension fail-closed 검증
- raw/clean/canonical/serving DuckDB ETL, 펀드 attribute bridge, source locator, quarantine
- canonical field 207행, v1 metric policy 59행, synonym 146행, quarantine rule 13행
- v1 serving 상품 60,903개, fund attribute 95,618행, metric evidence 1,156,332행
- QueryPlan 1.1, v1 registry validator, parameterized DuckDB execution, Evidence Bundle 1.1
- 조회·검색·필터·순위·비교·집계·정확한 상품 explain용 deterministic evidence renderer,
  금융 안전정책, 명시적 역질문과 서명된 후속 token
- scope별 distinct product count, registry-backed 수익률 기간, bounded catalog filter
- 다중 metric primary INNER·secondary LEFT/NULLS LAST, 모든 요청 metric·blocking limitation 렌더링
- 지표·필터별 공통 최신 기준일의 `sum/avg/min/max`·단일 통화 integration validation
- FastAPI GET `/answer`, provisional five-field response, optional clarification pair
- 40 gold + 10 policy fixture, 현재 full pytest 158개, 실제 TCP HCX mock,
  contract·ETL·compliance test
- multi-stage Dockerfile(builder full requirements, runtime `requirements-runtime.txt` 최소 dependency,
  원본 source·ETL 제외), OpenAPI·JSON Schemas, release freeze runbook과 manifest generator

### 현재 검증 상태

- 내부 XLSX 8개 source verification, v1 full rebuild, Ruff 통과
- raw 145,393·logical 60,913·serving 60,903·quarantine 10,
  fund attribute 95,618·metric evidence 1,156,332 검증
- serving DB SHA-256
  `4daab85638b6d6fa1c0f1ebd4070d4050dca57fbfd9aed7e39a8aef2399a1450`
- current fast gate 153/153(14.90초), full pytest 158/158(104.57초) 통과
- gold 40 + policy 10 fixture 50/50, plan subset 40개와 선언 assertion 103개 통과
- runtime compliance 현재 스캔 28 files/0 findings, current-code 실제 HTTP E2E 15/15 통과
- compliance 0 findings는 현재 정적 스캔 결과일 뿐 정책 준수의 절대적 증명이 아님
- HTTP 부하 smoke 100/100 성공·concurrency 10·0 failure(p95 131.75ms)
- 최신 로컬 검증은 158-pass이며 release manifest는 외부 gate 전까지 `DRAFT`

### 외부 입력·권한 blocker

- 8월 6일 설명회 녹취·현장자료·주최 측 최종 API/평가 contract·공식 참고질의
- 주최 측 허용 정확한 HCX model ID, 실제 팀 credential, credit, QPM/TPM 및 live E2E
- 현재 환경에 Docker/Podman이 없어 fresh build/run/restart 실증 미완료
- public deployment target·도메인·TLS·운영권한
- fee/AUM/return/bond yield 단위·0 의미·rating/risk 순서
- 최종 기술제안서 페이지·용량 형식, 공식 로고 원본·사용허가
- DRAFT manifest의 test=158·DB hash를 최신 로컬 증빙과 재검증하고, Git SHA·image digest
  placeholder는 실제 immutable image 값으로 교체해야 함; 이 작업과 외부 gate 전에는 FINAL 아님
- 이메일이 언급한 Green Factory 동선 안내 PDF

다음 입력은 설명회 녹취·현장자료입니다. 이를 받으면 원문 보존→요구문장 추출→기존
`OFFICIAL_*`/`TEAM_DECISION`과 diff→contract/registry/test 갱신 순서로 반영합니다. 이미
구현된 walking slice를 다시 만드는 것이 아니라 변경된 공식 조건만 안전하게 흡수합니다.
