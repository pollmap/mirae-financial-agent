# MVP·E2E 실행계획

상태: 설명회 전 작성한 v0.9 일정 기준선(현재 구현 현황 문서 아님)  
최종 제출 예정일: 2026-09-06

> 이 문서의 M0~M2 구간은 최초 계획과 남은 release 목표를 보존합니다. 2026-08-03 현재
> M0/M1 핵심 로컬 경로는 이미 구현되어 v1 local gate를 통과했습니다. 정확한 구현·검증
> 상태는 `../README.md`, `06_TEST_REPORT.md`, `11_IMPLEMENTATION_HANDOFF.md`를 우선합니다.
> 아직 완료로 간주하지 않는 항목은 Docker fresh build/restart, live HCX, public TLS,
> 최종 Git SHA·image digest입니다. `explain`은 exact-target source-backed 범위로 구현됐으며
> 개방형 금융교육·투자해설은 의도적으로 범위 밖입니다.

## 1. MVP 완료 정의

MVP는 네 상품군을 실제 데이터로 처리하고, 공개 GET 요청 한 번이 다음 전체 경로를
통과하는 상태입니다.

```text
real HTTP GET
-> request validation
-> real HyperCLOVA X planner
-> validated QueryPlan
-> real DuckDB query
-> evidence bundle
-> grounded answer
-> contract validation
-> public JSON response
```

Mock 화면이나 일부 상품군 데모만으로는 MVP 완료로 보지 않습니다.

## 2. 범위

### P0 - 제출 가능한 MVP

- 네 원본 데이터셋 전체 ingestion
- 공모펀드 다중 속성 반복 제거
- canonical 상품 catalog
- 핵심 Metric Registry
- 상품명·코드 상세조회
- 복합 조건 검색
- 정렬·Top-N
- 2개 이상 상품 비교
- count·평균·최대·최소 집계
- 제한적인 교차 상품군 비교
- 정확한 단일 상품의 source-backed 설명; target 누락 시 역질문
- HCX QueryPlan
- Answerability Engine
- 필드 단위 evidence
- 확인 불가·명확화·안전 제한
- PDF 예시 호환 GET API
- Docker build·run
- 공개망 E2E
- 재현 README·API spec

### P1 - 예선 점수 향상

- 한국어 동의어·약칭·오타
- AND·OR·부정조건·범위조건
- 동일 질문 일관성
- tie·NULL·sentinel·coverage 정책
- HCX timeout·429·5xx 처리
- 동시요청·부하·재시작 검증
- evidence와 answer claim 자동 대조
- 회귀 120개 이상

### P2 - 결선 대비

- 최소 비교 UI
- 결과 provenance drill-down
- 품질·운영 dashboard
- 외부 설명 데이터
- 발표·라이브 시연 흐름

## 3. 설명회 전 8/2~8/5

코드 계약을 성급하게 고정하지 않고, 설명회와 무관하게 재사용되는 작업을 완료합니다.

### 8/2 - 공식 기준선

- PDF 8페이지 전사와 이미지 검수
- 원본 SHA-256
- 요구사항·금지사항 matrix
- 공식·데이터·웹·팀결정·미확정 라벨
- 설명회 질문 초안

완료조건:

- 일반 추출에서 누락되는 6·7페이지 문구 포함
- API 운영 09.20과 평가 09.30 구분
- 마감 후 변경 `실격` 명시

### 8/3 - 데이터 감사

- 8 XLSX 전수 프로파일
- 행·열·schema reconciliation
- key·중복·결측·0·sentinel·극단값
- 펀드 item·attribute 분리
- field dictionary
- Metric Registry v0
- 답변 가능·부분 가능·불가능 질의표

완료조건:

- 총 145,393행 일치
- fund 11,139 `itm_no`와 95,619 attribute row 구조 입증
- 해외 ISIN을 key로 사용하지 않음
- 손상행 quarantine 규칙

### 8/4 - 계약·테스트 설계

- QueryPlan JSON Schema
- provisional API contract
- answerability 상태
- evidence schema
- query taxonomy
- gold 질의 40개와 기대 QueryPlan·결과 정의

### 8/5 - 배포·동결 설계

- 아키텍처·sequence·deployment diagram
- release manifest 규격
- NCP credit·model·network 질문
- 제출 후 변경 금지 runbook
- 설명회 1인 참석 질문지 최종화

## 4. 설명회 직후 8/6~8/7

### 입력

- 전체 녹취
- 현장 배포문서·화면 사진
- Q&A 답변
- 모델·크레딧·API 세부안내

### 처리

1. 시간순 전사 교정
2. 발언을 requirement·guidance·example·opinion으로 분류
3. 현재 문서와 source-by-source diff
4. `OPEN_QUESTION` 해소
5. API·HCX·운영 계약 freeze
6. 영향받은 test와 ADR 갱신

### 완료조건

- 개발자가 임의 해석할 핵심 계약이 없음
- 미확정 항목에는 안전한 adapter 또는 fail-closed 정책이 있음

## 5. M0 - Walking Skeleton, 8/8~8/13

목표: 네 상품군이 한 공개되지 않은 로컬 HTTP 경로를 실제로 통과합니다.

### 작업

- 저장소·Docker·CI 기본구조
- raw source hash gate
- 4종 ETL
- Parquet·DuckDB serving view
- 상품 ID·이름 lookup
- deterministic mock QueryPlan 입력
- evidence builder
- provisional response adapter
- 12개 E2E

12개 최소 matrix:

- 각 상품군: 상세조회 1
- 각 상품군: 조건검색 1
- 각 상품군: rank 또는 aggregate 1

### Done when

- 깨끗한 환경에서 한 명령으로 build
- 컨테이너 시작
- 12개 real GET 통과
- 모든 상품명·숫자가 source locator를 가짐

## 6. M1 - 제출 MVP, 8/14~8/23

### Agent

- real HCX gateway
- structured QueryPlan
- schema validation; invalid·semantic-invalid plan은 임의 repair 없이 controlled unavailable
- lookup, search, rank, compare, aggregate, clarify
- exact-target explain; source-backed 상품사실·원본 전략·benchmark만 deterministic render
- target 없는 개방형 설명은 역질문하고 데이터 밖 금융교육·투자의견은 범위 밖
- 다른 LLM fallback 없음

### Data semantics

- Metric Registry v1
- 단위·통화·기간·basis
- coverage·zero·missing policy
- fund item·attribute serving model
- ETF·ETN 내부 분리
- 상품군 비교 matrix

### Answer

- evidence bundle
- template renderer
- 선택적 개방형 HCX grounded explainer는 P1 이후 후보이며 현재 exact-target 설명에는 사용하지 않음
- claim validator
- forbidden recommendation·forecast filter

### API

- organizer GET adapter
- UTF-8·URL encoding
- input·error policy
- response schema validation

### Done when

- gold 40개 통과
- 네 상품군과 7 intent 포함
- 외부망 staging E2E
- 같은 validated QueryPlan·release의 product order·수치 동일

## 7. M2 - 품질·운영, 8/24~8/30

- gold·회귀 120개 이상
- 안전·적대 30개 이상
- 완전 blind 20개 이상
- typo·alias·복합조건
- NULL·tie·경계값
- HCX 429·timeout·5xx fault injection
- 동시 5·10·20 요청
- restart 후 동일 release 검증
- p50·p95 latency와 error rate 측정
- NCP public production 배포

## 8. 제출 준비, 8/31~9/3

- 기술제안서
- README
- API 명세
- 시스템 구성도·흐름도
- 테스트·품질 결과
- 운영 runbook
- secret scan
- dependency·license review
- 다른 LLM dependency·endpoint·key 부재 확인
- fresh environment rehearsal

## 9. Release freeze, 9/4~9/6

### 9/4

- release candidate 확정
- git tag
- Docker image digest
- source/data/prompt/schema/model manifest
- production config·restart policy 완료

### 9/5

- 새 환경에서 마지막 build·run
- public GET smoke·regression
- repository·proposal·API 문서 일치 확인
- backup·credit·certificate·DNS 확인

### 9/6

- 공식 마감시각보다 여유 있게 제출
- 제출 증적 보존
- 변경권한·mutable deployment 차단
- 관측과 사전 정의 자동 restart만 유지

## 10. 개발 repository 권장 구조

```text
app/
  api/
  agent/
  contracts/
  execution/
  evidence/
  safety/
data/
  raw_manifest/
  parquet/
  duckdb/
etl/
  readers/
  cleaners/
  canonical/
  quality/
registry/
  fields/
  metrics/
  synonyms/
tests/
  unit/
  contract/
  golden/
  e2e/
  blind/
  safety/
  load/
docs/
scripts/
Dockerfile
README.md
```

원본 XLSX를 Git에 넣을지, 대용량 링크로 제공할지는 주최 저장소 정책과 파일 크기를
확인한 뒤 결정합니다. 어떤 경우에도 raw hash와 재현 절차는 포함합니다.

## 11. 기능보다 먼저 통과해야 할 gate

### Data gate

- 공식 행·열 일치
- key·중복·손상행 처리
- raw↔canonical reconcile

### Contract gate

- 모든 응답 schema valid
- input echo 정책 일관
- GET·UTF-8·URL encoding

### Grounding gate

- 모든 상품명·숫자 claim evidence 존재
- 기준일·출처·coverage 포함

### Safety gate

- 전망·보장·단정 추천 없음
- 확인 불가·명확화 정상

### Runtime gate

- 다른 LLM 호출·SDK·key 없음
- clean Docker E2E
- public endpoint 재시작 후 정상

### Freeze gate

- source·git·image·prompt·model version 고정
- 제출물과 운영 release 동일
