# eval — independent evaluation harness

Scores the live agent service against an **independent DuckDB SQL oracle**.
The oracle (`eval/oracle.py`) never imports anything from `app/` — expected
answers are recomputed from the serving database with direct read-only SQL,
so an engine bug cannot silently validate itself.

## Layout

| File | Role |
|---|---|
| `templates.py` | Deterministic generator of 640 question specs (no randomness, no clock, no DB access). |
| `oracle.py` | Expected-answer computation via direct `duckdb.connect(..., read_only=True)`; lazy connection. |
| `run_eval.py` | Async driver: builds the in-process service (deterministic planner), fills runtime code slots, asks every question, scores vs the oracle, writes a JSON report. |
| `metamorphic.py` | Paraphrase-invariance checker over the template variant groups. |

## Question mix (640 total)

| kind | count | expectation |
|---|---|---|
| `lookup_code` | 60 | code lookup resolves to the exact `product_uid` (codes filled at runtime: first product ids per scope ordered by `product_uid`) |
| `rank_single` | 234 | exact ordered uid list — mirror SQL: valid quality states, common latest as-of, `value_num` order, `product_uid ASC` tie-break, `LIMIT n` |
| `filter_search` | 114 | exact first-n uid list under catalog filters (`ORDER BY product_uid`) |
| `count_aggregate` | 83 | exact `COUNT(DISTINCT product_uid)` (incl. per-scope group counts) |
| `cross_scope` | 69 | no refusal (`answerability != INCOMPARABLE` and results, or disclosure) **and** the `[교차 상품군 응답` disclosure line; unified-rank agreement recorded as info |
| `compare` | 40 | both runtime-filled codes present in the evidence items |
| `safety_block` | 20 | `SAFETY_LIMITED` / `UNAVAILABLE` |
| `ambiguous` | 20 | `NEEDS_CLARIFICATION` today (specs note the planned one-shot follow-up mode) |

## Run

Prerequisite: the serving DB exists (`data/serving/mirae_agent.duckdb`,
built by `scripts/build_data.py --no-parquet`). Do not run while a rebuild is
writing the file. From the repo root on Windows:

```powershell
# smoke run: first 50 questions
./.venv/Scripts/python.exe -m eval.run_eval --limit 50

# full run (~640 questions, deterministic planner, no network)
./.venv/Scripts/python.exe -m eval.run_eval

# custom DB / output paths
./.venv/Scripts/python.exe -m eval.run_eval --database data/serving/mirae_agent.duckdb --out artifacts/eval_report.json

# paraphrase invariance (groups of 2-3 rephrasings must return identical uid lists)
./.venv/Scripts/python.exe -m eval.metamorphic --limit 20
./.venv/Scripts/python.exe -m eval.metamorphic
```

Programmatic use:

```python
import asyncio
from eval.run_eval import run
report = asyncio.run(run("data/serving/mirae_agent.duckdb", limit=50))
```

## Reports

`artifacts/eval_report.json`:

- `accuracy` overall and `by_kind` totals/accuracy
- `rank_position_match_mean` — Kendall-ish positional agreement for rank questions
- `cross_scope_refusal_rate` — fraction of cross-scope questions that were refused
  (target: 0.0; the contract is "answer with disclosure", never refuse)
- `disclosure_rate` — fraction of multi-scope `cross_scope` questions carrying the
  `[교차 상품군 응답` disclosure line (target: 1.0). Per-scope count questions use a
  different mandated disclosure and are excluded from this rate.
- `failures` — up to 50 entries with `{id, question, expected, got}`

`artifacts/metamorphic_report.json`: `groups_total`, `groups_invariant`,
`invariance_rate`, and up to 50 violating groups with per-member uid lists.

## Determinism & independence guarantees

- `templates.generate()` uses no randomness and no clock: identical output every call.
- Runtime code slots are sampled `ORDER BY product_uid LIMIT n` — stable across runs
  on the same DB build.
- The oracle opens its read-only connection lazily on first query, never at import.
- The oracle duplicates contract constants (valid quality states, risk-grade labels,
  Korean filter values) on purpose: sharing them with `app/` would let a bad change
  rewrite the expectations to match itself.

## Known intentional gap probes

Some templates are designed to fail until the service grows the capability, so the
report tracks them instead of hiding them:

- `filter_search` "투자 지역이 글로벌" — no planner phrase mapping today.
- `cross_scope` 순자산 (net assets) union — no cross binding in the planner; the
  accepted behavior is a split listing with disclosure (scored as behavior, not rank).
- `compare`/`lookup_code` on scopes whose product ids the deterministic planner
  cannot tokenize measure identifier coverage on purpose.
