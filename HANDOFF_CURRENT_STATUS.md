# 인계 문서 — 다른 AI 에이전트/세션이 이어받을 때 가장 먼저 읽을 파일

> **main 통합 이후 첫 읽기**: `docs/20_MAINLINE_HANDOFF_AND_REPOSITORY_MAP.md`를 먼저 읽는다. 이 문서는 대회 엔진과 팀 인간검증 챗봇의 현재 구조·공식 원본 보존·재현 명령·외부 경계를 한 번에 연결한다. 아래의 오래된 수치와 서술은 해당 시점의 `HISTORICAL` 기록일 수 있으므로, 현 상태 판단에는 docs/20 → docs/19 → docs/18 순서를 우선한다.

## 2026-08-09 main 통합 기준: 팀 인간검증 챗봇 포함

`codex/human-qa-chatbot-v1`의 대회 엔진·QA 챗봇 변경을 검증한 뒤 `main`에 통합한다.
대회 엔진의 `GET /answer`와 다섯 문자열 계약은 그대로 두고, 같은 저장소에 초대 기반
QA Gateway, 시간순 채팅, 다중대화 상태, 조건·근거·검색경로 검사, 피드백·내보내기·즉시
삭제, 로컬/LAN 전용 배포 패키지를 추가했다. 이 상태를 이해하려면
`docs/20_MAINLINE_HANDOFF_AND_REPOSITORY_MAP.md` → `docs/19_HCX_HUMAN_QA_CHATBOT.md` →
`docs/18_FINAL_MASTER_PLAN_AND_RELEASE_READINESS.md` 순서로 읽는다. 실제 HCX 20→100
gate, 실제 Embedding, 다른 LAN 기기/NVDA/Windows 고대비 검수와 5~10명 파일럿은 계속
`PENDING_EXTERNAL`이다. 로컬 fixture preview를 live HCX 완료로 간주하지 않는다.

기준: 2026-08-08, branch **`codex/federated-completion-v3`** (기본 브랜치 아님 —
`main`이나 `briefing-rebaseline-v2`가 아니라 이 브랜치를 체크아웃해야 최신 상태입니다)
저장소: `https://github.com/pollmap/mirae-financial-agent` (**private**)

이 문서는 이 저장소를 처음 보는 AI 에이전트가 별도 대화 맥락 없이도 "지금 뭐가
되어 있고, 뭐가 안 되어 있고, 다음에 뭘 해야 하는지"를 정확히 파악하도록 쓴
단일 진입점입니다. **"왜 지금 이 모습인지"(설계 결정의 이유, 사용자의 협상
불가 원칙, 기각된 대안)는 이 파일이 아니라
`docs/16_MASTER_PROJECT_NARRATIVE.md`가 담당합니다 — 다른 코드 에이전트
계정이 처음 이어받는다면 **`docs/18_FINAL_MASTER_PLAN_AND_RELEASE_READINESS.md`
부터**, `docs/17`, 이 파일과 16을 차례로 읽으세요.** 내용이 다른 문서와 상충하면
`docs/18` → `docs/17` → 이 파일 → `docs/16_MASTER_PROJECT_NARRATIVE.md` →
`docs/15_REBASELINE_VALIDATION_REPORT.md` → `docs/14_BRIEFING_REBASELINE_PLAN.md`
순으로 최신입니다(`docs/17`은 공식 기준/적대적 검증, 이 파일은 실시간 상태,
16은 서사). `00_START_HERE.md`, `docs/11_IMPLEMENTATION_HANDOFF.md`,
`artifacts/windows_docker_verification_20260803.md`는 8/3-8/4 시점(설명회 이전
"prebrief" 상태, 태그 `prebrief-v1`)에서 멈춘 `HISTORICAL` 배경 문서입니다.

## 2026-08-09 v4 최종 전수감사 — 이 절이 현재 상태

- 런타임 소스 기준 커밋은 `c7c07c9`다. 전체 조건을 `ConditionLedger`로 기록하고
  `grounded`, `clarification_required`, `unavailable`, `not_comparable` 중 하나로
  판정한다. 중요한 조건의 무언 누락이 있으면 `FULL`을 금지한다.
- 2단계 HCX 플래너가 기본이고 1단계는 수동 롤백 전용이다. Exact/Alias·SQL·Graph
  실제 1–2 hop·BM25가 조건별로 실행되며, Vector/RRF는 정확히 1,024차원 cache와
  embedder가 있을 때만 활성화된다. 현재 Vector는 `VERIFIED_FIXTURE`, live cache는 0이다.
- 모호한 질문은 가장 판별력 높은 조건 하나를 묻고 signed state에 2·3·4턴 답변과
  사용자 정정을 누적한다. “좋은 상품”은 범위를, 범위가 있는 “좋은 해외 ETF”는 평가
  기준을 먼저 묻는다. 교차질의는 부족 조건을 묻되 거부하지 않는다.
- 재실행 결과: pytest **288/288**, Ruff PASS, compliance **102 files/0**, 기존 oracle
  **640/640**·교차거부 0, metamorphic **137/137**, 기존 holdout **100/100**, Graph
  **120/120**, BM25 **20/20**, v4 **200/200**, offline assurance **5,000/5,000**이다.
- 실제 서비스 경로를 쓰는 무키 local extensive gate는 독립 direct **1,200/1,200**,
  근거·정책 연결 **1,200/1,200**, 2·3·4턴 flow **300/300**, API 요청 **900/900**이다.
  이는 deterministic baseline이며 실제 HCX 결과가 아니다.
- 최신 프로세스 HTTP는 15/15, 100요청·동시10·오류0·p95 **112.45ms**다. 기존 같은
  로컬 기준 115.89ms보다 2.97% 낮다.
- fresh Docker `--no-cache --pull` build, read-only start, health, smoke 15/15, restart,
  동일 답·상품근거, 재-smoke 15/15를 통과했다. Docker 100×10은 오류 0·p95 447.99ms다.
  local digest `sha256:f17c04…9459a4`는 registry 제출 digest가 아니다.
- 공식 평가 문항 수는 공개되지 않았다. 20·100·200·300·640·1,200·1,500·2,100·
  5,000은 모두 내부 gate다. `HCX-007`은 팀 기본값일 뿐 주최 측 확정 모델이 아니다.
- 남은 것은 실제 HCX 20→100→1,200+300, CLOVA Embedding 1,024차원 cache/live smoke,
  NCP public HTTPS, 사람의 제출·freeze 승인이다. 이 전에는 상태가 `PENDING_EXTERNAL`이고
  “완벽/실서비스/제출 완료”라고 쓰지 않는다.

아래의 275 테스트, 93-file compliance, 1,000 direct/200 flow, 과거 Docker digest와
“Graph 미연결/기본 one” 서술은 모두 `HISTORICAL`이다.

## HISTORICAL — 2026-08-09 v3 재검증 부록

이 절의 수치와 Docker 대기 상태는 v4 전수감사 이전 기록이다. 현재 판정에는 위 v4 절과
`docs/18_FINAL_MASTER_PLAN_AND_RELEASE_READINESS.md`를 사용한다.

- 공식 PDF·FAQ·녹취의 권위를 재분리한 최신 기준은 `docs/17`이다. PDF가 요구한
  정보부족 역질문은 실제 API/화면에 있고, 주입·임의 SQL·미래예측은 안전 차단한다.
- source/ETL/KG/lexical을 새로 만들고 전체 pytest **275/275**, Ruff PASS,
  compliance **93 files/0**, eval **640/640**, metamorphic **137/137**, holdout
  **100/100**, Graph **120/120**, BM25 **20/20**, A–E PASS를 재실행했다.
- 20은 주최 측 평가 문항 수가 아니라 one/two planner parity smoke다. 100은 빠른 HCX
  E2E smoke이며, 새 강화 gate는 1,000 direct HCX 질의와 100개 2회·100개 3회 재질문
  (총 API 요청 1,700)을 요구한다. production은 세 sanitized PASS report를 모두 요구한다.
  모두 key 대기이며 실제 HCX 정확도/운영 완료라고 주장하면 안 된다.
- fresh Docker build는 Docker Desktop builder의 `auth.docker.io` DNS 실패로 두 번
  중단됐다. 기존 과거 image 성공을 재사용하지 않았으며, DNS 복구 뒤 fresh build/run/
  restart smoke가 남아 있다.

## HISTORICAL — v3 완료 상태

- **기본 플래너**: `PLANNER_STAGE=two`. HCX는 의도·스코프·개념·조건·정렬·
  집계만 만들고 서버 grounder가 허용 필드에 결속한다. `count/sum/avg/min/max`,
  `group_by`, 교차 스코프를 지원한다. `one`은 수동 롤백 전용이며 자동 폴백은 없다.
- **실행 중 Federated Retrieval**: 코드/정확명 Exact/Alias, SQL, 운용사·발행사·
  지역·자산유형·위험등급·벤치마크 Graph, 전략·벤치마크·퍼지명 BM25, 선택적
  Vector가 실제 `DuckDBEngine.execute()`에서 라우팅된다. Graph 후보는 동일 SQL
  조건과 일치할 때만 제약으로 쓰고 불일치·KG 부재 시 SQL을 유지하며 이유를 trace한다.
- **Vector 경계**: query embedder와 cache가 모두 있을 때만 활성화한다. 정확히
  1,024차원만 허용하고 zero-padding은 없다. 현재는 결정적 fixture/RRF까지 검증,
  실 CLOVA Embedding credential·cache는 외부 gate다.
- **근거/공개 계약**: 내부 `RetrievalTrace`는 채널·사유·후보수·fallback·검증·
  row/data hash 참조·지연만 담는다. 공개 응답은 `question_id`, `question`,
  `retrieved_context`, `think_trace`, `answer` 5개 필드를 그대로 유지하며 원문
  prompt나 비공개 추론은 내보내지 않는다.
- **최신 실측**: source 8/8, 재빌드 60,903 serving·KG 71,683/206,274/249,874·
  lexical 80,670/1,288,698/43,935, Vector 0. 전체 eval 640/640, 교차 거절 0%,
  metamorphic 137/137, 고정 holdout 100/100, Graph 전용 120/120, BM25 20/20,
  A~E 절제실험 PASS, pytest 275/275, Ruff PASS, compliance 93/0. 추가로 local extensive
  gate의 1,000 direct SQL-oracle·200 signed multi-turn flow도 1,000/1,000·200/200이다.
  실제 HCX F와 extensive gate만 credential 대기다.
- **부하/Docker**: 로컬 warm 100요청·동시10에서 실패 0, p95 115.89ms
  (이전 131.75ms 대비 개선). ETL 직후 cold 657.22ms와 Windows Docker p95
  473.98ms도 별도 기록해 숨기지 않는다. 최종 HEAD fresh `--no-cache --pull=false`
  image `sha256:bdce35e5...68445`, smoke 15/15, restart 후 healthy를 확인했다.
- **운영 release gate**: `deploy/live_hcx_plan_smoke.py --confirm-live-calls 40`이
  20문항을 1단계/2단계로 각각 호출하고, 100-case E2E와
  `deploy/live_hcx_extensive_e2e_gate.py --confirm-direct-hcx-calls 1000
  --confirm-api-requests 1700`은 질문·plan·answer·token·key를 저장하지 않는 추가
  보고서를 만든다. 마지막 gate는 1,000 direct를 SQL oracle로 채점하고 200개 다단계
  재질문을 실제 API contract까지 검증한다. `scripts/production_preflight.py`는 세 PASS
  보고서와 `PLANNER_STAGE=two`가 없으면 운영 배포를 거부한다.

아래 §2-3의 “Graph traversal 미연결”, “기본 one” 같은 문장은 v3 이전 이력이다.

## 0. 한 줄 요약

미래에셋증권 AI Festival 예선용 "금융상품 Agent". 자연어 질문을 HyperCLOVA X가
typed QueryPlan(또는 2단계 모드에서는 개념 plan → 서버 grounding)으로 바꾸고,
결정론적 DuckDB 엔진 + Knowledge Graph + BM25 lexical retrieval이 검색·필터·
비교·집계·교차상품군 비교를 수행하며, 원본 엑셀 행까지 추적되는 근거와 안전한
한국어 답변을 GET API로 반환한다. **8/6 설명회 브리핑과 외부 감사를 반영해
전면 재설계(federated semantic rebaseline)를 완료했고, 이어서 3-agent 적대적
코드 리뷰로 실제 크래시/무공시 오답 버그 2건과 eval 하네스 자체의 채점 결함을
찾아 전부 고쳤다(§2-5). 640문항 자체 평가에서 100% 정답·교차상품군 거절률
0%를 실측했다 — 이번엔 채점 로직도 같이 검증된 상태다. 그 뒤 한 번 더
"남은 것·미흡한 것" 종합 점검을 거쳐 Docker fresh build/restart를 실제로
검증했고(§2-6), 제출 기술제안서가 낡은/거짓 아키텍처 서술을 담고 있던 것과
requirements traceability의 Federated Retrieval 과장 등을 찾아 고쳤다(§2-7).
그 라운드가 낮은 우선순위로 남긴 두 항목(KG party 병합의 scope 누락,
`etl/kg.py` 단위테스트 부재)도 마저 처리했고, 그 과정에서 실 데이터 안의
진짜 병합 버그(스코프간 이름충돌 12건)를 발견·수정했다(§2-8). **남은 것은
이제 전부 코드로 해결할 수 없는 외부 gate뿐이다** — 진짜 HCX API 키·설명회
확정사항·배포 인프라가 있어야만 되는 항목들.

## 1. 왜 재설계가 있었는가 (8/3 → 8/8 사이 일어난 일)

8/3 "prebrief" 버전(`prebrief-v1` 태그)은 핵심 기능이 다 됐지만 교차 상품군
질의를 대부분 `INCOMPARABLE`로 거절했고, 설명회가 강조한 Ontology/Knowledge
Graph/Federated Retrieval/2단계 플래닝이 없었다. 8/6 설명회 요지와 외부 AI
감사가 이 갭을 지적했고, 사용자가 "교차 상품군 무제한(절대 거절 금지)"과
"설명회 기술스펙 전면 채택"을 명시적으로 지시했다. 그 결과가 이 브랜치다.
승인된 설계는 `docs/14_BRIEFING_REBASELINE_PLAN.md`, 실측 검증은
`docs/15_REBASELINE_VALIDATION_REPORT.md`에 있다.

## 2. 지금 100% 확실하게 되어 있는 것

### 2-1. 데이터·핵심 엔진 (8/3부터 유지)
- 원본 PDF/ZIP 무수정 보존, 145,393행/207필드 전수 ETL → DuckDB serving DB
- 4개 상품군: 상세조회·검색·필터·정렬·Top-N·비교·집계, 필드 단위 근거,
  다단계 역질문, 안전정책(미래예측/추천/실시간/0-치환 차단)

### 2-2. 교차 상품군 무거절 (이번 재설계의 핵심, W1)
- `registry/semantic/`(concept_catalog·comparability_matrix·value_aliases) +
  `app/semantics/capability.py`가 `registry.py`의 하드 거절 게이트를 대체.
  거절 값 자체가 스키마에 없음 — `UNIFIED_RANK`/`SPLIT_PRESENTATION`/
  `EXPLAIN_ONLY`/절대값 스코프는 대안 안내.
- `app/execution/cross_scope.py`: 단일스코프 서브플랜으로 분해해 기존
  검증된 엔진을 재사용 + 통화 자동판별(해외 USD 100%·국내 KRW 99.94% 실측) +
  의무 공시 블록(단위상태·기준일·0값·sentinel·coverage).
- 실측: eval의 cross_scope 69문항 **정답률 100%, 거절률 0%**.

### 2-3. 설명회 기술스펙 (W2-W3)
- **Ontology**: concept catalog(59개 물리 metric→~40개 스코프중립 개념) +
  grounder.py가 런타임 통제 계층 역할.
- **Knowledge Graph**: `etl/kg.py`가 `kg_node`(71,683)/`kg_edge`(206,274)/
  `kg_alias`(249,874) materialize. ETF→managedBy, ETN→issuedBy 역할 구분,
  entity resolution은 정규화 후 완전일치만(오병합 방지) **+ scope 일치까지
  요구**(2-8, 커밋 `c9efb67`) — 스코프가 다르면 이름이 같아도 병합 안 함.
- **Federated Retrieval**: `app/retrieval/`의 graph_retriever(exact
  alias+traversal) + lexical_retriever(순수 SQL BM25, 확장 불필요) +
  vector_retriever(코드 완성, 캐시 대기) + router.py/fusion.py(RRF).
  Entity 해석은 정확코드→정확KG별칭→LIKE부분일치→lexical fallback 3단
  안전망(겹치는 후보는 항상 기존 역질문 계약으로 흐름, 오답 승격 없음).
- **2단계 플래닝**: `HCX_SEMANTIC_PLAN_SCHEMA`(개념만, 물리 필드명 없음) +
  `app/semantics/grounder.py`(fail-closed). `PLANNER_STAGE=one|two` 플래그
  (기본 `one`). mock HCX 계약테스트로 Stage-1/2 결과 동일성 실증.
  **TPM 예약량 13,013B→5,383B(−58.6%), 처리량 4→11건/분.**

### 2-4. 실측 검증 (재현 명령 포함)
```bash
cd mirae_financial_agent_codex_prebrief
git checkout briefing-rebaseline-v2   # main이 아님!
py -3.12 -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
./.venv/Scripts/python.exe scripts/verify_sources.py            # PASS
./.venv/Scripts/python.exe -m ruff check app deploy etl scripts tests eval  # PASS
./.venv/Scripts/python.exe scripts/scan_runtime_compliance.py   # 44 files/0 findings
./.venv/Scripts/python.exe scripts/build_data.py --no-parquet   # KG+lexical 포함 재빌드 필요
./.venv/Scripts/python.exe -m pytest -q                         # 238 passed
./.venv/Scripts/python.exe -m eval.run_eval                     # 640/640 (100%)
./.venv/Scripts/python.exe -m eval.metamorphic                  # 137/137
```
**Windows 주의**: Git Bash `curl`은 한글 query를 깨뜨림. PowerShell
`curl.exe` 또는 Python `httpx` 사용할 것.

### 2-5. 버그 수정 이력 — W1-W4 개발 중 4건 + 최종 적대적 리뷰에서 추가 발견분
`docs/15_REBASELINE_VALIDATION_REPORT.md` §3(W1-W4 중)·§0(최종 리뷰)에 상세.

**W1-W4 개발 중 eval 하네스가 직접 잡아낸 4건**: (1) 점(.) 포함 티커가 4개
엔티티로 조각화되던 문제, (2) "가장 작은"이 정렬 안 되던 문제, (3) "ETF·ETN"
결합 문구가 ETN만 세던 비대칭 가드, (4) 서로 다른 스코프의 서로 다른 지표를
나란히 요청하면 크래시하던 문제.

**"최종 개발 점검" 지시로 진행한 3-agent 적대적 리뷰(교차상품군/시맨틱
안전성, federated retrieval 코드, 데이터정책+eval 신뢰성 — 각각 실제 코드를
합성·실데이터로 실행해 검증)에서 추가로 찾아 고친 것**:
- **크래시 버그**: SPLIT/EXPLAIN_ONLY 융합 결과 캡이 `max()`로 잘못 계산돼
  4스코프×limit 50=200건이 `EvidenceBundle`(max_length=50)에 그대로
  들어가 `ValidationError`로 API가 죽을 수 있었음.
- **무공시 오답 버그**: 개념이 스코프 1개에만 바인딩된 채(예: 신용등급은
  채권에만 존재) `UNIFIED_RANK`로 시도되면 숫자 파싱이 전부 실패해 "근거
  0건"의 확신에 찬 빈 답변이 나갈 수 있었음 — 이 재설계 전체가 없애려던
  바로 그 실패 유형. `capability.py`(스코프 1개 이하면 SPLIT_PRESENTATION로
  강등) + `cross_scope.py`(융합 결과 0건이면 스코프별 표시로 폴백) 이중 방어.
- 랭크/비교 키워드 충돌(코드 2개 명시 비교가 전체 랭킹으로 답변됨), 그 외
  minor 3건.
- **eval 하네스 자체의 채점 결함**(가장 중요한 발견): `cross_rank` 채점이
  정답 여부를 계산은 해놓고 `passed` 판정에 반영하지 않았고, `behavior`
  채점은 "공시문구만 있으면 통과"로 새어나가는 허점이 있었음 — 즉 크로스
  스코프 랭킹이 틀려도, 심지어 결과가 0건이어도 통과할 수 있었다. 채점을
  고치자 정확도가 100%→95.78%로 떨어졌고, 그 27건을 전부 조사한 결과 앱
  버그가 아니라 오라클이 앱의 기존 정책(ascending sentinel 차단, AUM
  국내+해외는 통화가 달라 SPLIT_PRESENTATION이 맞음) 2가지를 아직 반영
  못 하고 있었던 것으로 확인 — 오라클을 고쳐 정직하게 100%로 재수렴.

전부 eval 하네스(640문항, 독립 SQL oracle)와 3-agent 리뷰가 잡아냈고 근본
원인을 고쳤다 — 하네스나 리뷰를 답에 맞춘 게 아니다. 커밋 `5c85af5`.

### 2-6. Docker 검증 (2026-08-08 재확인 완료, W1-W4 신규 스테이지 포함)
`5c85af5` 직후 Docker Desktop 데몬이 이 PC에서 살아났고, W2-W4 신규 ETL
스테이지(KG·lexical build)를 포함한 fresh `--no-cache` 빌드/실행/재시작
검증을 실제로 완료했다: 컨테이너 내부 ETL이 §2-3과 동일한 카운트를 재현
(71,671 node/206,274 edge/249,857 alias, 80,670 lexical doc — 이 카운트는
`c9efb67`의 scope-aware 병합 수정 이전 값, 최신 값은 2-8 참고), production
기본값(`APP_ENV=production`+`PLANNER_MODE=hcx`)은 실 키 없이 fail-closed로
즉시 종료(의도된 동작), deterministic 모드로 재실행 시 smoke 15/15가
`docker restart` 전후 동일. 상세: `docs/15` §0, 커밋 `e577107`.

### 2-7. 최종 종합 점검 (2026-08-08, "다 체크해서 개발해" 지시, 커밋 `e577107`)
`5c85af5` 이후 사용자가 한 번 더 "남은 것·미흡한 것·개선할 것"을 전부
점검하라고 지시. Docker 재검증(2-6)에 더해 4개 병렬 에이전트로 이전
리뷰가 안 다룬 영역을 감사:

- **제출 문서 정확성(가장 중요)**: `docs/12_TECHNICAL_PROPOSAL_DRAFT.md`(실제
  기술제안서)가 재설계 이전 아키텍처를 그대로 서술하고 있었고, 심지어
  "통화·기간·단위가 필요한 교차 rank·compare는 기존 fail-closed 정책을 유지"라고
  명시 — 이 프로젝트의 핵심 지시(교차 질의 무조건 답변)와 정반대. README·
  docs/04·아키텍처 다이어그램 2종도 같은 계열의 낡은/거짓 서술. 전부 수정.
- **`artifacts/requirements_traceability.csv`의 SEM-002/SEM-003 과장**: Knowledge
  Graph의 `WITH RECURSIVE` 순회 함수와 party 조회 함수, Federated Retrieval의
  `router.route_theme_query`가 실 요청 경로는 물론 **테스트에서도 호출자 0건**임을
  grep으로 직접 재확인(이전 리뷰보다 더 엄격하게 확인됨) — CSV 문구를 실제
  live/scaffold 구분이 드러나게 수정.
- **API 보안/견고성**: 라이브 요청 경로는 대체로 깨끗함(SQL은 전부
  parameterized, limit/top_n은 Pydantic 모델 레벨에서 이미 강제, 클라이언트로
  내부정보 유출 없음). 다만 uvicorn에 연결 동시성 상한이 없어 NCP 크레딧
  초과(주최 미보전) 리스크가 있었음 → `--limit-concurrency 64` 추가. 서버
  예외가 uvicorn 자체 로거로는 트레이스백까지 남는 것을 확인 → 앱 자체
  로거에 예외 타입명만(원문·트레이스백 제외) 남기는 안전한 신호 추가.
- **배포/설정**: `.env.example`에 `PLANNER_STAGE`/`VECTOR_ENABLED`/
  `HCX_TPM_BUDGET` 누락 → 추가. **release manifest가 3커밋 전 SHA(`4afc169`)를
  참조 중이라 crash/오답 버그 수정 이후 상태를 반영 못 하고 있었음** → 재생성
  (현재 `e577107` 기준, 여전히 `DRAFT`). `scan_runtime_compliance.py`가
  `requirements-dev.txt`와 `scripts/tests/eval/deploy`를 스캔 범위 밖에 두고
  있었음 → 확장(44→84 files). **확장 직후 스캐너가 자기 자신을 오탐지**
  (`ANTHROPIC_API_KEY` 등 3개 패턴이 문자열 분할 처리가 안 돼 있었음) → 같은
  분할 기법 적용해 수정, 재스캔 0 findings로 검증.
- 이 라운드에서 `_lexical_entity_fallback`을 `reciprocal_rank_fusion` 경유로
  바꿈(단일 채널이라 순서엔 수학적으로 no-op임을 증명 후 적용) — "Federated
  Retrieval" 주장을 열망이 아니라 실제로 참이게 만듦.

낮은 우선순위로 확인만 하고 보류했던 것(2-8에서 마저 처리 완료):
`etl/kg.py`의 role/merge 로직 자체를 검증하는 전용 단위테스트 부재; `normalize_party`가
scope 구분 없이 정규화명만으로 병합.

최종 재검증(이 세션에서 직접 재실행): pytest 238/238, eval 640/640(100%,
거절률 0%, 공시율 98.55%), metamorphic 137/137, ruff clean, compliance
84 files/0 findings.

### 2-8. 보류 항목 마무리 (2026-08-08, 커밋 `c9efb67`)
사용자가 "보류한 것도 마저 해!!!"로 재지시. `etl/kg.py`의 party 병합을
`groupby(["scope", "normalized"])`로 바꾸고 `node_id`를
`party:<scope>:<normalized>`로 변경(edge dst_node_id·alias node_id도 동일
스킴으로 갱신). **재빌드하니 실제로 카운트가 바뀜**: kg_node 71,671→71,683
(+12), kg_alias 249,857→249,874(+17) — 즉 실 60,903개 상품 데이터 안에
스코프간 이름충돌이 12건 실재했고 지금까지 조용히 병합되고 있었다(이론적
리스크가 아니었음). `tests/unit/test_kg.py` 8개 신규(역할배정·동일스코프
병합·스코프간 미병합 회귀·alias종류·benchmark sentinel 제외·fund
manager_code 전용·구조적 invariant, `test_lexical.py`와 같은 in-memory
합성 데이터 스타일). 테스트 작성 중 실제 버그를 하나 더 발견: fund만 있고
채권/ETP가 0건인 합성 데이터에서 `CREATE TABLE kg_edge AS SELECT`가 빈
결과셋이라 DuckDB가 텍스트 컬럼을 INT32로 오추론해 다음 INSERT가
깨졌음(실 14.5만행 데이터에서는 절대 발생 못 하지만 방어 비용이 거의 0이라
`CAST(... AS VARCHAR)` 명시로 수정). 재검증: pytest **246/246**(238+kg
8개), eval 640/640(100%, KG graph 경로는 아직 live request path 밖이라
무변화 — 예상된 결과), metamorphic 137/137, ruff clean. 상세: `docs/15`
§0-3. **이걸로 낮은 우선순위 보류 항목은 더 이상 없다.**

## 3. 아직 안 된 것

| 항목 | 상태 | 비고 |
|---|---|---|
| 실제 `CLOVA_STUDIO_API_KEY`로 live E2E | ❌ | 키 없음. `deploy/live_hcx_plan_smoke.py` 준비됨 |
| 임베딩 캐시(`artifacts/embeddings/embeddings_cache.parquet`) | ❌ | 실키 필요. `scripts/build_embeddings.py` 완성돼 있음. 생성 후 `VECTOR_ENABLED=true`로 재빌드 |
| `PLANNER_STAGE=two` 640문항 전체 A/B | ❌ | flagship 1건 동등성만 실증(§2-3). eval 하네스가 DeterministicPlanner를 직접 호출해 두 단계 전체 재현엔 별도 mock-semantic 서버 배선 필요 |
| 공식 HCX model ID·API 계약 확정 | ❌ | `HCX-007`은 팀 baseline. 8/6 설명회 확정 대기 |
| Public HTTPS 배포 | ❌ | `deploy/compose.yaml` 준비됨, 실제 서버 없음 |
| GitHub **Organization** push | ⚠️ | 개인 private repo에만 있음 |
| FINAL release manifest | ❌ | 여전히 `DRAFT`(2-7·2-8에서 최신 SHA로 재생성은 완료, FINAL 전환엔 image digest·public 배포 등 다른 외부 gate 필요) |
| one-shot 기본값(모호시 추측 응답) | 🔵 의도적 보류 | 공식 요구사항이 역질문을 명시적으로 요구해서 재검토 후 보류. `docs/14` §W3 참고 |

낮은 우선순위 보류 항목(`etl/kg.py` 단위테스트, `normalize_party` scope-aware
병합)은 2-8에서 전부 처리 완료 — 아래 표에는 더 이상 남아있지 않다.

## 4. 다음 AI 에이전트가 할 일 우선순위

1. **키가 생기면**: `deploy/live_hcx_plan_smoke.py` 실행 → 성공하면
   `scripts/build_embeddings.py`로 임베딩 캐시 생성 → `VECTOR_ENABLED=true`로
   재빌드 → `PLANNER_STAGE=two` 실 HCX로 A/B.
2. **설명회(8/6) 이후**: `docs/08_BRIEFING_QUESTIONS_AND_DIFF_PROCESS.md`
   절차대로 반영. one-shot 기본값 여부도 이때 재결정.
3. **배포·제출**: `docs/10_RELEASE_FREEZE_RUNBOOK.md` 순서. FINAL manifest는
   freeze 직전 최종 커밋 기준으로 다시 한번 재생성할 것(매 커밋마다 자동
   갱신되지 않음 — 2-7에서 이걸 놓쳤던 사례 참고).

## 5. 이 문서를 쓰는 법 (다른 에이전트에게)

- **완전히 새로 이어받는다면** `docs/16_MASTER_PROJECT_NARRATIVE.md`(전체
  서사·사용자의 협상불가 원칙·설계 결정 이유)부터. 그 다음 이 파일(현재
  상태) → `docs/15_REBASELINE_VALIDATION_REPORT.md`(실측 수치) →
  `docs/14_BRIEFING_REBASELINE_PLAN.md`(설계 원안+진행상황) 순으로 읽어라.
  **이미 현재 상태만 빠르게 확인하면 되는 경우**엔 이 파일부터 시작해도
  된다.
- `git log --oneline briefing-rebaseline-v2` 상위 12개 커밋이 이번 재설계의
  전체 diff다(각 커밋 메시지가 상세 변경 근거를 담고 있음). `5c85af5`가
  최종 적대적 리뷰 수정분, `e577107`이 그 다음 종합 점검(Docker 실검증+
  제출문서 정확성+보안/배포 하드닝) 수정분, `c9efb67`이 그 라운드가 남긴
  보류 항목 마무리(KG scope-aware 병합+단위테스트) 수정분.
- `eval/`은 runtime 이미지에 안 들어가지만 저장소에는 포함돼 있다(오라클이
  `app/`을 import하지 않는 독립 검증 도구이기 때문). `eval/README.md` 참고.
- `devtools/`는 여전히 로컬에만 있고 저장소에는 없다.
