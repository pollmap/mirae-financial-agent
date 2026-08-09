# 공식 적합성·재질문·적대적 검증 최신 기준

작성일: 2026-08-09
적용 브랜치: `codex/federated-completion-v3`
상태: **로컬 구현·검증 진행 중 / 외부 자격증명·공개 배포는 미검증**

이 문서는 오래된 설계 이력과 현재의 제출 판단을 분리한다. 새 에이전트는 이 문서,
`docs/16_MASTER_PROJECT_NARRATIVE.md`, `docs/15_REBASELINE_VALIDATION_REPORT.md`,
`artifacts/requirements_traceability.csv` 순서로 읽고, 그 다음에 원본 자료를 확인한다.
이 문서의 사실은 아래 출처 표기를 벗어나지 않는다.

## 1. 출처의 권위와 해석 범위

| 등급 | 출처 | 이 프로젝트에서의 효력 |
|---|---|---|
| `OFFICIAL_PDF` | `inputs/`의 주최 측 과제 소개 PDF p.4–7 | 평가·제출의 확정 기준 |
| `OFFICIAL_WEB` | AI Festival 공지사항 Q&A | PDF 이후의 공식 공지. 확정된 항목만 반영 |
| `BRIEFING_GUIDANCE` | 제공된 `미래에셋_AI_페스티벌_설명회_녹취_순서정리.md` | Ontology/Graph/Federated Retrieval/grounding의 기술 방향. 녹취는 실제 과제 설명 직전 종료되어 계약·API 수치의 완전한 출처가 아님 |
| `TEAM_DECISION` | 마스터플랜 및 이 저장소의 gate | 팀이 높게 잡은 품질/운영 기준. 절대로 “주최 측 확정”이라고 쓰지 않음 |

### 원본에서 직접 재확인한 확정 사항

- 네 상품 스코프는 국내채권, 국내 ETF, 해외 ETF, 공모펀드이며 기준 스냅샷은
  2026-07-11이다. 각 마스터 스키마가 다르므로 단일 자유 텍스트 검색만으로는 충분하지
  않다. (`OFFICIAL_PDF` p.4–5)
- 자연어 조건검색·상세조회·비교·정렬/순위/집계·상품군 교차질의와 근거 표시가 필요하다.
  데이터로 확인할 수 없으면 확인 불가를 밝히거나 필요한 조건을 역질문해야 한다.
  데이터 없는 수익률 전망 및 단정적 투자 추천은 금지다. (`OFFICIAL_PDF` p.4–5)
- 제출·평가 runtime LLM은 HyperCLOVA X만 허용된다. 다른 언어모델을 runtime에 추가하지
  않는다. 임베딩 등 비언어모델의 선택은 공식 FAQ상 제한되지 않지만, 실제 endpoint/model은
  별도 live 검증 전까지 미확정이다. (`OFFICIAL_PDF` p.4, `OFFICIAL_WEB` FAQ)
- 주최 측 제공 데이터가 평가 기준이며 외부 데이터와 상충하면 주최 측 데이터를 우선한다.
  이 구현은 평가 근거를 네 공식 스코프와 그 행/해시로 제한한다. (`OFFICIAL_PDF` p.5)
- 평가 API 예시는 `GET /answer?question_id=...&question=...`와 다섯 문자열 필드
  `question_id`, `question`, `retrieved_context`, `think_trace`, `answer`이다. 이는 예시
  스키마이므로 호환 profile로 유지하되, 최종 계약 변경 공지가 오면 adapter만 바꾼다.
  (`OFFICIAL_PDF` p.7)
- private GitHub Organization, 재현 가능한 코드/Docker/README, 기술제안서, endpoint URL과
  JSON 명세가 예선 제출물이다. public network API와 평가기간 가동이 요구된다. PDF에는
  API 가동 09.07–09.20(변경 시 공지), 일정 페이지에는 평가 09.07–09.30으로도 적혀 있어
  **더 최신의 공식 공지로 확인할 때까지 09.07–09.30 전체를 운영 대상으로 둔다**.
  마감 뒤 commit/push/deploy 변경은 실격 사유다. (`OFFICIAL_PDF` p.3, p.7)

### 문항 수에 대한 정정

**공식 평가 문항 수·동시성·timeout은 현재 공개 확정값이 아니다.** 공개 FAQ도 평가
데이터셋은 비공개이며 참고 질의 set를 별도 공지한다고만 한다. 공개 Q&A에 보이는
“총 질의 수·동시성·timeout”은 참가자의 질문이지 주최 측 답변이 아니다. 따라서
`20문항`은 공식 시험 수가 아니라 아래의 `TEAM_DECISION` planner parity smoke이고,
`100문항`은 빠른 live E2E smoke, `1,000 direct + 200 multi-turn`은 강화 release gate다.
어느 것도 주최 측의 공식 평가 문항 수가 아니므로 README·제안서·발표에서 공식 수치처럼
말하면 안 된다.

## 2. 현재 구현이 공식 요구를 만족시키는 방식

| 공식 요구 | 실제 구현 | 주장 가능한 상태 |
|---|---|---|
| 조건/조회/비교/집계/교차질의 | typed `QueryPlan` → allow-list/registry → DuckDB SQL, cross-scope는 스코프별 계획을 유지 | 로컬 검증. 실제 HCX 경로는 key 대기 |
| 근거 기반 답변 | 공식 source file/sheet/Excel row/row hash를 EvidenceBundle에 보존, SQL 결과를 최종 권위로 사용 | 로컬 검증 |
| 정보부족 시 역질문 | `NEEDS_CLARIFICATION`과 `missing_slots`, 선택지, 서명된 follow-up token. UI에서도 선택지를 다시 전송 | 로컬 API 검증 |
| 환각/전망/단정 추천 방지 | 서버 safety gate가 prompt/SQL 유출, 미래 가격·수익 예측, 개인 맞춤 매수 권유, 실시간 가장, 결측값=0 변환을 실행 전에 차단 | 로컬 API·단위 검증 |
| HCX-only | production은 `PLANNER_MODE=hcx`, `PLANNER_STAGE=two`, HCX key 없이는 시작 실패. 다른 LLM fallback 없음 | mock/정적 검증; real HCX 미실행 |
| Graph/keyword/vector 활용 | Exact/Alias·SQL·Graph·BM25를 RetrievalPlan으로 라우팅하고 SQL 교차검증. Vector는 1,024차원 cache+key가 있을 때만 선택적으로 사용 | Graph/BM25 로컬 검증; Vector fixture만, live cache 미생성 |
| 공개 API/제출 | GET `/answer`, healthcheck, Docker/compose, production preflight, TLS reverse-proxy 예시 | 로컬 Docker 검증; public URL/TLS/NCP 미실행 |

### 재질문은 실제로 되는가?

된다. 예를 들어 “수익률 높은 ETF를 알려줘”처럼 시장 또는 기간이 결과를 바꾸는 질의는
거절하지 않고, 필요한 시장/기간/기준을 짧은 선택지로 되묻는다. 사용자가 선택하면
서명된 상태에 보존된 원래 질의에만 그 조건을 합쳐 실행한다. token 변조·누락·한쪽
parameter만 제출은 `400 INVALID_CLARIFICATION`으로 차단한다. 불명확하지만 데이터로
좁힐 수 있는 질의에만 이 흐름을 쓰며, 미래 전망·임의 SQL·프롬프트 유출처럼 답하면
안 되는 요청을 “추가 질문”으로 우회시키지는 않는다.

공개 화면에는 결과/제한/근거만 표시한다. 내부 field ID, source row hash, raw
`retrieved_context`, `think_trace`, system prompt는 화면에 노출하지 않는다. 다섯 필드
호환 API 자체는 유지한다.

## 3. 테스트의 의미와 확대된 안전 회귀

어떤 유한한 테스트 집합도 “모든 자연어 경우의 수”를 증명하지 못한다. 따라서 숫자만
늘리는 대신 아래처럼 서로 다른 실패면을 분리해 검증한다.

| 층 | 대상 | 현재 기준 |
|---|---|---|
| 결정론 정확성 | 네 스코프의 lookup/filter/rank/compare/aggregate/cross | 독립 SQL oracle 640문항 + paraphrase metamorphic 137그룹 |
| retrieval | Graph 관계/Graph+SQL, lexical BM25, Vector/RRF fixture, fallback | holdout 100, Graph 120, BM25 20, Vector fixture. 각 범위는 실제 live 사용률을 뜻하지 않음 |
| API/재질문 | 5-field schema, bounded options, signed follow-up, 변조/누락 token, 2·3회 후속 state | ASGI contract 회귀 + local extensive gate 200/200 |
| 적대적 안전 | 한/영 프롬프트·개발자 메시지·hidden prompt·raw SQL·DB 변경, 미래예측/매수권유, realtime 가장, 결측=0, HTML/NUL/공백/과대입력 | HTTP 대표 매트릭스와 45개 정규화 변형 unit matrix. 모든 결과는 통제된 answerability 또는 400이며 5xx가 아님 |
| 실운영 부하 | local Docker warm 100 요청, 동시 10, restart/health | 로컬만 검증. 외부 망은 별도 gate |
| 실제 HCX | 20 planner one-vs-two parity, 100 two-stage smoke, **1,000 direct SQL-oracle + 100 two-follow-up + 100 three-follow-up** public API gate | 스크립트 완비, key 주입 전에는 **미실행** |

### 2026-08-09 재실행 증빙

- source manifest의 PDF/ZIP/email SHA-256과 XLSX 8개를 다시 확인하고 ETL·KG·lexical
  index를 새로 생성했다. 결과는 raw 145,393행, serving 60,903, KG
  71,683 node/206,274 edge/249,874 alias, lexical 80,670 document/1,288,698
  posting/43,935 vocabulary, vector cache 0이다.
- 전체 `pytest`는 **275/275**, Ruff는 `app deploy etl scripts tests eval` 전체 통과,
  runtime compliance는 **93 files / 0 findings**이다. 독립 oracle 640/640,
  metamorphic 137/137, holdout 100/100, Graph 120/120, BM25 20/20, A–E PASS를
  같은 재생성 DB에서 다시 실행했다.
- 새 로컬 HTTP server의 `/health/ready`, 15-case smoke, 다섯 필드 schema, 한국어
  주입/원시 SQL/영문 미래예측 차단, 모호 ETF 질의의 `NEEDS_CLARIFICATION`을 실제로
  확인했다.
- `deploy/live_hcx_extensive_e2e_gate.py --local-verify`는 credential 없이도 direct
  **1,000/1,000**(독립 SQL oracle·근거·5-field contract)과 2회/3회 후속 **200/200**
  (signed state·원천 근거)을 재실행했다. 이는 real HCX 결과가 아니라 live corpus가
  현재 data/engine에서 성립함을 확인한 local baseline이다.
- **fresh Docker build는 미통과**: Docker Desktop builder가 두 번 연속
  `auth.docker.io` DNS lookup 실패로 base image metadata를 받지 못했다. host DNS는
  정상 해석했지만 builder 내부 DNS 문제이므로, 네트워크가 복구된 뒤 `docker build
  --no-cache --pull=false ...`와 run/restart smoke를 다시 해야 한다. 기존 image의
  과거 성공을 새 코드 검증으로 재사용하지 않는다.

`deploy/live_hcx_plan_smoke.py --confirm-live-calls 40`은 20개의 안전한 고정 질문을
one/two로 각 1회 호출하는 parity smoke다. `deploy/live_hcx_e2e_gate.py
--confirm-live-calls 100`은 rank 35, filter 25, aggregate 20, cross-scope 20을 실제
two-stage HCX로 실행한다. 후자는 정확도 98% 이상, 100/100 HCX 사용·근거 연결,
교차질의 거부 0을 요구한다. 두 보고서 모두 질문·prompt·plan·answer·상품 UID·secret을
저장하지 않는다. 추가로 `deploy/live_hcx_extensive_e2e_gate.py
--confirm-direct-hcx-calls 1000 --confirm-api-requests 1700`은 500개의 독립 의미
명세를 두 표현으로 확장한 1,000 direct 질의를 독립 SQL oracle로 채점하고, 100개의
2회 후속 및 100개의 3회 후속 재질문을 실제 `/answer` API와 signed state로 끝까지
실행한다. direct는 98% 이상 및 1,000/1,000 HCX·근거·5-field contract를, 재질문은
200/200 최종 HCX·근거·결정론 baseline evidence-signature 일치를 요구한다. 세 보고서는
질문·prompt·plan·answer·token·상품 UID·secret을 저장하지 않으며 production preflight는
셋 중 하나라도 없으면 실패한다.

## 4. 의도적으로 하지 않은 것

- HCX 뒤에 자유 형식 “composer” LLM을 두지 않는다. 답변은 검증된 SQL EvidenceBundle에서
  결정론적으로 렌더링한다. 이는 모델 사용을 줄이려는 편의가 아니라 데이터 밖의 금융
  주장을 막기 위한 안전 설계다. 여기서 F는 “live HCX two-stage E2E”이지 “free-form
  composer”가 아니다.
- Graph가 멋있어 보인다는 이유만으로 GraphDB product나 고정 2-hop을 강제하지 않는다.
  현재 공식 마스터에서 검증 가능한 manager/issuer/region/asset/risk/benchmark 1-hop
  관계는 runtime에서 SQL과 교차검증한다. 다중 홉이 실제 평가 데이터로 필요해지면
  별도 gold case와 SQL authority 검증을 먼저 추가한다.
- 외부 비공식 데이터, 실시간 가격, 개인 적합성 판단, 수익 보장/매수 지시는 MVP와
  공식 평가근거에 넣지 않는다.

## 5. release 판단

다음은 코드로 해결할 수 없거나 아직 실행하지 않은 **외부 gate**다. 하나라도 비어 있으면
“제출/운영 완료”라고 보고하지 않는다.

1. 팀이 발급한 HCX key/model/endpoint로 20 parity, 100 E2E, 1,000 direct + 200 multi-turn
   E2E report를 실제 생성한다. 마지막 gate는 명시적으로 1,000 direct HCX 질의와 총 1,700
   API 요청을 승인해야 실행된다.
2. CLOVA Embedding credential과 최종 endpoint/model을 확인한 뒤 1,024차원 cache를 생성하고
   Vector live smoke를 실행한다. cache가 없으면 Vector만 꺼지고 Exact+SQL+Graph+BM25는 계속 쓴다.
3. NCP 계정/credit, VPC·방화벽, public domain/TLS를 구성하고 외부 `/health/*`와 `/answer`
   smoke를 실행한다.
4. 주최 측의 최종 API timeout/concurrency/운영기간/허용 HCX model 공지를 다시 확인한다.
5. private Organization repository에 제출하고, final image digest/release manifest를 만든 뒤
   사람이 freeze를 승인한다.

## 6. 문서 유지 규칙

- `docs/16`과 `HANDOFF_CURRENT_STATUS.md`의 “기본 one”, “Graph 미연결”, “F composer” 같은
  과거 설명은 역사 기록이다. 현재 상태는 이 문서와 traceability 행의 상태 표기를 따른다.
- `OFFICIAL_*`, `BRIEFING_GUIDANCE`, `TEAM_DECISION`을 같은 문장에 섞지 않는다.
- 테스트 개수/성능은 실행한 report와 commit SHA가 함께 있을 때만 갱신한다. 계획 수치나
  fixture 수를 실서비스 정확도로 바꾸어 쓰지 않는다.
