# Pre-briefing ExecPlan

상태: **DRAFT only — 최초 사전계획을 보존한 기록.** 현재 구현·검증 상태는
`../docs/11_IMPLEMENTATION_HANDOFF.md` 및 `../docs/06_TEST_REPORT.md`가 우선

## Objective

2026-08-06 설명회 전에 공식 요구사항, 실제 데이터 구조, 교체 가능한 계약, MVP E2E
설계와 실행 가능한 로컬 MVP를 확보하고, 설명회 녹취가 들어오면 확정 조건의 diff만
기존 구현에 반영할 수 있게 합니다.

## Progress

- [x] PDF hash·8페이지 전사·시각검수
- [x] 6페이지 평가 이미지 문구 복구
- [x] 7페이지 API 운영 이미지 문구 복구
- [x] ZIP hash·8 XLSX 구성 확인
- [x] 145,393행·207 field profile
- [x] key·중복·결측·0·ETN 혼입 확인
- [x] 펀드 item·attribute 반복구조·손상행 확인
- [x] 요구사항 baseline
- [x] architecture·QueryPlan·API draft
- [x] MVP·test·briefing plan
- [x] 40 gold·10 policy fixture
- [x] Evidence·request·response·release schemas and provisional OpenAPI
- [x] traceability matrix·diagrams·freeze runbook·brand policy
- [x] immutable input copies and source hash gate
- [x] 4종 full ETL·DuckDB·결정론적 executor·evidence·GET API 구현
- [x] v1 registry 59개와 serving metric evidence 1,156,332행 재빌드
- [x] source XLSX 8/8·fast 153/153(14.90초)·full 158/158(104.57초)
- [x] gold/policy 50/50(40 plan subset·103 assertion)·runtime scan 28 files/0 findings
- [x] real HTTP E2E 15/15·load 100/100(concurrency 10, failure 0, p95 131.75ms)
- [x] serving DB SHA-256 `4daab85638b6d6fa1c0f1ebd4070d4050dca57fbfd9aed7e39a8aef2399a1450`
- [x] 국내 ETF 1Y source-present 986·quality-valid 951·2026-06-15 common-latest rankable 940
- [x] fund full 11,138/API 공모 기본 11,115; public risk 8,564·missing 2,551;
  판매중 8,445·판매완료 2,670
- [x] HCX adapter mock·역질문 후속·runtime compliance 검증
- [x] scope별 분리 교차 count·registry-backed 수익률 기간·bounded catalog filter
- [x] 정확한 상품 target의 source-backed explain·다중 metric NULL 보존·전 metric/limitation 렌더링
- [x] `sum/avg/min/max` 통합 검증·hardened manifest/report/DB/Git/image-ref runbook
- [x] multi-stage Docker runtime에서 원본 source·ETL 제외 및 최소 `requirements-runtime.txt` 사용
- [ ] 설명회 녹취 수령
- [ ] briefing diff와 final contract freeze
- [ ] 실제 HCX credential E2E
- [ ] Docker fresh build/run/restart·immutable image digest
- [ ] public TLS/domain·실제 Git SHA
- [ ] 8월 6일 주최 측 최종 API contract·허용 HCX model 확인
- [ ] 개방형 금융교육 범위는 보류; 최종 release manifest 확정

## Decisions

- 단일 오케스트레이터
- FastAPI + DuckDB + Parquet
- HCX typed planner + deterministic executor
- 공통 catalog + 상품군별 detail + Metric Registry
- evidence-first answer
- Codex는 개발 전용이며 제출 runtime의 언어모델은 HyperCLOVA X만 사용
- external data와 UI는 MVP 후순위

## Open questions

- API exact contract
- `retrieved_context`, `think_trace`
- timeout·concurrency·retry·status
- 정확한 HCX model·credit·rate limit
- API 09.20/09.30 운영 종료
- post-deadline restart·failover
- ETF master의 ETN 평가 범위
- fund family와 metric unit·zero semantics

## Next milestone

설명회 녹취를 받아 `../docs/08_BRIEFING_QUESTIONS_AND_DIFF_PROCESS.md` 절차로 요구사항을
갱신합니다. 이미 구현된 walking slice·full ETL·DuckDB·API를 다시 만들지 않고,
공식 확정 model/API/운영 조건을 config·contract·registry·test에 반영한 뒤 Docker fresh
build/restart, live HCX, public TLS, final manifest 순서로 남은 release gate를 닫습니다.
