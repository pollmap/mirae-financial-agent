# 공식 적합성·재질문·적대적 검증 최신 기준

작성일: 2026-08-09

적용 브랜치: `codex/federated-completion-v3`

상태: **`PENDING_EXTERNAL` — 로컬 완성 후보, 실제 HCX·Embedding·공개배포 미검증**

이 문서는 공식 적합성 판단의 최신 기준이다. 전체 최종 설계·수치·외부 gate는
`docs/18_FINAL_MASTER_PLAN_AND_RELEASE_READINESS.md`, 기계 판독 수치는
`artifacts/release_evidence_v4.json`, 역사적 개발 서사는
`docs/16_MASTER_PROJECT_NARRATIVE.md`를 따른다.

## 1. 출처의 권위

| 등급 | 출처 | 효력 |
|---|---|---|
| `OFFICIAL_PDF` | `inputs/official_task.pdf` p.4–7 | 과제·평가·제출의 최우선 기준 |
| `OFFICIAL_DATA` | `inputs/official_data.zip` | 상품·숫자·근거의 권위 원천 |
| `OFFICIAL_WEB` | 공식 Festival 공지·FAQ | PDF 이후 확정 공지만 반영 |
| `BRIEFING_GUIDANCE` | 제공 설명회 녹취 | Ontology·KG·Federated·grounding 방향 |
| `TEAM_DECISION` | 마스터플랜·내부 gate | 공식보다 엄격할 수 있으나 공식 요구로 표현하지 않음 |
| `PENDING_EXTERNAL` | 최종 모델/API/운영 공지 | 확인 전 release를 통과시키지 않음 |

원본 PDF·ZIP·DOCX는 수정하지 않는다. 기존 마스터플랜이 공식 자료와 충돌하면 공식 자료가
우선한다. 외부 비공식 데이터는 평가 근거에 섞지 않는다.

## 2. 공식 자료에서 확인한 조건

- 대상은 국내채권, 국내 ETP, 해외 ETP, 공모펀드 네 스코프다.
- 자연어 조건검색, 상세조회, 비교, 정렬·순위·집계, 상품군 교차질의와 근거 표시가
  필요하다.
- 공식 데이터로 확인할 수 없으면 확인 불가를 밝히거나 결과를 바꾸는 부족 조건을
  역질문한다.
- 데이터 없는 수익률·가격 전망과 단정적 투자 추천을 하지 않는다.
- 제출·평가 runtime의 언어모델은 HyperCLOVA X만 사용한다.
- 주최 측 데이터가 평가 근거이며 외부 데이터와 충돌하면 주최 측 자료를 우선한다.
- 평가 API 예시는 `GET /answer`와 `question_id`, `question`, `retrieved_context`,
  `think_trace`, `answer` 다섯 문자열 필드다. adapter로 호환성을 유지하고 최종 공지가
  달라지면 adapter만 변경한다.
- 재현 가능한 코드·Docker·README, 기술제안서, endpoint와 JSON 명세, public network API
  운영이 제출 범위다. 마감 뒤 결과를 바꾸는 commit/push/deploy는 금지한다.

공식 평가 문항 수·동시성·timeout은 공개 확정되지 않았다. 20·100·200·300·640·1,200·
1,500·2,100·5,000은 모두 팀 내부 검증량이지 공식 시험 문항 수가 아니다.

## 3. 요구별 현재 판정

| 공식 요구 | 구현·증거 | 상태 |
|---|---|---|
| 조회·복합필터·비교·정렬·집계 | 2단계 semantic plan → ConditionLedger → allow-list SQL | `VERIFIED_LOCAL` |
| 교차질의 | scope별 계획·후보 보존, 거부하지 않고 통합/분리/설명 | `VERIFIED_LOCAL` |
| 근거 | 공식 file/sheet/Excel row/field/hash와 claim 결속 | `VERIFIED_LOCAL` |
| 정보 부족 역질문 | 가장 판별력 높은 질문 하나, signed 2·3·4턴 상태 | `VERIFIED_LOCAL` |
| 안전 | injection·prompt/SQL/key 유출·허위근거·미래예측·단정추천 차단 | `VERIFIED_LOCAL` |
| HCX-only | 다른 LLM provider·fallback 없음, production은 HCX key 없이 시작 거부 | 정적·mock `VERIFIED_LOCAL`; live `PENDING_EXTERNAL` |
| Federated Retrieval | Exact/Alias·SQL·Graph 1–2 hop·BM25, optional Vector/RRF | Graph/BM25 `VERIFIED_LOCAL`; Vector `VERIFIED_FIXTURE` |
| 공개 API | 5-string response, no-store, access-log off, healthcheck | local `VERIFIED_LOCAL`; public TLS `PENDING_EXTERNAL` |

## 4. 조건 누락 방지와 역질문

모든 질문은 `ConditionLedger`를 만든다. 스코프, 상품유형, 운용사·발행사, 지역,
자산유형, 전략, 벤치마크, 기간, 지표, 조건, 정렬, 집계 각각은 `grounded`,
`clarification_required`, `unavailable`, `not_comparable` 중 하나여야 한다. 중요한 조건이
설명 없이 사라지면 `FULL`을 금지한다.

“좋은 상품”은 상품 범위를, “좋은 해외 ETF”는 평가 기준을 묻는다. “수익률 높은 ETF”는
시장과 기간 중 판별력이 더 큰 하나부터 질문한다. “배당 인컴 전략과 비슷한 해외 ETF”를
단순 첫 5개로 바꾸거나 “미래에셋·미국 주식형” 중 하나를 버리는 동작은 회귀 테스트로
차단한다.

사용자 응답은 서명 상태에 누적한다. 조건 보충, 대명사, 지표 변경, 정정, 없는 선택지,
근거 요구를 지원한다. 만료·변조 token과 불완전 parameter pair는 통제된 오류로 차단한다.
교차질의는 정보 부족을 이유로 거부하지 않고 필요한 조건을 물어 끝까지 처리한다.

## 5. Retrieval·근거·통화

- Exact/Alias: 코드와 정확명·검증된 별칭
- SQL: 숫자·필터·정렬·집계·최종 근거 행의 권위 원천
- Graph: 운용사·발행사·지역·자산·위험·벤치마크의 scope/role 격리된 실제 1–2 hop
- BM25: 전략·벤치마크 설명·퍼지 상품명
- Vector/RRF: embedder와 정확히 1,024차원 cache가 모두 유효할 때만 사용

Graph 후보는 SQL 원본행으로 재검증한다. Graph·Vector 장애 또는 coverage 부족은 SQL/BM25로
명시적으로 폴백하고 이유를 trace에 남긴다. Vector에는 zero-padding이 없으며 현재 실 cache는
0이므로 live Vector라고 주장하지 않는다.

국내 금액 지표도 요청 지표를 가진 필터 결과가 단일 통화임을 데이터로 증명할 때만 기본
통화를 적용한다. 그렇지 않으면 통화를 역질문한다. 단위·기준일·기간이 다르면 직접 비교하지
않고 차이를 공개한다. 결측·0·sentinel은 서로 치환하지 않는다.

`think_trace`는 채널, 조건 검증, 후보 수, fallback, 데이터 hash·시간 같은 검증 메타데이터만
담는다. 비공개 추론, system prompt, 원문 민감 헤더, key를 담지 않는다.

## 6. 2026-08-09 최종 재실행 증거

| 검증 | 결과 | 해석 |
|---|---:|---|
| source/ETL | XLSX 8, raw 145,393, serving 60,903, quarantine 10 | `VERIFIED_LOCAL` |
| KG/lexical | 71,683/206,274/249,874; 82,249/1,291,823/43,932 | `VERIFIED_LOCAL` |
| pytest/Ruff/compliance | 288/288; PASS; 102 files/0 | `VERIFIED_LOCAL` |
| 기존 oracle/metamorphic | 640/640, 교차거부 0; 137/137 | `VERIFIED_LOCAL` |
| Federated | 100/100, Graph 120/120, BM25 20/20, A–E PASS | local/fixture |
| v4 holdout | 200/200 | 수정 동결 corpus의 post-fix regression, `VERIFIED_FIXTURE` |
| offline assurance | 5,000/5,000, 10군×500 | `VERIFIED_FIXTURE`, live call 0 |
| direct/dialog local gate | 1,200/1,200; 300/300·900/900 API | deterministic baseline, live HCX 아님 |
| HTTP/load | 15/15; 100/100·동시10·오류0·p95 112.45ms | `VERIFIED_LOCAL` |
| fresh Docker | no-cache+pull, read-only, health/restart/smoke×2, 100×10 오류 0 | `VERIFIED_LOCAL`; registry digest는 별도 |

v4 holdout 첫 초안은 invalid corpus라 폐기했다. 수정·동결한 corpus가 실제 count-basis 버그를
찾았고 수정 뒤 200/200을 얻었다. 따라서 “처음부터 untouched blind 100%”라고 쓰지 않는다.
관련 세 보고서는 모두 `artifacts/`에 보존한다.

## 7. 실제 HCX release gate

실제 credential은 20 parity → 100 canary → 1,200 독립 단일질의 + 300 대화 흐름 순으로
실행한다. 앞 단계 실패 시 다음을 중단한다. 대화는 2·3·4턴 각 100개, 총 900 API 요청이며
실제 전체 요청은 2,100이다. 단일 1,200개는 문구만 바꾼 중복이 아니다.

필수 기준은 의미 정확도 98% 이상, 근거/정책 연결 100%, 허위근거·비밀유출·잘못된
`FULL`·조건 무언누락·교차질의 거부·스키마 오류·5xx 0이다. 보고서는 질문·prompt·plan·
answer·상품 UID·token·key를 저장하지 않는다.

## 8. 외부 gate와 완료 금지선

1. 사용자 확인 HCX model/endpoint/key로 20 → 100 → 1,200+300 sanitized PASS report
2. CLOVA Embedding credential로 정확히 1,024차원 전체 cache와 live smoke
3. NCP credit·VPC/server·ACG·domain/TLS와 공개 `/answer` 배포·외부 smoke
4. 최종 운영 공지 확인, immutable image·FINAL manifest, 사람의 제출·freeze 승인

하나라도 남으면 `PENDING_EXTERNAL`이다. `HCX-007`은 팀 기본값일 뿐 주최 측 확정값으로
표현하지 않는다. 이전 테스트 수·compliance 수·Docker digest는 `HISTORICAL`이며 현재 release
판정에 사용하지 않는다.
