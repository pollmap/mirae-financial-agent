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

## W2 (~8/21): KG + 2단계 플래너

1. `etl/kg.py`: `kg` 스키마(kg_node/kg_edge/kg_alias, edge마다 source_row_hash),
   `etl/build.py:946-948` 사이 삽입·`:848` 스키마 튜플 확장. 역할 모델링:
   ETF→managedBy, ETN→issuedBy, 채권→issuedBy, 펀드는 code 노드만(발명 금지).
   entity resolution은 정규화 후 완전일치만(접미사 제거; 한국투자≠한국투자증권),
   sameEntityAs는 is_inferred+공시. 해외 benchmark sentinel 2종 제외.
2. `app/retrieval/graph_retriever.py`: `WITH RECURSIVE` 1-2 hop,
   engine.py:404-449/753-769 LIKE 매칭 대체(플래그, fallback 유지).
3. `app/planner/pre_router.py`: ISIN/티커/정확명 → HCX 생략 lookup.
4. 2단계 플래너: Stage-1 축소 스키마(~1.2KB)+프롬프트(~1.5KB, 물리 metric 나열
   제거) `planner_stage` 플래그 + `app/semantics/grounder.py`(개념→물리,
   값→canonical, fail-closed). gold 전체 A/B 후 전환. TPM 실측 ≤7KB 게이트.
5. X03/X05 잔여 완화: 해외 AUM은 실데이터 거래통화 단일(USD) 확인 시
   허용+공시; 등급 순서는 여전히 순서정책 미확정 시 병렬 설명 유지.

## W3 (~8/28): Lexical + Federated + eval

1. `etl/lexical.py` 순수 SQL BM25(lex_doc/lex_term/lex_df/lex_stats; 해외 전략문
   5,566건·상품명 2/3-gram·비-sentinel benchmark) + `lexical_retriever`.
2. `app/retrieval/router.py`/`fusion.py`: 정확식별자→SQL직행 / 구조조건→SQL /
   관계→Graph / 테마→Lexical(+Vector). 구조필터 교집합, 채널 간 RRF(k=60).
   융합 결과는 product_uid 재조인 → FieldEvidence 경로 불변.
3. service.py:1136-1162·catalog_filters.py:59-60 잔여 차단 제거, one-shot
   기본값(`one_shot_mode`; 모호 시 최고 coverage 해석+대안 병기, demo는 역질문).
4. `eval/`: 템플릿 ≥500문항 + 독립 pandas oracle + metamorphic + ablation.
   게이트: 교차 거절 0, 공시 100%, 템플릿 정답 ≥95%.

## W4 (~9/6): Vector + freeze

1. `etl/vectors.py`: CLOVA Studio 임베딩 사전계산 → parquet 캐시 커밋 →
   FLOAT[1024]+array_cosine_similarity, 런타임 질의 임베딩 실패 시 BM25 강등.
   **지연 시 최우선 컷.**
2. ablation 리포트 → Docker parity·mock E2E·실 HCX smoke → FINAL manifest →
   9/4-9/6 수정만.

컷라인 순서: ①vector→BM25만 ②2단계→축소프롬프트 1단계 ③KG 재귀→alias 평면
lookup ④eval 500→200. **불가침: 교차 답변·공시 계약·증거 규율.**

## 리스크

1. 완화의 fail-closed 침식 → comparability 단일 진실원 + 공시 hard-assert +
   metamorphic 혼합값 검사.
2. 새 스키마 HCX 회귀 → A/B 후 전환, 구 스키마 shippable 유지.
3. readiness/reconcile assert 체인 → CSV·assert 동커밋 원칙.
4. entity resolution 오병합 → 완전일치만, 전용 eval 템플릿.
5. 일정 → 전 컴포넌트 플래그화 + 금요일 게이트 + 컷라인.
