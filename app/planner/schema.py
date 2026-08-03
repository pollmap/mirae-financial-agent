"""HCX-supported QueryPlan schema and local validation prompt.

CLOVA Studio Structured Outputs supports a useful JSON Schema subset.  Regex,
`additionalProperties`, and the stricter cross-field rules remain enforced by
the local Pydantic `QueryPlan` model before any execution.
"""

from __future__ import annotations

from typing import Any

SCOPE_ENUM = ["bond", "domestic_etp", "overseas_etp", "fund"]
INTENT_ENUM = [
    "lookup",
    "search",
    "rank",
    "compare",
    "aggregate",
    "explain",
    "clarify",
    "unsupported",
]

CONDITION_VALUE_SCHEMA: dict[str, Any] = {
    "anyOf": [
        {"type": "string"},
        {"type": "number"},
        {"type": "boolean"},
        {
            "type": "array",
            "maxItems": 30,
            "items": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "number"},
                    {"type": "boolean"},
                ]
            },
        },
    ]
}


HCX_QUERY_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "version": {"type": "string", "enum": ["1.1"]},
        "intent": {"type": "string", "enum": INTENT_ENUM},
        "scopes": {
            "type": "array",
            "items": {"type": "string", "enum": SCOPE_ENUM},
            "minItems": 0,
            "maxItems": 4,
        },
        "entities": {
            "type": "array",
            "maxItems": 10,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "code": {"type": "string"},
                    "scope": {"type": "string", "enum": SCOPE_ENUM},
                },
                "required": ["name", "code", "scope"],
            },
        },
        "filter_groups": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "join": {"type": "string", "enum": ["AND"]},
                    "conditions": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 12,
                        "items": {
                            "type": "object",
                            "properties": {
                                "field": {"type": "string"},
                                "op": {
                                    "type": "string",
                                    "enum": [
                                        "eq",
                                        "ne",
                                        "gt",
                                        "gte",
                                        "lt",
                                        "lte",
                                        "between",
                                        "in",
                                        "contains",
                                        "starts_with",
                                        "is_null",
                                        "is_not_null",
                                    ],
                                },
                                "value": CONDITION_VALUE_SCHEMA,
                                "value2": CONDITION_VALUE_SCHEMA,
                                "unit": {"type": "string"},
                            },
                            "required": ["field", "op", "value", "value2", "unit"],
                        },
                    },
                },
                "required": ["join", "conditions"],
            },
        },
        "groups_join": {"type": "string", "enum": ["AND", "OR"]},
        "metrics": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
        "aggregations": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "function": {"type": "string", "enum": ["count", "sum", "avg", "min", "max"]},
                    "field": {"type": "string"},
                    "alias": {"type": "string"},
                    "distinct": {"type": "boolean"},
                },
                "required": ["function", "field", "alias", "distinct"],
            },
        },
        "sort": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string"},
                    "direction": {"type": "string", "enum": ["asc", "desc"]},
                    "nulls": {"type": "string", "enum": ["last"]},
                },
                "required": ["field", "direction", "nulls"],
            },
        },
        "group_by": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        "assumptions": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
        "needs_clarification": {"type": "boolean"},
        "clarification_question": {"type": "string"},
        "missing_slots": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
        # Flat array instead of an empty-or-2..4 anyOf: anyOf over array shapes
        # is the least portable construct for provider-side constrained
        # decoding, and QueryPlan.semantic_shape already enforces exactly 0 or
        # 2..4 typed options locally before any plan is executed.
        "clarification_options": {
            "type": "array",
            "minItems": 0,
            "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "value": {"type": "string"},
                    "label": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["value", "label", "description"],
            },
        },
    },
    "required": [
        "version",
        "intent",
        "scopes",
        "entities",
        "filter_groups",
        "groups_join",
        "metrics",
        "aggregations",
        "sort",
        "group_by",
        "limit",
        "assumptions",
        "needs_clarification",
        "clarification_question",
        "missing_slots",
        "clarification_options",
    ],
}


QUERY_PLANNER_SYSTEM_PROMPT = """당신은 미래에셋증권 AI Festival 금융상품 Agent의 질의 계획기다.
사용자 질문을 JSON Schema에 맞는 QueryPlan 한 개로만 변환한다.

상품군: bond=국내채권, domestic_etp=국내 ETF/ETN, overseas_etp=해외 ETF/ETN, fund=공모펀드.
허용 기능: lookup/search/rank/compare/aggregate/explain/clarify/unsupported.
상품 선택·수치 계산·SQL 실행은 절대 하지 않고 조건만 구조화한다.
원본에 없는 상품·지표·값·단위·기준일을 만들지 않는다.
미래수익 예측, 수익 보장, 단정적 매수추천, 실시간 데이터 요청은 unsupported로 둔다.
결측을 0으로 바꾸라는 요청도 unsupported로 둔다.
상품군이나 수익률 기간처럼 결과를 바꾸는 조건이 없으면 추정하지 말고 clarify로 둔다.
clarify에는 missing_slots와 2~4개의 짧은 clarification_options를 넣고 이미 파악한 조건은 유지한다.
수익률 기간이 없으면 return_period를 질문한다. ETF인데 국내/해외가 없으면 market을 질문한다.
여러 정렬 metric의 가중치나 우선순위가 없으면 ranking_priority를 질문한다.
metric과 field는 서버의 allowlist 이름만 사용한다. raw SQL, URL, 함수명, 도구명을 출력하지 않는다.
내부 사고과정이나 설명문을 출력하지 않는다."""

QUERY_PLANNER_SYSTEM_PROMPT += """

[Structured Outputs 빈 값 규칙]
이 schema는 HCX 지원 범위에 맞춰 null type을 쓰지 않는다. 선택 문자열이 해당되지 않으면
빈 문자열 ""을 사용한다. entity를 만들 때 name/code 중 하나는 반드시 채우고 scope도 반드시
지정한다. 상품군 자체가 불명확하면 entity를 만들지 말고 clarify한다.
intent=clarify와 needs_clarification=true는 반드시 함께 사용하며, 그 외 intent에서는
needs_clarification=false와 clarification_question=""를 사용한다.
"""

QUERY_PLANNER_SYSTEM_PROMPT += """

[허용 filter field]
product.id, product.name, product.scope(교차 상품군 distinct count의 scope별 OR 분기에만 사용),
product.internal_type, product.market, product.currency, product.trading_currency,
product.manager(ETP만), product.manager_code(펀드 원본 코드), product.issuer(채권 발행기관),
product.country_code(채권 원본 국가코드), bond.kind, fund.domestic_overseas_class,
product.asset_class, product.investment_region,
product.risk_grade, product.sale_status, product.public_private, product.pension_trade_eligible,
product.asset_type, product.region, product.pension_eligible, product.benchmark,
product.strategy(ETP만), bond.exchange, 그리고 아래 허용 metric.

[허용 metric]
bond.issue_amount, bond.issue_date, bond.maturity_date, bond.coupon_rate,
bond.risk_grade, bond.credit_rating, bond.buy_yield, bond.buyable_quantity,
bond.corporate_pretax_yield, bond.corporate_after_tax_yield, bond.after_tax_yield,
bond.preferential_tax_yield, bond.avg_annual_tax_yield, bond.deposit_equivalent_yield_154,
bond.remaining_days_raw, bond.duration, bond.convexity, bond.evaluation_price,
bond.applied_yield, domestic_etp.base_index, domestic_etp.other_expense_ratio,
domestic_etp.expense_ratio, domestic_etp.tracking_error, domestic_etp.discrepancy_rate,
domestic_etp.return_1d,
domestic_etp.return_1m, domestic_etp.return_3m, domestic_etp.return_6m,
domestic_etp.return_1y, domestic_etp.return_ytd, domestic_etp.aum_last,
domestic_etp.nav_last, domestic_etp.net_assets, domestic_etp.close_price,
domestic_etp.volume_1d, overseas_etp.expense_ratio, overseas_etp.return_1d,
overseas_etp.return_1m, overseas_etp.return_3m, overseas_etp.return_6m,
overseas_etp.return_1y, overseas_etp.return_ytd, overseas_etp.aum_last,
overseas_etp.nav_last, overseas_etp.discrepancy_rate, overseas_etp.close_price,
overseas_etp.volume_1d, fund.net_assets, fund.return_1w, fund.return_1m,
fund.return_3m, fund.return_6m, fund.return_18m, fund.return_1y, fund.return_2y,
fund.return_3y, fund.return_5y, fund.risk_grade, fund.expense_ratio.

[해석 규칙]
- ETF라고 명시하면 product.internal_type=ETF, ETN이면 ETN. ETP는 둘 모두다.
- 미국 주식 투자 해외 ETP는 product.asset_type=Equity와
  product.region=United States of America, 국내 ETP의 주식형/미국 투자는
  product.asset_type=주식과 product.region=미국을 쓴다. 국내 ETP의 연금가능은
  product.pension_eligible=Y다. 위험등급 3등급은 국내 ETP에서만
  product.risk_grade=다소높은위험(3등급)으로 쓴다. 다른 scope로 값 라벨을 번역하지 않는다.
- A/Q로 시작하는 국내 상품코드는 domestic_etp, 해외 ticker/ISIN은 overseas_etp다.
- 몇 개인가/수는 aggregate이며 count(product.id distinct=true)를 사용한다.
- 여러 상품군의 단순 상품 수를 각각 묻는 경우에만 metrics 없이
  group_by=[product.scope]로 상품군별 distinct count를 만든다. 금액·수익률·위험 지표는
  이 규칙으로 우회하지 않는다.
- “제공된/값이 있는” 지표 수는 metrics에 그 지표를 넣고 aggregate count를 사용하며,
  assumptions=[metric_count_basis=source_present]를 넣는다. “0보다 큰/유효한” 수와 구분한다.
- 높은/큰/많은 순은 desc, 낮은/작은 순은 asc, nulls는 항상 last다.
- 상위 N이 없으면 limit=10, 최대50이다.
- 원본 행 count는 assumptions에 count_basis=raw, 격리 count는 count_basis=quarantine,
  펀드 raw/serving 속성행 count는 count_basis=fund_attribute를 넣는다.
- 공모/사모는 product.public_private, 판매중/판매완료는 product.sale_status다.
- 장내/장외는 bond.exchange, USD 거래통화는 product.trading_currency다.
- product와 metric을 비교 가능한지 판단하거나 계산하지 말고 요청 구조만 보존한다.
- query에 여러 상품군이 명시되면 scopes에 모두 넣는다. 서버가 비교가능성을 판정한다.
- 수익률·보수·AUM의 미확정 단위나 0 의미를 가정하지 않는다.
- 해외 ETP는 사용 가능한 수익률 기간이 없다. 1일 수익률은 전부 0인 상수이고 나머지
  기간 필드는 없으므로, 기간 미지정 수익률 질문에 1개월/3개월/1년 선택지를 만들지 않는다.
- lookup 이름이 코드가 다른 여러 상품을 가리킬 수 있으므로 하나를 임의 선택하지 않는다.
- compare에는 사용자가 지정한 2개 이상의 entity와 비교 metric을 빠짐없이 넣고, 없으면 clarify한다.
- explain은 원본 상품 필드·전략·벤치마크를 설명할 정확한 entity가 있을 때만 사용한다.
  대상 상품명·코드가 없으면 일반 금융지식을 생성하지 말고 explanation_target을 clarify한다.
- aggregate의 sum/avg/min/max는 반드시 같은 scope의 명시된 numeric metric을 field와 metrics에 넣는다.
- aggregation.alias는 설명문이나 한글이 아니라 소문자 영문으로 시작하는 snake_case로 만든다.
  예: product_count, average_return_1y, maximum_aum. aggregation.function에 정의된 함수 외의
  임의 함수명이나 계산식은 출력하지 않는다.
"""
