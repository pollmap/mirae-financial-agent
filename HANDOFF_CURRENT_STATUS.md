# 인계 문서 — 다른 AI 에이전트/세션이 이어받을 때 가장 먼저 읽을 파일

기준: 2026-08-08, branch **`briefing-rebaseline-v2`** (기본 브랜치 아님 — `main`이
아니라 이 브랜치를 체크아웃해야 최신 상태입니다)
저장소: `https://github.com/pollmap/mirae-financial-agent` (**private**)

이 문서는 이 저장소를 처음 보는 AI 에이전트가 별도 대화 맥락 없이도 "지금 뭐가
되어 있고, 뭐가 안 되어 있고, 다음에 뭘 해야 하는지"를 정확히 파악하도록 쓴
단일 진입점입니다. **내용이 다른 문서와 상충하면 이 파일 →
`docs/15_REBASELINE_VALIDATION_REPORT.md` → `docs/14_BRIEFING_REBASELINE_PLAN.md`
순으로 최신입니다.** `00_START_HERE.md`, `docs/11_IMPLEMENTATION_HANDOFF.md`,
`artifacts/windows_docker_verification_20260803.md`는 8/3-8/4 시점(설명회 이전
"prebrief" 상태, 태그 `prebrief-v1`)에서 멈춘 배경 문서입니다.

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
남은 것은 여전히 "진짜 HCX API 키·설명회 확정사항·배포 인프라가 있어야만
되는" 외부 gate뿐이다.**

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
- **Knowledge Graph**: `etl/kg.py`가 `kg_node`(71,671)/`kg_edge`(206,274)/
  `kg_alias`(249,857) materialize. ETF→managedBy, ETN→issuedBy 역할 구분,
  entity resolution은 정규화 후 완전일치만(오병합 방지).
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
(71,671 node/206,274 edge/249,857 alias, 80,670 lexical doc), production
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

낮은 우선순위로 확인만 하고 보류한 것(이유 포함, `docs/15` §0에 상세):
`etl/kg.py`의 role/merge 로직 자체를 검증하는 전용 단위테스트 부재(구조적
invariant만 검증 중); `normalize_party`가 scope 구분 없이 정규화명만으로
병합(현재 그래프 party 함수 호출자가 0이라 실 위험은 0, 라이브 배선 전
scope-aware groupby로 고치는 게 맞음).

최종 재검증(이 세션에서 직접 재실행): pytest 238/238, eval 640/640(100%,
거절률 0%, 공시율 98.55%), metamorphic 137/137, ruff clean, compliance
84 files/0 findings.

## 3. 아직 안 된 것

| 항목 | 상태 | 비고 |
|---|---|---|
| 실제 `CLOVA_STUDIO_API_KEY`로 live E2E | ❌ | 키 없음. `deploy/live_hcx_plan_smoke.py` 준비됨 |
| 임베딩 캐시(`artifacts/embeddings/embeddings_cache.parquet`) | ❌ | 실키 필요. `scripts/build_embeddings.py` 완성돼 있음. 생성 후 `VECTOR_ENABLED=true`로 재빌드 |
| `PLANNER_STAGE=two` 640문항 전체 A/B | ❌ | flagship 1건 동등성만 실증(§2-3). eval 하네스가 DeterministicPlanner를 직접 호출해 두 단계 전체 재현엔 별도 mock-semantic 서버 배선 필요 |
| 공식 HCX model ID·API 계약 확정 | ❌ | `HCX-007`은 팀 baseline. 8/6 설명회 확정 대기 |
| Public HTTPS 배포 | ❌ | `deploy/compose.yaml` 준비됨, 실제 서버 없음 |
| GitHub **Organization** push | ⚠️ | 개인 private repo에만 있음 |
| FINAL release manifest | ❌ | 여전히 `DRAFT`(2-7에서 최신 SHA로 재생성은 완료, FINAL 전환엔 image digest·public 배포 등 다른 외부 gate 필요) |
| one-shot 기본값(모호시 추측 응답) | 🔵 의도적 보류 | 공식 요구사항이 역질문을 명시적으로 요구해서 재검토 후 보류. `docs/14` §W3 참고 |
| `etl/kg.py` role/merge 전용 단위테스트 | 🔵 의도적 보류 | 2-7 참고. 실 호출자 0건이라 리스크 0, 그래프 기능 라이브 배선 전에 하면 됨 |
| `normalize_party` scope-aware 병합 | 🔵 의도적 보류 | 2-7 참고. 같은 이유로 리스크 0 |

## 4. 다음 AI 에이전트가 할 일 우선순위

1. **키가 생기면**: `deploy/live_hcx_plan_smoke.py` 실행 → 성공하면
   `scripts/build_embeddings.py`로 임베딩 캐시 생성 → `VECTOR_ENABLED=true`로
   재빌드 → `PLANNER_STAGE=two` 실 HCX로 A/B.
2. **설명회(8/6) 이후**: `docs/08_BRIEFING_QUESTIONS_AND_DIFF_PROCESS.md`
   절차대로 반영. one-shot 기본값 여부도 이때 재결정.
3. **배포·제출**: `docs/10_RELEASE_FREEZE_RUNBOOK.md` 순서. FINAL manifest는
   freeze 직전 최종 커밋 기준으로 다시 한번 재생성할 것(매 커밋마다 자동
   갱신되지 않음 — 2-7에서 이걸 놓쳤던 사례 참고).
4. **여유가 있다면**: `etl/kg.py` role/merge 단위테스트, `normalize_party`
   scope-aware groupby(§3의 두 보류 항목) — 그래프 기능을 실제로 라이브
   배선하기 전 선행 조건.

## 5. 이 문서를 쓰는 법 (다른 에이전트에게)

- 이 파일 → `docs/15_REBASELINE_VALIDATION_REPORT.md`(실측 수치) →
  `docs/14_BRIEFING_REBASELINE_PLAN.md`(설계 원안+진행상황) 순으로 읽어라.
- `git log --oneline briefing-rebaseline-v2` 상위 9개 커밋이 이번 재설계의
  전체 diff다(각 커밋 메시지가 상세 변경 근거를 담고 있음). `5c85af5`가
  최종 적대적 리뷰 수정분, `e577107`이 그 다음 종합 점검(Docker 실검증+
  제출문서 정확성+보안/배포 하드닝) 수정분.
- `eval/`은 runtime 이미지에 안 들어가지만 저장소에는 포함돼 있다(오라클이
  `app/`을 import하지 않는 독립 검증 도구이기 때문). `eval/README.md` 참고.
- `devtools/`는 여전히 로컬에만 있고 저장소에는 없다.
