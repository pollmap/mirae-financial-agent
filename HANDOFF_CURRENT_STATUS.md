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
전면 재설계(federated semantic rebaseline)를 완료했고, 640문항 자체 평가에서
100% 정답·교차상품군 거절률 0%를 실측했다. 남은 것은 여전히 "진짜 HCX API
키·설명회 확정사항·배포 인프라가 있어야만 되는" 외부 gate뿐이다.**

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

### 2-5. 이번 재설계 과정에서 발견·수정한 실제 버그 4건
`docs/15_REBASELINE_VALIDATION_REPORT.md` §3에 상세. 요약: (1) 점(.) 포함
티커가 4개 엔티티로 조각화되던 문제, (2) "가장 작은"이 정렬 안 되던 문제,
(3) "ETF·ETN" 결합 문구가 ETN만 세던 비대칭 가드, (4) 서로 다른 스코프의
서로 다른 지표를 나란히 요청하면 크래시하던 문제. 전부 eval 하네스(640문항,
독립 SQL oracle)가 잡아냈고 근본 원인을 고쳤다 — 하네스를 답에 맞춘 게 아니다.

### 2-6. Docker / HCX mock / 실 LLM 검증 (8/3 시점, 아직 유효하지만 W2-W4
신규 스테이지로 재검증 필요 — §4 참고)
- `deploy/mock_clova_studio.py`(dev 전용): CLOVA Studio v3 계약 재현.
- `devtools/`(실 LLM 브릿지, **저장소 미포함·gitignore**): Ollama+qwen3:8b로
  실제 LLM 연결 검증한 기록. 재현하려면 이 파일 대신
  `artifacts/windows_docker_verification_20260803.md` §5,7 참고.
- GitHub: `pollmap` 개인 private repo. `.env.production`/`devtools/`는
  git-ignore로 미포함 확인됨.

## 3. 아직 안 된 것

| 항목 | 상태 | 비고 |
|---|---|---|
| **Docker fresh build/restart, W2-W4 스테이지 포함 재검증** | ⏳ 진행중 | 8/3 검증은 KG/lexical/vector 빌드 스테이지 이전 버전 기준. 이 세션에서 재검증 시도했으나 Docker Desktop 데몬 기동 지연으로 완료 못 함(다음 에이전트가 이어서 `docker build --no-cache` 실행) |
| 실제 `CLOVA_STUDIO_API_KEY`로 live E2E | ❌ | 키 없음. `deploy/live_hcx_plan_smoke.py` 준비됨 |
| 임베딩 캐시(`artifacts/embeddings/embeddings_cache.parquet`) | ❌ | 실키 필요. `scripts/build_embeddings.py` 완성돼 있음. 생성 후 `VECTOR_ENABLED=true`로 재빌드 |
| `PLANNER_STAGE=two` 640문항 전체 A/B | ❌ | flagship 1건 동등성만 실증(§2-3). eval 하네스가 DeterministicPlanner를 직접 호출해 두 단계 전체 재현엔 별도 mock-semantic 서버 배선 필요 |
| 공식 HCX model ID·API 계약 확정 | ❌ | `HCX-007`은 팀 baseline. 8/6 설명회 확정 대기 |
| Public HTTPS 배포 | ❌ | `deploy/compose.yaml` 준비됨, 실제 서버 없음 |
| GitHub **Organization** push | ⚠️ | 개인 private repo에만 있음 |
| FINAL release manifest | ❌ | 여전히 `DRAFT` |
| one-shot 기본값(모호시 추측 응답) | 🔵 의도적 보류 | 공식 요구사항이 역질문을 명시적으로 요구해서 재검토 후 보류. `docs/14` §W3 참고 |

## 4. 다음 AI 에이전트가 할 일 우선순위

1. **Docker 재검증부터**: `docker build --no-cache -t mirae-financial-agent:rc .`
   → 빌드 로그에서 `build_kg`/`build_lexical` 성공 확인(카운트가 §2-3과
   비슷해야 함) → `docker run` → `scripts/e2e_smoke.py` → `docker restart` →
   동일 결과 확인. (이 세션에서 Docker Desktop 데몬이 안 떠서 못 끝냄.)
2. **키가 생기면**: `deploy/live_hcx_plan_smoke.py` 실행 → 성공하면
   `scripts/build_embeddings.py`로 임베딩 캐시 생성 → `VECTOR_ENABLED=true`로
   재빌드 → `PLANNER_STAGE=two` 실 HCX로 A/B.
3. **설명회(8/6) 이후**: `docs/08_BRIEFING_QUESTIONS_AND_DIFF_PROCESS.md`
   절차대로 반영. one-shot 기본값 여부도 이때 재결정.
4. **배포·제출**: `docs/10_RELEASE_FREEZE_RUNBOOK.md` 순서.

## 5. 이 문서를 쓰는 법 (다른 에이전트에게)

- 이 파일 → `docs/15_REBASELINE_VALIDATION_REPORT.md`(실측 수치) →
  `docs/14_BRIEFING_REBASELINE_PLAN.md`(설계 원안+진행상황) 순으로 읽어라.
- `git log --oneline briefing-rebaseline-v2` 상위 6개 커밋이 이번 재설계의
  전체 diff다(각 커밋 메시지가 상세 변경 근거를 담고 있음).
- `eval/`은 runtime 이미지에 안 들어가지만 저장소에는 포함돼 있다(오라클이
  `app/`을 import하지 않는 독립 검증 도구이기 때문). `eval/README.md` 참고.
- `devtools/`는 여전히 로컬에만 있고 저장소에는 없다.
