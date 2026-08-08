# 설명회 반영 전면 재기준화 계획 (Federated Semantic Rebaseline)

기준: 2026-08-08, branch `briefing-rebaseline-v2`
승인된 원본 플랜: 이 문서는 실행 요약본이며, W1은 이미 구현·검증 완료.

## 방향 (사용자 확정 지시)

1. **교차 상품군 질의 무거절**: 통합 순위(호환) / 상품군별 분리(단위·통화 상이) /
   병렬 설명(척도 상이) / 부재 스코프 대안 안내 — 어떤 경우에도 거절하지 않고,
   공시(단위상태·기준일·coverage·0값·sentinel)를 극도로 엄격하게 첨부한다.
   fail-closed 속성은 "거절"에서 **"무공시 금지"**로 이동한다.
2. **설명회 기술스펙 전면 채택**: Ontology(런타임 통제 계층) · Knowledge Graph ·
   Federated Retrieval(SQL+Graph+BM25+Vector) · 2단계 플래닝.
3. 제약: HCX-007 단일 LLM, 2vCPU/4GB, DuckDB 읽기전용(인덱스류는 전부 ETL
   materialize), 9/6 freeze, 평가는 질문당 GET 1회(one-shot 기본).

## W1 완료 내역 (f8536e8)

- `registry/semantic/`: concept_catalog_v1(59 물리 metric→~40 개념, `ABSENT` 1급),
  comparability_matrix_v1(거절 값 없음), value_aliases_v1(번역은 is_inferred 공시).
- `app/semantics/`: 개념·비교가능성 로더 + `evaluate_cross_scope`(무거절, 약한
  모드 우선, 부재 스코프→대안).
- `app/execution/cross_scope.py`: 단일스코프 서브플랜 분해(기존 머신 재사용,
  스코프별 기준일 자동 유지) + 비적용 조건 스코프별 제외·공시 + 모드별 융합 +
  의무 공시 블록.
- registry 하드게이트(517-536) → capability 위임. engine dispatch 위임.
- 보수 정책 완화: ETP 양 스코프 `ALLOWED_SOURCE_LITERAL`(0값 제외·개수 공시).
- fixture 재기준화: X01(ETP+펀드 1Y 통합)·X02(보수 통합)·X04(표면금리·수익률
  병렬)·D06/O05(보수 순위 재개) 응답형으로 전환. 159/159 green.

## W2 완료 내역 (5d20385, 2026-08-08 — 계획보다 조기 완료)

1. `etl/kg.py`: `kg` 스키마(kg_node/kg_edge/kg_alias, edge마다 source_row_hash).
   역할 모델링: ETF→managedBy, ETN→issuedBy, 채권→issuedBy, 펀드는 code
   노드만(발명 금지). entity resolution은 정규화 후 완전일치만(접미사 제거;
   한국투자≠한국투자증권), sameEntityAs는 is_inferred+공시. 해외 benchmark
   sentinel 2종 제외. 실측: 71,671 node/206,274 edge/249,857 alias.
2. `app/retrieval/graph_retriever.py`: `WITH RECURSIVE` 1-2 hop,
   `engine.py`의 entity 해석에 exact-alias 우선 매칭으로 편입(LIKE fallback 유지).
3. `app/planner/pre_router.py`: ISIN/티커/정확코드 → HCX 생략 lookup
   (explain-intent 질문은 명시적으로 제외 — 다른 계약이므로).
4. 2단계 플래너 완료: `HCX_SEMANTIC_PLAN_SCHEMA`(1,045B)+
   `SEMANTIC_PLANNER_SYSTEM_PROMPT`(1,266B) + `app/semantics/grounder.py`
   (fail-closed, 개념→물리·값→canonical). `planner_stage` 플래그(기본 `one`).
   `tests/contract/test_hcx_two_stage_e2e.py`로 flagship 교차질의 Stage-1/2
   결과 동일성 실증. **TPM 13,013B→5,383B(−58.6%), 처리량 4→11건/분.**
5. X03 완전 해결: 해외/국내 AUM 등 통화분리 지표는 실측 지배통화(해외 USD
   100%, 국내 KRW 99.94%) 자동적용+공시로 전환(단일스코프 직접호출은 기존
   명시필터 요구 유지). X05(등급척도 상이)는 설계대로 EXPLAIN_ONLY 병렬 유지.

## W3 완료 내역 (4afc169, 2026-08-08)

1. `etl/lexical.py` 순수 SQL BM25(lex_doc/lex_term/lex_df/lex_stats) +
   `lexical_retriever`. 실측: 80,670 doc/1,288,698 posting/43,935 vocab.
2. `app/retrieval/router.py`/`fusion.py`(RRF k=60) 완료. 실제 연결 지점은
   계획의 "테마 텍스트 질의"가 아니라 **entity 해석 3단 안전망**으로 재설계:
   정확코드/ISIN → 정확 KG alias → LIKE 부분일치 → (전부 실패시만) lexical
   fallback. 겹치는 후보는 항상 기존 clarify 계약으로 흐르므로 오답 승격 없음.
   Vector 채널은 router 로직엔 있으나 엔진 호출부는 `vector_enabled=False`
   하드코딩(캐시 없는 현재 상태와 일치; 캐시 확보 시 한 줄 변경).
3. service.py의 교차스코프 수익률-기간 하드 차단
   (`CROSS_PRODUCT_LOCKED_PENDING_BASIS`) 제거 → 스코프별 선호기간 합집합으로
   단일스코프와 동일한 역질문 흐름으로 전환.
4. **one-shot 기본값은 재검토 후 보류**: 공식 요구사항이 "정보 부족 시
   확인불가 또는 역질문"을 명시적 채점 기준으로 요구하므로, 무조건 추측
   답변으로 대체하는 것은 확정되지 않은 가정으로 필수 기능을 트레이드하는
   것과 같다고 판단. 8/6 설명회에서 후속 파라미터 처리 관련 공식 답변이
   나오면 재검토.
5. `eval/`: 템플릿 640문항(목표 500 초과) + 독립 SQL oracle + metamorphic.
   **게이트 결과: 교차 거절 0%, 공시 98.55%, 정답률 100%(초기 77.5%에서
   4회 반복 개선; 그 과정에서 실제 planner 버그 4건 발견·수정)**.
   상세: `docs/15_REBASELINE_VALIDATION_REPORT.md`.

## W4 (~9/6): Vector 배선 + freeze 준비 — 진행 중

1. `etl/vectors.py`+`app/retrieval/vector_retriever.py`+
   `scripts/build_embeddings.py` **코드 완성**(23 unit test). 실 임베딩
   캐시는 CLOVA_STUDIO_API_KEY 수령 후 생성 필요 — `vector_enabled=false`
   유지 중. **컷라인 이미 선반영됨(지연 아님, 설계상 키 의존).**
2. 남은 항목: Docker fresh build/restart parity를 W2-W4 신규 스테이지(KG/
   lexical 빌드 포함) 대상으로 재검증 → FINAL manifest 준비 → 실 HCX 키
   수령 시 smoke → 9/4-9/6 수정만.

컷라인 순서(원안 유지, 현재까지 전부 유지 중): ①vector→BM25만(이미 기본)
②2단계→축소프롬프트 1단계(이미 기본, `two`는 검증되었으나 미승격)
③KG 재귀→alias 평면 lookup ④eval 500→200(불필요, 640 완주).
**불가침 원칙 — 전부 실측 확인됨: 교차 답변(거절률 0%)·공시 계약(98.55%)·
증거 규율(FieldEvidence 경로 무변경).**

## 리스크

1. 완화의 fail-closed 침식 → comparability 단일 진실원 + 공시 hard-assert +
   metamorphic 혼합값 검사.
2. 새 스키마 HCX 회귀 → A/B 후 전환, 구 스키마 shippable 유지.
3. readiness/reconcile assert 체인 → CSV·assert 동커밋 원칙.
4. entity resolution 오병합 → 완전일치만, 전용 eval 템플릿.
5. 일정 → 전 컴포넌트 플래그화 + 금요일 게이트 + 컷라인.
