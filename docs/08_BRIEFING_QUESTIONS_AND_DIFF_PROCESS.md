# 8월 6일 설명회 질문지·녹취 반영 절차

금융상품 Agent 세션: 2026-08-06 13:00~14:30  
장소: 네이버 그린팩토리 2층 CONNECT HALL  
참석: 팀당 1인

## 1. 현장에서 가장 먼저 확정할 10개

### P0-1. API contract

PDF의 `/answer`, `question_id`, `question`, 다섯 response field는 고정 계약입니까,
아니면 예시입니까? 모든 value를 string으로 보내야 합니까? 추가 field가 허용됩니까?

### P0-2. retrieved_context

원본 row·field를 JSON으로 넣어야 합니까, 자연어 근거 요약이면 됩니까? 최대 길이와
필수 출처 수준은 무엇입니까?

### P0-3. think_trace

내부 추론 원문이 아니라 intent, filter, 사용도구, 계산, 결과 수 같은 execution trace를
제공해도 됩니까? 평가에 필요한 정확한 수준은 무엇입니까?

### P0-4. 호출 규격

timeout, 최대 question 길이, QPS, concurrency, retry 횟수, URL encoding, HTTP status,
인증 또는 IP allow-list 규칙을 알려주실 수 있습니까?

### P0-5. API 운영기간

PDF에는 API 활성화가 09.07~09.20, 전체 예선평가는 09.07~09.30으로 적혀 있습니다.
실제 필수 운영 종료일은 언제입니까?

### P0-6. 제출 후 운영

마감 후 자동 restart, 동일 image로 instance 재기동, 장애 instance 교체, secret rotation,
DNS·certificate 갱신, health-based failover는 허용됩니까?

### P0-7. HyperCLOVA X

허용되는 정확한 model ID·버전·endpoint는 무엇입니까? 지급 credential, credit,
QPM·TPM, 지역, 사용 신청 절차는 어떻게 됩니까?

### P0-8. Codex 개발 사용

제출·평가 runtime의 언어모델을 HCX로만 제한하고 타사 LLM API·SDK·키를 제거한다는
전제에서, 개발 과정에 Codex 같은 코딩 Agent를 사용하는 것은 허용됩니까?

### P0-9. 참고 질의·채점

참고 질의 set 제공 시점과 형식은 무엇입니까? 정답은 상품 ID, 상품명, 자연어 답변 중
무엇으로 채점합니까? 숫자 오차·동률·NULL·정렬 규칙이 있습니까?

### P0-10. ETF·ETN과 펀드 반복구조

국내·해외 ETF 마스터에 ETN이 포함되어 있습니다. ETF 질의에서 ETN을 제외하는 것이
의도입니까? 공모펀드 95,619행은 `itm_no` 11,139개와 속성태그 반복인데, 상품 단위
count·비교는 `itm_no` 축약을 기준으로 보는 것이 맞습니까?

## 2. 데이터 의미 질문

### Metric unit

- 국내·해외 `cu_charge_rt`의 단위는 percentage point입니까?
- 수익률 필드는 percentage point입니까?
- AUM·순자산 금액 단위와 통화는 무엇입니까?
- 채권 수익률·표면금리 단위는 무엇입니까?

### Missing·zero

- 비용·추적오차·괴리율·가격·수익률의 0은 실제 0입니까, 미수집 placeholder입니까?
- `99991231`, `0`, `NULL`의 공식 sentinel 의미가 있습니까?
- 국내 ETF `pd_tr_yn=1`은 거래정지입니까, 거래가능입니까?

### Data time

- 2026-07-11은 파일 추출일이고 일부 시장 field는 6월 14~16일입니다. 답변 기준일은
  field별 update date와 snapshot date를 모두 표시하면 됩니까?

### Product semantics

- 국내 ETF의 `pd_net_tamt`와 `du_last_aum` 중 AUM 공식 우선필드는 무엇입니까?
- 펀드의 `rptt_ksd_itm_no`는 동일 펀드 family·share class 식별자로 사용할 수 있습니까?
- `prfd_attr_cd` 228종의 공식 codebook이 있습니까? sample의 `axis_*`로 의미를 추정해도
  됩니까, 아니면 코드 그대로만 써야 합니까?
- 공모펀드 테이블의 사모 표기 상품은 공모 질의 universe에서 제외해야 합니까?
- sample sheet의 `axis_*`는 전체 데이터 분류의 ground truth입니까, 예시 파생값입니까?
- ETP master와 공모펀드 master에 경제적으로 같은 상장지수상품이 함께 보일 때 중복
  상품으로 병합해야 합니까, 서로 다른 source record로 유지해야 합니까?
- 해외 ETP의 `pd_curr_cd`, `pd_trd_ccy`, AUM/NAV/price의 metric currency 의미를 각각
  어떻게 해석해야 합니까?

## 3. 상품군 교차 질의 질문

- 주최 측이 기대하는 대표 교차질의와 비교 metric은 무엇입니까?
- 국내 ETF와 펀드의 동일 기간수익률 비교가 의도된 범위입니까?
- 서로 다른 통화 AUM은 환산을 요구합니까, 통화별 분리를 요구합니까?
- 채권 신용등급과 펀드·ETF 위험등급을 통합할 공식 mapping이 있습니까?
- 해외 ETF에 장기수익률·위험등급이 없을 때 `확인할 수 없음`이 정답 정책입니까?

## 4. 모델·retrieval 질문

- 제3자 embedding·reranker도 허용됩니까, Naver 계열만 허용됩니까?
- 평가 server에서 외부 인터넷·외부 API 호출이 허용됩니까?
- 외부 데이터는 정성평가에서만 보조적으로 쓰는 것이 권장됩니까?
- HCX structured output·function calling 중 권장 패턴이 있습니까?
- 모델 장애 시 deterministic parser·안전 응답을 허용합니까?

## 5. 제출·제안서 질문

- 제출 마감 정확한 시각은 09.06 23:59가 맞습니까?
- 기술제안서 파일형식·페이지·용량 제한이 있습니까?
- 원본 데이터와 변환 artifact를 private repo에 포함해야 합니까?
- 대용량 파일 링크의 만료·접근권한 조건은 무엇입니까?
- Dockerfile은 필수입니까, 동등한 재현환경이면 됩니까?
- API spec은 OpenAPI 파일과 별도 문서 중 어떤 형식이 좋습니까?
- `/health` endpoint나 운영 연락망 제출이 필요합니까?
- 공식 AI Festival·미래에셋증권·HyperCLOVA X 로고를 제안서·데모 UI에 사용할 수
  있습니까? 공식 원본·브랜드 가이드가 제공됩니까?

## 6. 현장 기록 템플릿

각 답변을 아래 형식으로 기록합니다.

```text
[시간]
[질문 ID]
[답변자/소속]
[원문 답변]
[예/아니오/조건부]
[공식 확정 수준: 공지 예정 / 현장 확정 / 권장 / 개인 의견]
[영향 문서]
[영향 코드]
[후속 확인]
```

화면·슬라이드에만 나온 조건은 사진 번호와 슬라이드 제목을 함께 기록합니다.

## 7. 녹취 수령 후 처리 절차

### Step 1. 원문 보존

- 원본 음성·녹취를 수정하지 않고 보존
- 파일 hash
- 녹취 시간대·세션·발화자 메타데이터

### Step 2. 교정 전사

- 발화 순서 유지
- 금융·API·HCX·데이터 용어 교정
- 들리지 않는 부분은 임의 복원하지 않고 `[청취불가]`
- 화면참조는 `[슬라이드/시연 참조]`

### Step 3. Requirement extraction

각 문장을 다음으로 분류합니다.

- `MUST`
- `MUST_NOT`
- `SHOULD`
- `MAY`
- `EXAMPLE`
- `FUTURE_NOTICE`
- `OPINION`

### Step 4. Source diff

```text
requirement_id
topic
PDF baseline
official web baseline
team email baseline
briefing statement
status: unchanged|clarified|changed|new|conflict
implementation impact
test impact
owner
```

### Step 5. Contract freeze

- API schema
- HCX model·credential
- data universe
- metric meaning
- 운영기간·마감 후 조치
- 참고 질의·채점방식

### Step 6. Plan update

영향받는 문서·JSON Schema·테스트·ADR만 변경합니다. 설명회 전 문서를 통째로
재작성하지 않습니다.

## 8. 설명회 종료 시 확보해야 하는 결과

- P0 10개 질문의 답변 또는 공식 추후공지 약속
- 정확한 API 계약 상태
- 정확한 HCX 사용 조건
- 데이터의 0·NULL·단위 핵심 의미
- ETF·ETN과 펀드 반복구조 처리 기준
- 평가·운영기간·마감 후 장애조치 기준
- 참고 질의 set 수령경로
- 담당 Q&A 채널과 후속문의 방법
