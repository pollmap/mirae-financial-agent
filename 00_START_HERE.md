# 미래에셋증권 금융상품 Agent - 실행 MVP·Codex 인계 패키지

> **현재 진입점(2026-08-09)**: 이 파일 아래 내용은 2026-08-03 prebrief의 역사적
> 기준이다. 먼저 `docs/20_MAINLINE_HANDOFF_AND_REPOSITORY_MAP.md`를 읽고, 이어서
> 최신 대회 엔진 `docs/18_FINAL_MASTER_PLAN_AND_RELEASE_READINESS.md`, 팀 인간검증 챗봇
> `docs/19_HCX_HUMAN_QA_CHATBOT.md`를 읽는다. QA 챗봇은 대회 API를 변경하지 않으며,
> 실제 HCX와 사람 파일럿은 아직 `PENDING_EXTERNAL`이고 로컬 fixture는 live 검증이 아니다.

기준일: 2026-08-03  
현재 단계: 2026-08-06 오프라인 설명회 전 실행 MVP·v1 local DRAFT gate 통과,
실제 HCX key E2E·Docker fresh build/restart·public TLS·Git/image digest·주최 측 contract 대기

## 한 줄 정의

국내채권·국내 ETP(ETF/ETN)·해외 ETP(ETF/ETN)·공모펀드에 대한 자연어 질문을 HyperCLOVA X가
제한된 질의 계획으로 바꾸고, 결정론적 데이터 엔진이 검색·필터·비교·계산한 뒤,
원본 필드까지 추적 가능한 근거와 안전한 한국어 답변을 공개 GET API로 반환합니다.

Codex는 개발 도구이며 제출 runtime의 유일한 LLM은 HyperCLOVA X입니다.

이 제품은 범용 투자상담, 개인화 투자추천, 미래수익 예측 서비스가 아닙니다.

## 이 패키지에서 완료한 일

- 첨부 PDF 8페이지 전체 텍스트·이미지 이중 검수
- 일반 텍스트 추출에서 빠지는 6페이지 평가기준과 7페이지 API 운영조건 전사
- ZIP 내부 8개 XLSX 원본 무수정 전수 프로파일링
- 145,393행과 207개 원본 필드의 스키마·결측·0·고유키·중복 현황 추출
- 공식 조건, 홈페이지 보완, 배포 데이터 관찰, 팀 설계, 미확정 질문 분리
- 네 상품군 공통 시맨틱 모델과 비교 가능성 규칙 설계
- HyperCLOVA X 전용 Agent, GET API, 테스트, 배포·동결 E2E 설계
- 설명회 질문과 설명회 녹취 반영 절차 작성
- Codex가 따라야 할 저장소 규칙 작성
- PDF page별 요구사항→구현→검증 추적표 작성
- 4개 상품군 40개 실행형 gold fixture와 교차·안전 fixture 작성
- request·response·QueryPlan·Evidence·release manifest·OpenAPI 초안 작성
- 아키텍처·요청 sequence·배포 Mermaid와 freeze runbook 작성
- 공식 웹 로고·키비주얼 위치와 안전한 활용정책 정리
- 11-sheet 데이터 감사 workbook: `artifacts/MiraeAsset_Financial_Agent_Data_Audit.xlsx`
- 공식 전체 145,393행 raw→clean→canonical→serving ETL과 DuckDB 구현
- 60,903개 serving 상품·95,618개 fund attribute·v1 metric policy 59개·1,156,332개
  metric evidence 실제 빌드
- 논리 상품 60,913개·quarantine 10개 reconciliation 및 serving DuckDB SHA-256
  `4daab85638b6d6fa1c0f1ebd4070d4050dca57fbfd9aed7e39a8aef2399a1450` 검증
- 국내 ETF 1Y source-present 986/1,201·quality-valid 951/1,201·공통 최신일
  2026-06-15 rankable 940/1,201 구분
- 펀드 전체 serving 11,138개와 API 기본 공모 11,115개 분리; 공모 위험등급
  8,564/11,115(결측 2,551), 전체 위험등급 8,565/11,138, 판매중 8,445·판매완료
  2,670 검증
- `HCX-007` Native Structured Outputs adapter와 local validation 구현
  (`TEAM_DECISION` baseline이며 주최 측 공식 model ID는 `OPEN_QUESTION`)
- missing slots/options/preserved plan/token을 가진 역질문과 후속 질의 구현
- registry-backed 수익률 기간, bounded catalog filter, scope별 분리 교차 count 구현
- 정확한 상품 target의 source-backed explain, 다중 metric NULL 보존·전 지표 렌더링 구현
- GET `/answer`, health, 모든 blocking limitation renderer, 금융 안전정책, multi-stage Dockerfile 구현
- fast 153/153(14.90초), full pytest 158/158(104.57초), 40 gold + 10 교차·안전 fixture
  50/50·plan subset 40·선언 assertion 103, current-code 실제 HTTP 15/15 통과
- HTTP 부하 smoke 100/100 성공·concurrency 10·0 failure(p95 131.75ms), runtime compliance
  현재 스캔 28 files/0 findings, 내부 XLSX 8개 source verification 통과
- compliance 0 findings는 현재 스캔 결과일 뿐 다른 LLM 부재의 절대적 증명이 아니며,
  실제 HCX key·Docker·public TLS·immutable release gate 전까지 산출물은 `DRAFT`

## 읽는 순서

Codex에서 바로 이어 개발하려면 먼저 `CODEX_USAGE_GUIDE.md`의 압축 해제·첫 프롬프트·
검증·설명회 반영·freeze 순서를 따릅니다.

1. `README.md`
2. `CODEX_USAGE_GUIDE.md`
3. `CODEX_MASTER_PROMPT.md`
4. `docs/04_DATASET_REPORT.md`
5. `docs/06_TEST_REPORT.md`
6. `docs/11_IMPLEMENTATION_HANDOFF.md`
7. `MASTER_BRIEFING.md`
8. `VALIDATION_REPORT.md`
9. `docs/01_PDF_FULL_TRANSCRIPTION.md`
10. `docs/01A_TEAM_EMAIL_TRANSCRIPTION.md`
11. `docs/00_OFFICIAL_WEB_SNAPSHOT.md`
12. `docs/02_REQUIREMENTS_BASELINE.md`
13. `docs/03_DATA_AUDIT_AND_SEMANTIC_MODEL.md`
14. `docs/04_PRODUCT_ARCHITECTURE_SPEC.md`
15. `docs/05_MVP_E2E_EXECUTION_PLAN.md`
16. `docs/06_API_CONTRACT_DRAFT.md`
17. `docs/07_TEST_AND_EVALUATION_PLAN.md`
18. `docs/08_BRIEFING_QUESTIONS_AND_DIFF_PROCESS.md`
19. `docs/09_BRAND_AND_ASSET_POLICY.md`
20. `docs/10_RELEASE_FREEZE_RUNBOOK.md`
21. `artifacts/requirements_traceability.csv`
22. `tests/gold_queries_v0.jsonl`
23. `CODEX_START_PROMPT.md`

개발용 상위 규칙은 루트의 `AGENTS.md`입니다.

## 출처 우선순위

1. 첨부 과제 PDF와 데이터 ZIP
2. 추후 전달될 설명회 녹취·주최 측 팀별 공지
3. 공식 홈페이지·공식 FAQ
4. 팀 내부 설계

문서의 모든 판단은 다음 라벨 중 하나로 구분합니다.

- `OFFICIAL_PDF`: 첨부 PDF에 명시
- `OFFICIAL_DATA`: 실제 배포 데이터에서 검증
- `OFFICIAL_TEAM_EMAIL`: 주최 측이 참가팀에 보낸 운영 메일
- `OFFICIAL_WEB`: 공식 홈페이지·FAQ의 보완정보
- `PDF_EXAMPLE`: PDF가 예시라고 명시한 비고정 형식
- `BRIEFING_CONFIRMED`: 설명회에서 추후 확정
- `OPEN_QUESTION`: 아직 확정할 수 없음
- `TEAM_DECISION`: 평가 대응을 위한 내부 설계

원본은 `inputs/official_task.pdf`, `inputs/official_data.zip`에 읽기 전용 사본으로
동봉했습니다. 개발 시작 시 `artifacts/source_manifest.json`의 SHA-256을 먼저
검증해야 합니다.

## 지금 고정할 제품 범위

### 포함

- 네 상품군 전체
- 상품 상세조회
- 복합 조건 검색
- 필터·정렬·Top-N
- 상품 비교
- 집계
- 상품군별 distinct count처럼 안전하게 분리된 교차 질의; 호환되지 않는 교차 metric 순위는 차단
- 조회·검색·필터·순위·비교·집계 결과의 데이터 근거·제한사항 설명
- 정확한 단일 상품 target의 source-backed 설명(원본 상품사실·전략·benchmark)
- target 없는 개방형 금융교육·투자해설은 역질문 또는 안전 제한
- 확인 불가·명확화 응답
- 필드 단위 근거
- 공개 GET API
- Docker 재현 구성과 로컬 실제 HTTP E2E; Docker fresh build/restart 실증은 외부 gate

### 설명회 전 보류

- API 예시를 최종 고정 계약으로 간주하는 것
- `think_trace`의 최종 표현 수준
- 정확한 HCX 모델 ID와 제공 크레딧
- 실제 HCX key E2E, Docker fresh build/restart, public TLS
- 최종 Git SHA·container image digest
- 8월 6일 주최 측 API·평가 contract
- 외부 데이터 추가
- 제3자 임베딩·reranker 선택
- 화면 UI
- 개인화·포트폴리오 기능
- 실시간 시세 연동

## 가장 중요한 개발 원칙

HyperCLOVA X는 자연어 의도와 조건을 해석합니다. 실제 상품 선택, 필터, 순위,
수치 계산은 검증된 SQL·코드가 수행합니다. 답변의 상품명과 숫자는 실행 결과와
근거에 존재하는 값만 사용할 수 있습니다.

권장 핵심 흐름:

```text
GET API
-> 입력 검증
-> HCX typed QueryPlan
-> 계획 검증
-> DuckDB/SQL 실행
-> Evidence Bundle
-> 답변 작성
-> 수치·근거·금지표현 검증
-> JSON 응답
```

## 설명회 이후 절차

설명회 녹취를 그대로 추가한 뒤 다음 순서만 수행합니다.

1. 발화자·시간순 전사 교정
2. 요구사항 문장 추출
3. 현재 기준선과 항목별 diff
4. `OPEN_QUESTION`을 `BRIEFING_CONFIRMED` 또는 `TEAM_DECISION`으로 변경
5. API·모델·배포 계약 확정
6. 이미 구현된 MVP를 다시 만들지 않고, 확정된 변경만 contract·registry·test에 반영

이 과정을 거치면 문서를 처음부터 다시 쓰지 않고, 설명회에서 바뀐 조건만 정확히
반영할 수 있습니다.
