# 마스터 프로젝트 서사 — 왜 지금 이 모습인가 (다른 에이전트 인수인계용)

이 문서는 이 저장소를 처음 여는 AI 에이전트가 **다른 계정, 다른 대화 세션,
사용자와의 사전 대화 없이** 프로젝트를 이어받을 때를 위해 쓴다. 목적은 "지금
뭐가 되어 있나"(그건 `HANDOFF_CURRENT_STATUS.md`가 더 압축적으로 잘 한다)가
아니라, **왜 지금 이 모양인지, 어떤 대안이 왜 기각됐는지, 사용자가 정확히
무엇을 협상 불가 조건으로 못 박았는지**를 전달하는 것이다. 이걸 모르면
겉보기에 합리적인 "개선"이 실제로는 사용자가 이미 명시적으로 거부한 방향으로
되돌아가는 실수를 할 수 있다.

**읽는 순서**: 이 문서 → `HANDOFF_CURRENT_STATUS.md`(현재 상태 압축본) →
`docs/15_REBASELINE_VALIDATION_REPORT.md`(실측 수치와 3번의 사후 검증
전체 경위) → `docs/14_BRIEFING_REBASELINE_PLAN.md`(원 설계안+주차별 진행
기록). 넷 중 내용이 상충하면 이 문서가 아니라 **`HANDOFF_CURRENT_STATUS.md`가
최신**이다(이 문서는 서사이지 실시간으로 갱신되는 상태판이 아니다) — 단,
"왜 이렇게 결정했는가"는 이 문서에만 있다.

## 0. v3 완성 부록 — 2026-08-08 `codex/federated-completion-v3`

사용자가 후속으로 “외부 키·인프라·사람 승인만 남기고 직접 해결 가능한 것은 전부
개발하라”고 확정했다. 이에 따라 이 문서 아래쪽의 두 과거 결정은 더 이상 현재
상태가 아니다.

1. **2단계 플래너를 운영 기본값으로 승격했다.** 집계 스키마와 grounder에
   `count/sum/avg/min/max` 및 `group_by`를 추가했고 mock HCX 단일·교차·집계 E2E,
   640 회귀, 100 고정 holdout을 통과했다. 1단계는 자동 fallback이 없는 수동
   롤백 옵션으로만 남았다.
2. **Federated 모듈을 실제 실행 경로에 연결했다.** `RetrievalPlan`이 Exact/Alias,
   SQL, Graph, BM25, 선택적 Vector 후보를 끝까지 유지하고 최종 상품·숫자·필터·정렬은
   SQL 공식 행으로 다시 검증한다. party resolver와 1-hop traversal, 자산유형·지역·
   위험등급·벤치마크 관계가 실제 호출된다. 전략/벤치마크와 퍼지명은 BM25, Vector
   cache가 있으면 1,024차원 검색과 RRF를 사용한다.

검증은 구현 전에 고정한 SHA-256
`0c7de9a9c98378a0d44c47e289c4ef7b9fb577cf3cebbd473b421066e5f823a8`
holdout 100/100, Graph 120/120, BM25 20/20, A~E 절제실험 PASS, 기존 640/640·
교차 거절 0%·metamorphic 137/137이다. Vector는 fixture 검증, F(HCX composer)는
실 credential 대기 상태다. 따라서 §5의 “기본 one”, §6의 “Graph/BM25/RRF 실 경로
미연결” 표는 당시 결정을 설명하는 역사 기록이며 현재 상태 판단에는 사용하지 않는다.

외부 작업은 HCX 20문항 A/B live gate, CLOVA Embedding key/cache/live smoke,
NCP VPC·서버·방화벽·도메인/TLS 공개 배포, 사람의 제출 승인/freeze뿐이다.

---

## 1. 이 프로젝트가 무엇인가

제10회 2026 미래에셋증권 AI Festival, `금융상품 Agent` 트랙 예선 제출물.
자연어 질문("국내 ETF 중 1년 수익률 높은 5개는?", "국내채권과 해외ETF를
AUM 기준으로 같이 순위 매겨줘")을 받아 국내채권·국내ETF/ETN·해외ETF/ETN·
공모펀드 4개 상품군에 대해 조회·검색·필터·정렬·비교·집계·**상품군간 교차
질의**를 수행하고, 원본 엑셀 행까지 추적 가능한 근거와 함께 한국어로 답하는
GET API. 제출 runtime의 언어모델은 **HyperCLOVA X(HCX) 단일**만 허용 —
다른 LLM은 개발 도구로도, fallback으로도, 판정자로도 절대 안 됨. 마감
2026-09-06.

핵심 기술: FastAPI + 읽기전용 DuckDB. LLM은 자연어를 typed QueryPlan(또는
2단계 모드에선 스코프 중립 "개념" 플랜)으로 바꾸는 역할만 하고, 상품 선택·
계산·정렬은 결정론적 엔진이 parameterized SQL로 수행한다. 모든 답변의 모든
수치·상품명은 근거(evidence)에 결속되며, 근거가 없으면 만들어내지 않고
명시하거나 역질문한다.

## 2. 전체 타임라인 (무슨 일이, 왜 일어났나)

### 2026-08-03 — "prebrief" 베이스라인
Windows PC에서 release candidate를 복원해 검증: source verify PASS, pytest
158/158, HTTP E2E 15/15, load smoke 100/100, Docker fresh build/restart
PASS. 이 시점의 설계는 핵심 기능이 다 됐지만 **상품군 교차 질의를 대부분
`INCOMPARABLE`로 거절**했고, Ontology/Knowledge Graph/Federated
Retrieval/2단계 플래닝 같은 게 전혀 없었다. `prebrief-v1` 태그로 이 상태를
보존해뒀다.

### 2026-08-06 — 설명회 + 외부 감사
설명회가 Ontology Grounding·Knowledge Graph·Federated Retrieval(SQL+
Graph+Vector+BM25 결합)·2단계 HCX 플래닝을 기술스펙으로 명시했다. 사용자가
가져온 외부 AI 감사 리포트(25개 항목의 전수감사)가 이 갭을 정확히
지적했고, 동시에 **GitHub 저장소가 실수로 PUBLIC 상태**였다는 것도
발견했다 — 즉시 PRIVATE로 복구.

### 2026-08-06~07 — 설계 재검토, 사용자의 핵심 지시
사용자가 감사 결과를 반영해 재설계하라고 지시한 뒤, **이 프로젝트 전체를
관통하는 협상 불가 원칙**을 명시적으로 못 박았다(원문, §3 참고). 요지:
상품군 교차 질의는 **어떠한 제한·한계도 없이** 항상 답변해야 하고(통화·기간·
단위가 달라도 거절이 아니라 통합/분리/설명 중 하나로 반드시 답함), 설명회가
요구한 기술스펙(Ontology+KG+Vector+SQL 결합)을 실제로 채택해야 하며, 이
모든 걸 대회 조건과 종합해 가장 합리적으로 재설계하라는 것.

### 2026-08-08 — W1부터 W4까지 쉬지 않고 개발
사용자가 "쉬지 않고 완벽하게" 개발하라고 지시(5개 이상 서브에이전트 병렬
사용도 명시적으로 지시). `docs/14_BRIEFING_REBASELINE_PLAN.md`의 W1-W4
계획대로 4주치 작업을 하루 안에 완주:
- **W1**: `registry/semantic/` 시맨틱 계층(개념 카탈로그·비교가능성 매트릭스·
  값 별칭), `app/semantics/capability.py`가 하드 거절 게이트를 대체,
  `app/execution/cross_scope.py` 교차 실행기.
- **W2**: `etl/kg.py` Knowledge Graph 빌드, `app/retrieval/graph_retriever.py`,
  2단계 플래너(`app/semantics/grounder.py`), pre-router.
- **W3**: 순수 SQL BM25 lexical 인덱스, federated 라우터/융합, one-shot
  기본값 검토, 640문항 eval 하네스(독립 SQL oracle) 첫 실행.
- **W4**: Vector 배선(코드 완성, 실키 없어 비활성), Docker parity 재확인
  시도(이땐 Docker Desktop이 안 떠서 실패), freeze 준비 문서화.

### 2026-08-08 — 1차 사후 검증: 3-agent 적대적 리뷰 (커밋 `5c85af5`)
사용자가 "최종 개발 점검"을 요청했다. **여기서부터가 이 프로젝트의 진짜
전환점이다**: 코드를 다시 읽는 대신, 실제로 실행해서 검증하는 3개 병렬
적대적 리뷰 에이전트를 자체 발주했다(교차상품군/시맨틱 안전성, federated
retrieval 코드, 데이터정책+eval 하네스 신뢰성). 결과:
- 실제 크래시 버그 1건, 무공시 오답 버그 1건, 그 외 minor 다수 발견·수정.
- **가장 중요한 발견**: eval 하네스 자체가 채점을 잘못하고 있었다.
  `cross_rank` 채점이 정답 여부를 계산은 해놓고 통과 판정에 반영을 안
  했고, `behavior` 채점은 "공시문구만 있으면 통과"로 새고 있었다. 고치니
  정확도가 100%→95.78%로 떨어졌다 — **이건 회귀가 아니라 그동안 가려져
  있던 진실이 드러난 것**이었다. 27건 전수조사 결과 앱 버그는 0건, 전부
  오라클(채점 기준)이 앱의 기존 정책 2가지를 놓치고 있던 것으로 확인,
  오라클을 고쳐 100%로 정직하게 재수렴.
- 상세 경위: `docs/15` §0.

### 2026-08-08 — 2차 사후 검증: 최종 종합 점검 (커밋 `e577107`/`434120c`)
사용자가 다시 한번 "남은 것·미흡한 것·개선할 것을 다 체크해서 개발해"라고
지시. 이 시점 Docker Desktop이 복구돼 fresh build/restart를 처음으로
실제 완료했고, 이전 리뷰가 안 다룬 4개 영역(제출 문서 정확성, API 보안,
이전 리뷰 보류 항목, 배포/설정)을 감사. **가장 심각했던 발견은 코드가
아니라 제출 문서**였다 — 실제 기술제안서(`docs/12`)가 재설계 이전
아키텍처를 그대로 서술하며 "교차 rank/compare는 fail-closed 유지"라는,
이 프로젝트의 핵심 지시와 정반대인 문장을 담고 있었다. README·docs/04·
아키텍처 다이어그램도 같은 문제. `requirements_traceability.csv`의
Federated Retrieval 관련 주장도 과장돼 있어(graph 순회·라우팅 함수가
테스트에서도 호출자 0건) 재작성. 상세: `docs/15` §0-2.

### 2026-08-08 — 3차: 보류 항목 마무리 (커밋 `c9efb67`/`247bded`)
사용자가 "보류한 것도 마저 해"라고 재지시. 2차 라운드가 "리스크 0"이라며
낮은 우선순위로 남겼던 두 항목(KG party 병합이 스코프를 무시함,
`etl/kg.py` 전용 테스트 부재)을 처리. **실 데이터로 재빌드하니 실제로
카운트가 바뀌었다**(kg_node +12, kg_alias +17) — 스코프간 이름충돌이
이론이 아니라 실재했다는 뜻. 새 테스트를 쓰는 과정에서 두 번째 진짜 버그
(빈 결과셋에서 DuckDB 타입 오추론)도 잡았다. 상세: `docs/15` §0-3.

**패턴을 보라**: 세 번의 사후 검증 모두 "다 됐다"고 보고한 직후 사용자가
"진짜? 다시 봐"라고 재지시했고, 매번 진짜 문제가 나왔다. 이게 이 사용자와
일하는 법의 핵심이다(§11 참고).

## 3. 사용자의 핵심 지시사항 (원문, 협상 불가)

아래는 이 세션에서 사용자가 실제로 입력한 문장들이다. 의역하지 않고
그대로 남긴다 — 향후 어떤 "개선"을 검토하든 이 원칙들과 충돌하면 안 된다.

> "아니 근데 교차검증도 어떠한 제한이나 한계가 있어도 안되고!!!! 그냥
> 극도로 까다롭게 가고 저기서 요구한 기술스펙인 온톨로지, Graph, Vector,
> SQL 결합는 반드시 쓰는편이 맞다고 생각하는데..지금 설꼐가 정말 더
> 나아?? 최종적으로 전체 검토하고 다시 가장 합리적인게 뭐고 조건은
> 뭔지 조건과 대회 모든걸 다 종합해서 설꼐 기획 개발하자!!"

이게 **이 프로젝트 전체를 지배하는 단일 최상위 원칙**이다. 두 가지를
동시에 요구한다: (1) 상품군 교차 질의는 어떤 이유로도 거절하면 안 됨 —
지금 구현은 `comparability_matrix_v1.csv`가 UNIFIED_RANK/SPLIT_
PRESENTATION/EXPLAIN_ONLY/ABSENT-대안 중 하나로 항상 답하는 스키마이며,
**이 스키마 자체에 거절 값이 없다.** (2) 설명회가 요구한 기술스펙
(Ontology+KG+Vector+SQL 결합)을 실제로 채택 — W1-W4가 이걸 구현했다.

다른 핵심 지시들(모두 원문):
- "전부 파악한 후 개발 완료하거라!!!" — 처음 인계받았을 때, 감사·이해부터
  완전히 하고 개발을 완료하라는 지시. **"완료"의 기준이 낮지 않다.**
- "데모 말고 진짜 작동하고 클로바 대신 다른거 연결시켜서 일단 확인하면
  되는거 아님?" — 데모로 눈속임하지 말고 실제로 작동시켜서 검증하라는
  요구. (실제로 이땐 Ollama+qwen3를 `devtools/`(gitignored) 뒤에 연결해
  검증했다 — **제출 코드에는 절대 포함 안 됨**, HCX-only 규칙은 절대
  타협 대상이 아니다.)
- "야 이걸 깃헙에 올려줄래?? 내가 다른 에이아이 에이전트에서 사용하고
  개선하고 현재 사항이 어떠하고 추가로 안한게 뭔지 하나도 빠짐없이!" —
  다른 AI 에이전트가 이어받을 수 있게 **빠짐없이** 기록하라는 지시. 이
  문서와 HANDOFF가 그 결과물이다.
- "W2부터 W4가지 쉬지 않고 개발해!!! 완벽하게!!!" + "서브에이전트를
  5개 이상 돌려서 빨리 개발해" — 속도와 병렬성에 대한 명시적 지시.
- "최최초최종 개발 점검 및 개발학"(→"최종 개발 점검"으로 해석) — 1차
  사후검증 트리거.
- "야 이제 최종적으로 남은게 뭐고 다한게 뭐고 미흡한건 없는지 개선한ㄹ건
  없는지 다 체크해서 개발해!!!" — 2차 사후검증 트리거.
- "보류한 것도 마저 해!!! 다만 불가능한건 어쩔수없고 ㅋㅋ 그리고 다하면
  깃헙에 최신화하고 이후 작업은 다른 코드 에이전트 계정으로 할건데..
  그녀석이 우리 전체 개발 과정과 대화와 가장 최신에 합의된 사항과
  추가적인 내용이나 디테일과 기획,설꼐 등등을 전부 작성하렴!!!" —
  3차 사후검증 트리거이자 **이 문서를 쓰라는 지시 그 자체**.

읽어보면 알겠지만 이 사용자는 (a) 진짜 작동하는 것과 데모를 명확히
구분하고, (b) "다 됐다"는 보고를 그대로 믿지 않고 반복적으로 재검증을
요구하며, (c) 빠짐없는 인수인계 문서를 여러 번 명시적으로 요구했다.

## 4. 아키텍처 — 지금 실제로 작동하는 것

```text
GET /answer?question=...&question_id=...
  → 계약 어댑터(app/main.py) — 입력 가드, 로그 리댁션
  → pre-router(app/planner/pre_router.py) — 정확 코드/ISIN/ticker면
    HCX 생략하고 결정론 lookup plan 직행
  → [해석 필요시] HyperCLOVA X — Stage-1: 스코프 중립 "개념"만 출력
    (물리 field/metric 이름은 절대 노출 안 함, 기본은 PLANNER_STAGE=one
    이라 물리 스키마를 직접 요청하는 구 방식, two는 opt-in)
  → [two 모드만] grounder.py Stage-2 — 개념→물리 QueryPlan, fail-closed
  → 스키마/시맨틱/allow-list 검증 (registry.py)
  → 단일 스코프: DuckDB 직접 실행
    교차 스코프: cross_scope.py가 스코프별 서브플랜으로 분해 → 각각
    기존 단일스코프 엔진 그대로 실행(정책·품질게이트 자동 상속) →
    comparability_matrix가 정한 방식(통합순위/분리제시/설명전용/대안)
    으로 융합 + 의무 공시
  → entity 해석 3단 안전망: exact code/ISIN → KG exact alias(딕셔너리
    조회, WITH RECURSIVE 순회 아님) → LIKE 부분일치 → (그래도 없으면)
    BM25 lexical fallback(RRF 경유, 현재 단일채널이라 순서엔 no-op)
  → EvidenceBundle + Answerability
  → evidence-only renderer
  → strict five-field JSON
```

**핵심 파일 지도**:
- `app/domain/models.py` — `OrganizerResponse` 5필드 계약. **이 세션
  내내 한 번도 안 바뀜.**
- `app/semantics/` — `capability.py`(교차가능성 판정), `grounder.py`
  (2단계 Stage-2), `concepts.py`(개념 카탈로그 로더), `normalize.py`
  (party명 정규화, scope-aware).
- `app/execution/cross_scope.py` — 교차 실행기. `app/execution/
  engine.py` — 단일스코프 엔진, entity 해석 3단 안전망.
- `app/retrieval/` — `graph_retriever.py`(KG 조회), `lexical_retriever.py`
  (BM25), `router.py`+`fusion.py`(RRF). **주의**: `router.route_theme_
  query`와 KG의 `traverse()`/party 조회 함수들은 구현·단위테스트는
  됐지만 **실 요청 경로에는 아직 연결 안 됨**(§6 참고, 과장하지 말 것).
- `etl/kg.py` — Knowledge Graph 빌드(node/edge/alias materialize).
  `etl/lexical.py` — BM25 인덱스 빌드. `etl/vectors.py` — 임베딩 배선
  (비활성).
- `registry/semantic/` — `concept_catalog_v1.csv`, `comparability_
  matrix_v1.csv`, `value_aliases_v1.csv`. 교차 답변의 "거절 없음"이
  코드가 아니라 **이 CSV들이 데이터로 정의**한다.
- `eval/` — 640문항 독립 SQL oracle 하네스. `app/`을 import하지 않는다
  (오라클 독립성이 핵심). Docker 이미지엔 안 들어가지만 git repo엔 있다.

## 5. 핵심 설계 결정과 그 이유 (기각된 대안 포함)

- **전면 재작성 대신 증분 추가**: 기존 스파인(service→planner→registry/
  engine→rendering)과 158개 테스트의 회귀 가치를 보존하려고, 신규
  컴포넌트를 전부 새 패키지(`app/semantics/`, `app/retrieval/`,
  `app/execution/cross_scope.py`)로 추가하고 기존 코드는 위임 지점만
  바꿨다. **기각된 대안**: 전면 재설계 — 시간 대비 리스크가 너무 컸고,
  이미 검증된 회귀 안전망을 버리는 셈이었다.
- **cross_scope.py가 기존 단일스코프 엔진을 재사용**: 교차 스코프
  실행기를 별도로 새로 짜지 않고, N개 서브플랜으로 쪼갠 뒤 **이미
  검증된** 단일스코프 머신에 그대로 넘긴다. 이렇게 하면 단일스코프의
  모든 정책·품질게이트(sentinel 차단, coverage 공시 등)가 교차 경로에도
  자동으로 적용된다 — 별도로 재구현했다면 놓치기 쉬운 부분.
- **비교가능성을 CSV(데이터)로, 코드 하드게이트로 안 함**: "거절 없음"을
  지키는 방법은 두 가지였다 — 코드에서 스코프 수를 세어 판단하거나(예전
  방식), `comparability_matrix_v1.csv`가 개념별로 통합/분리/설명/대안을
  선언하는 방식. 후자를 택했다 — **스키마 자체에 거절 값이 없어서**,
  코드 리뷰만으로는 "거절 경로가 남아있는지" 걱정할 필요가 없다.
- **2단계 플래닝을 기본값이 아니라 opt-in으로**: Stage-1(개념만)+
  grounder 방식이 TPM을 58.6% 줄이지만, 640문항 전체 A/B가 아직 없어서
  (mock-HCX로 flagship 1건만 검증) 기본값은 여전히 검증이 더 풍부한
  구 방식(`PLANNER_STAGE=one`)이다. **성급하게 기본값을 바꾸지 않은
  이유**: 이미 100%로 검증된 시스템의 기본 경로를 덜 검증된 경로로
  바꾸는 건 이 프로젝트의 "실행해서 검증" 원칙에 위배된다.
- **Federated Retrieval의 상당 부분이 아직 실 경로에 안 연결됨을
  숨기지 않고 명시**: `router.route_theme_query`, `reciprocal_rank_
  fusion`(엔진 fallback 경유 제외), KG의 `traverse()`/party 함수는
  구현·단위테스트는 됐지만 실 요청에서 호출된 적이 없다. **기각된
  선택지**: 이걸 서둘러 실 경로에 배선하는 것 — 100%로 막 도달한
  시스템에 eval로 검증 안 된 새 동작 경로를 넣는 리스크가 이득보다
  컸다. 대신 정직하게 "코드 완성, 실 경로 미배선"이라고 문서화했다
  (`artifacts/requirements_traceability.csv` SEM-002/SEM-003).
- **normalize_party 병합을 (scope, 정규화명) 단위로**: 처음엔 정규화명
  단위로만 병합했는데, 채권 발행사와 ETF 운용사가 우연히 같은 정규화
  문자열이 되면(Ltd/LLC 같은 접미사를 같은 걸로 취급하므로) 실제로는
  다른 두 회사가 병합될 위험이 있었다. 3차 라운드에서 scope를
  groupby key에 추가해 고쳤고, 재빌드하니 실 데이터에 이런 충돌이
  12건 실재했다(§2 참고).
- **eval 하네스가 `app/`을 import하지 않는 독립 오라클**: 이건 처음부터
  의도적 설계였고, 1차 사후검증 라운드가 왜 중요했는지의 핵심이다 —
  하네스 자체에 채점 버그가 있어도 "답을 하네스에 맞춘" 게 아니라
  오라클의 물리적 계산 로직을 실제로 고쳐야 했다(오라클이 `app/`
  코드를 참조할 수 없으므로 우회할 방법이 없다).

## 6. Federated Retrieval — 정직한 현재 상태 (과장 금지)

이 항목을 따로 뺀 이유: `artifacts/requirements_traceability.csv`가
한때 이걸 과장했다가 재확인 과정에서 잡혔다(§2, 2차 라운드). 다음 표를
그대로 믿어라 — grep으로 직접 재확인된 사실이다.

| 컴포넌트 | 실 요청 경로에 연결됨? | 테스트됨? |
|---|---|---|
| SQL exact code/ISIN lookup | ✅ | ✅ |
| KG exact alias 딕셔너리 조회(`resolve_product_nodes_by_name`) | ✅ | ✅ |
| LIKE 부분일치 | ✅ | ✅ |
| BM25 lexical fallback(`_lexical_entity_fallback`) | ✅(3단 폴백으로만) | ✅ |
| `reciprocal_rank_fusion`(RRF) | ✅(위 fallback 경유, 현재 단일채널이라 순서엔 no-op) | ✅ |
| `router.route_theme_query` | ❌ | 자체 단위테스트만, 실 경로 호출자 0 |
| KG `traverse()`(WITH RECURSIVE 다중 홉) | ❌ | 테스트도 0건 |
| KG party 조회(`resolve_party_nodes`/`products_for_party`) | ❌ | 테스트도 0건 |
| Vector 검색 | ❌(설계상 비활성, `vector_enabled=false`) | 단위테스트만 |

640문항 eval에서 lexical fallback 자체가 **0회 트리거**됐다(모든 정답
질문이 SQL 경로만으로 풀렸다는 뜻) — 즉 eval의 100%는 SQL 경로의
정확성을 증명하지, federated retrieval 스택 전체를 증명하지 않는다.
이걸 그래프/라우터/vector까지 실 경로에 배선하려면 **그 전에** 퍼지
매칭/근접 매칭을 요구하는 새 eval 질문군을 먼저 만들어야 한다(현재
640문항은 전부 정확 매칭 위주로 설계됨) — 그래야 배선 후에도 정확도가
유지되는지 검증할 수 있다.

## 7. 지금 검증된 수치 (재현 명령 포함)

```bash
cd mirae_financial_agent_codex_prebrief
git checkout briefing-rebaseline-v2   # main 아님!
py -3.12 -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
./.venv/Scripts/python.exe scripts/verify_sources.py            # PASS
./.venv/Scripts/python.exe -m ruff check app deploy etl scripts tests eval  # PASS
./.venv/Scripts/python.exe scripts/scan_runtime_compliance.py   # 84 files/0 findings
./.venv/Scripts/python.exe scripts/build_data.py --no-parquet   # KG+lexical 포함
./.venv/Scripts/python.exe -m pytest -q                         # 246 passed
./.venv/Scripts/python.exe -m eval.run_eval                     # 640/640 (100%)
./.venv/Scripts/python.exe -m eval.metamorphic                  # 137/137
```

기대값: pytest **246/246**, eval **640/640(100%)**, 교차거절률 **0.0%**,
공시율 **98.55%**, metamorphic **137/137**, compliance **84 files/0**,
KG **71,683 node/206,274 edge/249,874 alias**, lexical **80,670 doc/
1,288,698 posting**. Docker fresh `--no-cache` build+run+restart도
2026-08-08에 실제로 검증됨(`docs/15` §0-2).

**Windows 주의사항** (이 세션에서 반복적으로 부딪힌 것들):
- Makefile의 `PYTHON ?= .venv/bin/python`은 WSL/Linux 가정. Windows
  Git Bash/PowerShell에선 `.venv/Scripts/python.exe -m ...`로 직접
  호출할 것.
- Git Bash `curl`은 한글 query를 깨뜨린다 — PowerShell `curl.exe` 또는
  Python `httpx` 사용.
- `git status`/`git diff`는 Windows CRLF 자동변환 때문에 실제로 파일
  내용이 바뀌었어도 "변경없음"으로 보일 수 있다. 의심되면 `md5sum`이나
  파일 내용을 직접 비교해서 재확인할 것 — 이 세션에서 실제로 이것 때문에
  "eval_report.json이 최신인가?"를 잘못 판단할 뻔했다.
- Docker Desktop 데몬이 이 개발 PC에서 여러 번 응답을 멈췄다(프로세스
  누적이 원인이었던 적도 있음). 안 뜨면 `Get-Process`로 중복 프로세스
  확인 후 정리하고 한 번만 깨끗하게 재시작할 것 — 재시도를 반복하면
  오히려 프로세스가 더 쌓인다.
- bash에서 백슬래시 경로 안에 변수를 바로 붙이면(`...\$var`) `\$`가
  리터럴 `$`로 escape돼 변수 확장이 안 된다 — 경로를 변수로 먼저 뽑고
  `"$BASE/$var"`처럼 슬래시로 이어붙일 것.

## 8. 저장소 지도

- **브랜치**: `briefing-rebaseline-v2` (기본 `main` 아님! 반드시 이
  브랜치를 checkout할 것). GitHub: `pollmap/mirae-financial-agent`,
  **private**.
- **커밋 순서** (전부 `git log --oneline briefing-rebaseline-v2`로 확인
  가능, 오래된 순): `78592d5`(P0) → `f8536e8`(W1) → `2719bd3`(docs14) →
  `577d4f2`(W2 core) → `5d20385`(W2 complete+eval) → `4afc169`(W3) →
  `cf40740`(W4 docs) → `5c85af5`(1차 적대적 리뷰) → `4c23dda`(1차 리뷰
  문서) → `e577107`(2차 종합점검) → `434120c`(2차 문서) → `c9efb67`
  (3차 보류항목 마무리) → `247bded`(3차 문서). 각 커밋 메시지가 상세
  변경 근거를 담고 있다 — 요약이 아니라 원본을 읽을 가치가 있다.
- **prebrief-v1 태그**: 8/3 재설계 이전 상태 보존.
- **`devtools/`**: 실 LLM(Ollama+qwen3) 검증 브릿지. **저장소에 없고
  영구 gitignore** — HCX-only 규칙 위반 소지가 있어서 절대 커밋 안 함.
- **`inputs/`**: 원본 대회 데이터(PDF/ZIP). 무수정 보존, SHA-256 검증.

## 9. 진짜 남은 것 (전부 외부 gate, 코드로 해결 불가)

`HANDOFF_CURRENT_STATUS.md` §3이 최신 표를 유지한다. 요지만 적으면:

1. **실 `CLOVA_STUDIO_API_KEY`** — 없으면 live E2E도, 임베딩 캐시
   생성도(`scripts/build_embeddings.py`는 완성돼 있음), `PLANNER_
   STAGE=two`의 640문항 전체 A/B도 불가능.
2. **8/6 설명회의 공식 확정사항** — `HCX-007`은 팀 baseline일 뿐 주최
   확정 모델 ID가 아님. `docs/08_BRIEFING_QUESTIONS_AND_DIFF_PROCESS.md`
   절차대로 반영 필요.
3. **Public HTTPS 배포** — `deploy/compose.yaml` 준비됨, 실제 서버 없음.
4. **GitHub Organization push** — 현재 개인 private repo에만 있음(주최
   지침에 따라 제출 시 org repo에 올려야 할 수 있음).
5. **FINAL release manifest** — 여전히 `DRAFT`. `scripts/generate_
   release_manifest.py --final`은 image digest·public 배포 등을
   요구한다. **주의**: 이 매니페스트는 커밋할 때마다 자동 갱신되지
   않는다 — 2차 라운드에서 실제로 3커밋 뒤처진 걸 발견했다. freeze
   직전 최종 커밋 기준으로 반드시 재생성할 것.
6. **9/6 마감, 9/5 내부 freeze** — `docs/10_RELEASE_FREEZE_RUNBOOK.md`
   순서대로. 마감 후 commit/push/deploy 등 변경은 실격 사유(대회 규정).

**낮은 우선순위 보류 항목은 이제 없다** — `etl/kg.py` 단위테스트와
`normalize_party` scope-aware 병합 둘 다 3차 라운드(`c9efb67`)에서
마무리됐다.

## 10. 이 세션의 작업 방식에서 배운 것

- **"완료됐다"는 보고를 그대로 믿지 말 것.** 이 세션에서 3번의 "다
  됐습니다" 보고 중 3번 다 사용자가 재검증을 요구했고, 3번 다 진짜
  문제가 나왔다(eval 채점 버그, 제출 문서의 거짓 서술, 실 데이터의
  스코프간 이름충돌). 특히 **자기 자신이 만든 채점/검증 도구도
  의심할 것** — eval 하네스 자체가 틀린 채점을 하고 있었다.
- **코드를 읽는 것과 실행하는 것은 다르다.** 이 세션에서 가치 있었던
  발견은 전부 "실제로 실행해서" 나왔다 — Docker를 진짜 빌드해서 실행,
  eval을 진짜 재실행, kg.py 테스트를 진짜 작성해서 돌려봄. 코드
  리뷰만으로는 이 중 어느 것도 못 찾았을 것이다.
- **"과장하지 않기"가 정확성만큼 중요하다.** SEM-002/SEM-003을 두 번
  고쳤다 — 처음엔 "구현됨"이라고만 썼다가, 재확인해서 "실 경로엔
  일부만 연결됨"으로, 다시 재확인해서 "테스트조차 없는 것도 있음"으로
  더 정밀하게 고쳤다. 대회 제출 문서에서 실제보다 과장하는 건 정확성
  문제이자 신뢰성 문제다.
- **Git 커밋을 두 단계로 나누는 패턴**: 코드/기능 변경 커밋 → 그
  커밋의 실제 SHA를 참조하는 문서/매니페스트 커밋. 이렇게 하면
  release manifest가 항상 "그 앞 커밋"을 정확히 가리키고, 문서가
  거짓 SHA를 담지 않는다.
- **의도적으로 보류한 것은 "왜"까지 기록할 것.** "낮은 우선순위"라고만
  적으면 다음 에이전트가 왜 그런지 몰라서 서둘러 고치거나 반대로
  영원히 방치할 수 있다. 이 세션은 항상 "왜 지금은 리스크가 0인가"와
  "언제 이걸 진짜로 고쳐야 하는가"를 같이 적었다.

## 11. 이 사용자와 일하는 법

- 한국어로 소통하며, 감탄부호·강조("!!!", "ㅋㅋ")가 많은 편이지만 요구
  사항 자체는 정확하고 기술적으로 깊다 — 톤에 속아 요구를 가볍게 보지
  말 것.
- **"점검해"/"체크해"는 "다시 읽어봐"가 아니라 "다시 실행해서
  검증해"를 의미한다.** 매번 실제로 코드를 돌리고, 실제로 Docker를
  빌드하고, 실제로 재빌드해서 숫자가 바뀌는지 확인하는 식으로 응답해야
  했다.
- **속도와 병렬성을 명시적으로 선호한다**("서브에이전트 5개 이상 돌려서
  빨리"). 여러 독립적인 조사/구현이 있으면 병렬 에이전트를 적극
  활용할 것.
- **"보류"는 영구 면제가 아니라 유예다.** 이 세션에서 낮은 우선순위로
  미룬 항목들도 결국 "마저 해"라는 지시로 이어졌다. 보류할 땐 정확한
  이유와 재개 조건을 남길 것("불가능한 것"과 "지금 안 한 것"을 사용자가
  구분해서 요구한다는 점도 주목 — 순수 외부 gate만 예외로 인정한다).
- **인수인계 문서를 매우 중요하게 여긴다.** "하나도 빠짐없이"를 여러
  번 강조했다 — 압축된 요약보다 전체 서사와 이유를 남기는 쪽을
  선호한다. 이 문서 자체가 그 요구의 산물이다.
- 이 프로젝트에 관한 이전 세션 기억은
  `C:\Users\lch68\.claude\projects\C--Users-lch68-Desktop\memory\mirae-financial-agent.md`
  (같은 Claude Code 사용자 계정 한정, 다른 에이전트 계정에선 접근 불가)에
  누적돼 있다. 접근 가능하면 참고할 것.
