# 평가 API 계약 초안

상태: `PROVISIONAL`  
목적: PDF 예시와 최대한 호환하되 설명회 후 adapter만 교체

## 1. 공식 확정과 잠정 결정을 분리

### `OFFICIAL_PDF`

- 평가자는 GET 요청을 사용합니다.
- Public 망에서 접속 가능한 평가 API가 필요합니다.
- Endpoint URL과 요청·응답 JSON 명세를 제출해야 합니다.
- 지정 운영기간에 API를 활성화해야 합니다.

### `PDF_EXAMPLE`

- 경로 `/answer`
- query parameter `question_id`, `question`
- response field `question_id`, `question`, `retrieved_context`, `think_trace`, `answer`

PDF가 `스키마 예시`라고 표현하므로 정확한 고정 계약 여부는 아직 확정할 수 없습니다.

### `TEAM_DECISION`

설명회 전에는 예시를 그대로 따릅니다. request/response adapter를 core service와 분리해
설명회 뒤 경로·필드·타입·오류규칙을 즉시 바꿀 수 있게 합니다.

서명된 역질문 상태를 이어가기 위해 `clarification_token`, `clarification_response` 두
선택 parameter를 확장했습니다. 이 두 field는 PDF 예시에 없는 팀 결정이며, 최종 평가기가
추가 parameter를 허용하는지는 `OPEN_QUESTION`입니다.

## 2. Provisional endpoint

```http
GET /answer?question_id=Q-001&question={UTF-8 URL-encoded question}
Accept: application/json
```

### 입력

| 필드 | 잠정 타입 | 필수 | 규칙 |
|---|---|---|---|
| question_id | string | 예 | 1~200자, 원문 그대로 echo, 공백만 금지 |
| question | string | 예 | UTF-8, 1~2,000자, 공백만 금지 |
| clarification_token | string | 아니오 | 최대 10,000자; 이전 명확화 응답의 서명 상태 |
| clarification_response | string | 아니오 | 최대 500자; 사용자의 추가 조건 |

`clarification_token`과 `clarification_response`는 **함께 있거나 함께 없어야** 합니다.
`contracts/provisional-api-request.schema.json`은 JSON Schema 2020-12의
`dependentRequired`로 이 규칙을 강제하고, 실제 service도 한쪽만 온 요청을 400으로
거부합니다. 후속 요청에서도 PDF 예시 호환을 위해 `question_id`, `question`은 계속 필수입니다.

```http
GET /answer?question_id=Q-001&question={original-question}
  &clarification_token={signed-token}&clarification_response={selected-condition}
```

중복 `question_id`는 idempotency key가 아니라 평가 식별자로 취급합니다. domain-result
cache key는 정규화된 question, release, data·ETL·registry·schema·prompt·renderer·safety
policy, HCX model version의 조합입니다. `question_id` echo가 섞이지 않도록 최종 HTTP
응답 전체는 cache하지 않습니다.

GET query string이 access log·proxy·APM에 남지 않도록 production command에서 access log를
끄고, `Cache-Control: no-store`를 기본값으로 둡니다. proxy·APM redaction과 percent-encoding
후 URI byte 제한은 배포 환경에서 별도로 검증해야 하는 운영 gate입니다.

## 3. Provisional success response

```json
{
  "question_id": "Q-001",
  "question": "해외 티커 EES의 상세 정보를 알려줘.",
  "retrieved_context": "{...compact evidence JSON...}",
  "think_trace": "intent=lookup; scope=overseas_etp; entity_code=EES; result_count=1; answerability=FULL",
  "answer": "EES는 WisdomTree US SmallCap Fund입니다. 제공 데이터 스냅샷(2026-07-11)의 PREF02N001에서 확인했습니다."
}
```

설명회 전 호환 profile은 정확히 다섯 key와 string value를 사용합니다. 이는
`TEAM_DECISION`이며 공식 고정 계약으로 표현하지 않습니다.

## 4. retrieved_context

사람이 읽을 수 있으면서 기계적으로 검사 가능한 compact JSON string을 권장합니다.

```json
{
  "data_version": "2026-07-11",
  "answerability": "FULL",
  "reason_code": null,
  "result_count": 1,
  "coverage": null,
  "aggregates": [],
  "items": [
    {
      "product_uid": "GLOBAL_ETP:PREF02N001:EES",
      "name": "WisdomTree US SmallCap Fund",
      "source_table": "PREF02N001",
      "source_excel_row": 2,
      "fields": [
        {
          "metric": "product.name",
          "source_field": "pd_nm",
          "value": "WisdomTree US SmallCap Fund",
          "unit": null,
          "as_of_date": null,
          "as_of_status": "DATASET_SNAPSHOT_ONLY"
        }
      ]
    }
  ],
  "limitations": ["상품명 필드의 개별 기준일은 원본에 없어 데이터셋 스냅샷만 표시"]
}
```

필수 정보:

- data version과 field-level as-of. 값은 있으나 개별 날짜가 없으면
  `as_of_date=null`·`DATASET_SNAPSHOT_ONLY`, 필드·값 자체가 없으면 answerability
  `UNAVAILABLE`
- 결과 상품 ID와 이름
- source table·row·field
- raw·normalized value
- 단위
- 계산식 또는 정렬 규칙
- 유효 모집단과 제외 사유
- 데이터 한계

Renderer는 Evidence Bundle의 첫 limitation만 고르지 않고 모든 blocking limitation을 중복
제거해 `answer`에 표시합니다. 다중 metric 결과는 요청한 모든 metric과 각 결측 상태를
함께 표시합니다. `explain`은 exact product target이 있을 때 source-backed 상품사실·원본
전략·benchmark만 반환하고, target이 없으면 정상 200 `NEEDS_CLARIFICATION` 응답을 사용합니다.

## 5. think_trace

`think_trace`에는 내부 chain-of-thought를 넣지 않습니다. 다음 execution audit만 넣습니다.

- intent
- scopes
- 적용 filter
- 사용 metric
- 정렬·집계 방식
- 사용 도구와 serving view
- result count
- answerability status
- 처리시간 구간
- transport retry 발생 여부와 fallback 미사용 확인

예:

```text
intent=rank; scopes=domestic_etp; filters=internal_type:ETF, region:미국;
metrics=return.1y,aum; sort=return.1y:desc,aum:desc; result_count=5;
answerability=PARTIAL_WITH_COVERAGE; planner=HCX; executor=DuckDB
```

설명회에서 raw reasoning을 요구하더라도 안전하고 감사 가능한 execution trace로 충족할
수 있는지 확인합니다.

## 6. 정상적인 비결과 응답

다음은 HTTP 성공 응답으로 반환하는 것이 잠정적으로 합리적입니다.

- 결과 없음
- 조건 부족으로 명확화 필요
- 데이터에 metric 없음
- 비교 불가능
- 안전정책상 전망·단정 추천 제한

다섯 필드는 유지하고 `answer`에 사유와 가능한 대안을 설명합니다.

```json
{
  "question_id": "Q-002",
  "question": "해외 ETF 중 1년 수익률이 가장 높은 상품을 알려줘.",
  "retrieved_context": "{\"data_version\":\"2026-07-11\",\"answerability\":\"UNAVAILABLE\",\"reason_code\":\"SOURCE_FIELD_ABSENT\",\"coverage\":null,\"aggregates\":[]}",
  "think_trace": "intent=rank; scope=overseas_etp; metric=overseas_etp.return_1y; answerability=UNAVAILABLE",
  "answer": "제공된 해외 ETF 마스터에는 1년 수익률 필드가 없어 해당 순위를 확인할 수 없습니다. AUM(단위 제한 표시)·가격·거래량 기준 비교는 가능합니다."
}
```

## 7. 오류 정책 초안

설명회 확인 전 내부 adapter 기본값입니다.

| 상황 | 잠정 status | 처리 |
|---|---:|---|
| 필수 parameter 없음 | 400 | 구조화된 input error |
| 공백 question | 400 | 구조화된 input error |
| 과도한 question 길이 | 400 | FastAPI validation error object |
| clarification pair 한쪽만 제공 | 400 | `INVALID_CLARIFICATION` error object |
| 정상 no-result | 200 | 정상 5필드 응답 |
| clarify·unsupported | 200 | 정상 5필드 응답 |
| HCX transport/validation 불가 | 503 | 정상 5필드 shape의 controlled unavailable |
| 내부 실행 실패 | 500 | request id와 release version 로그 |

현재 400 응답은 PDF 예시 5필드가 아니라 `error`, `detail` 두 field object입니다. 평가기가
2xx만 수집하는지, 400/503의 정확한 schema와 retry를 어떻게 해석하는지 확인한 후 고정합니다.

## 8. Contract adapter interface

```text
OrganizerRequestAdapter
  parse_http_request()
  validate_contract()
  to_domain_question()

CoreAgentService
  answer(domain_question) -> DomainAnswer

OrganizerResponseAdapter
  from_domain_answer()
  validate_response_schema()
  serialize_json()
```

이 구조를 사용하면 QueryPlan·데이터 엔진·Evidence는 그대로 두고 외부 contract만
바꿀 수 있습니다.

## 9. 설명회 확정 항목

- 정확한 path와 parameter 이름
- 다섯 response field 고정 여부
- field type과 additional field 허용 여부
- retrieved_context 형식·길이
- think_trace 기대 수준
- 오류 schema와 status code
- timeout·retry·concurrency·QPS
- 인증·IP allow-list
- question 최대 길이와 encoding
- 역질문 후속 parameter 허용 여부와 `dependentRequired` pair
- Markdown·표 허용 여부
- health endpoint 제출 여부
