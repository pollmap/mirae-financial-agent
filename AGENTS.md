# Codex Project Rules - 미래에셋증권 금융상품 Agent

> **Read `docs/18_FINAL_MASTER_PLAN_AND_RELEASE_READINESS.md`,
> `docs/17_OFFICIAL_CONFORMANCE_AND_ADVERSARIAL_ASSURANCE.md`,
> `HANDOFF_CURRENT_STATUS.md`, and `docs/16_MASTER_PROJECT_NARRATIVE.md`
> first, before anything below.** This file predates the 2026-08-06 briefing
> and the federated-semantic-rebaseline that followed it (branch
> `briefing-rebaseline-v2`). Most of it is still binding project policy, but
> §5's "do not start with ... GraphDB" line is now stale -- the project
> *did* add a Knowledge Graph (`etl/kg.py`) once the briefing made it a
> required tech-spec item, and §10's paths/commands predate that work too.
> Where this file and the three documents above disagree, they are newer and
> win.

## 1. Source of truth

Apply sources in this order.

1. Organizer-provided task PDF and data ZIP in `inputs/`
2. Later organizer briefing transcript, written notices, and team-specific notices
3. Official festival homepage and official FAQ
4. Team engineering decisions

Never convert an example, assumption, or team decision into an official rule.
Every material requirement must use one of these labels:

- `OFFICIAL_PDF`
- `OFFICIAL_DATA`
- `OFFICIAL_TEAM_EMAIL`
- `OFFICIAL_WEB`
- `PDF_EXAMPLE`
- `BRIEFING_CONFIRMED`
- `OPEN_QUESTION`
- `TEAM_DECISION`

When two official sources differ, record both, use the newer and more specific
notice operationally, and keep the conflict open until confirmed.

## 2. Immutable sources

- Never edit organizer-provided PDF, ZIP, or XLSX files.
- Store SHA-256 hashes and verify them before every data build.
- Resolve sources from `artifacts/source_manifest.json`; never rely on Unicode display
  filenames or first-match glob selection.
- Preserve raw values, source filenames, sheet names, Excel row numbers, and row hashes.
- Keep raw, clean, canonical, and serving layers separate.
- Never repair a damaged source row silently. Preserve it and quarantine it with a reason.

## 3. Model compliance

- The submitted and evaluated runtime may use only HyperCLOVA X as its language model.
- Never add another LLM provider as a fallback, evaluator, summarizer, router, or judge.
- Codex is a development tool only. Submitted runtime code, image, environment, and
  configuration must not call OpenAI, Anthropic, Google, or another LLM provider.
- Embedding and reranking choices remain `OPEN_QUESTION` until the briefing policy is
  incorporated, even though the public FAQ says non-language-model areas are unrestricted.
- A HyperCLOVA X outage must fail closed, use a deterministic non-LLM path where valid,
  or return a controlled unavailable response.

## 4. Product boundary

Build a grounded financial-product finder and analyst, not a general investment adviser.

Required product flow:

`natural-language question -> typed QueryPlan -> validated deterministic execution -> evidence -> safe answer -> public GET API`

The deterministic engine, not the LLM, chooses products and calculates numbers.

Never:

- invent products, values, dates, units, or sources;
- treat missing data as zero;
- compare incompatible periods, units, currencies, or risk scales;
- present snapshot data as real-time data;
- forecast unsupported returns;
- issue definitive investment recommendations;
- reveal hidden chain-of-thought.

If the data cannot answer a request, state that limitation or ask for the missing condition.

## 5. MVP priority

Priority order:

1. All four organizer datasets and reproducible ETL
2. Lookup, search, filter, rank, compare, aggregate, and limited cross-product queries
3. Field-level evidence and answerability rules
4. HyperCLOVA X structured planning
5. Public GET API and clean Docker E2E
6. Regression, blind, safety, load, and fault tests
7. Technical proposal and demo materials
8. UI and optional enrichment only after the API passes the release gate

Do not start with multi-agent orchestration, a large UI, portfolio optimization, or
live-market integration.

**(2026-08-08 update)** The "do not start with ... GraphDB" clause that used to
be here no longer applies: the 2026-08-06 briefing made Ontology/Knowledge
Graph/Federated Retrieval/two-stage planning required tech-spec items, and
the project implemented them (`etl/kg.py`, `app/retrieval/`,
`app/semantics/`) rather than treating the MVP priority order above as a
reason to skip them. The distinction that still holds is "graph database
*product*" (Neo4j etc., genuinely unneeded at this data scale) versus "graph
*relationship model*" (needed once the briefing required it, implemented
inside the same single DuckDB file). See `docs/16_MASTER_PROJECT_NARRATIVE.md`
for why and `docs/04_PRODUCT_ARCHITECTURE_SPEC.md`'s 2026-08-08 correction.

## 6. Contract discipline

- Treat `/answer` and the five response fields in the PDF as a provisional compatibility
  profile because the PDF calls them a schema example.
- Isolate request and response adapters so the briefing contract can be changed quickly.
- Use allow-listed canonical fields, metrics, operators, scopes, and limits.
- Never execute raw SQL, URLs, arbitrary expressions, or tool names produced by the model.
- Use parameterized SQL and deterministic tie-breaking.
- Use Decimal for financial calculations and version every formula and rounding rule.
- Redact GET query strings from access logs/APM and return `Cache-Control: no-store`.
- When a present source field has no field-level as-of, keep `as_of_date=null`, set
  `as_of_status=DATASET_SNAPSHOT_ONLY`, and state that the individual date was not provided.
  Use answerability `UNAVAILABLE` only when the requested source field/value itself is absent;
  never infer a date.
- Return an execution audit summary in `think_trace`; do not expose private model reasoning.

## 7. Quality gates

No feature is complete until all applicable checks pass:

- source hash and official row/column reconciliation;
- schema and type validation;
- unit, period, currency, missing, zero, and sentinel policies;
- golden QueryPlan and deterministic result tests;
- every answer claim maps to evidence;
- forbidden-answer and hallucination tests;
- fresh Docker build and real HTTP E2E;
- no secret or non-HCX runtime dependency;
- public endpoint health, restart, and logging verification.

## 8. Submission freeze

- Create an immutable release manifest containing Git SHA, image digest, source hashes,
  data version, prompt version, schemas, HCX model ID, and non-secret configuration hash.
- Finish production configuration and restart policy before the official deadline.
- After submission, do not commit, push, redeploy, change prompts/data/code, or perform any
  action that changes results. The PDF states that detected changes result in disqualification.
- Keep the API available through 2026-09-30 unless a later official notice resolves the
  difference between the PDF's 09.07-09.20 API window and 09.07-09.30 evaluation period.

## 9. Working method

- Keep `docs/02_REQUIREMENTS_BASELINE.md` and `docs/08_BRIEFING_QUESTIONS_AND_DIFF_PROCESS.md`
  current before implementation changes.
- Record meaningful design choices as ADR entries in `docs/04_PRODUCT_ARCHITECTURE_SPEC.md`.
- When the briefing transcript arrives, transcribe it, extract requirement statements,
  produce a source-by-source diff, update labels, then implement.
- Preserve unrelated user work and never rewrite organizer files.

## 10. Current executable baseline

- The repository now contains a fully implemented, federated-semantic-rebaselined MVP on
  branch `briefing-rebaseline-v2` (not `main`). Read `HANDOFF_CURRENT_STATUS.md` and
  `docs/16_MASTER_PROJECT_NARRATIVE.md` first; `CODEX_MASTER_PROMPT.md` and
  `docs/11_IMPLEMENTATION_HANDOFF.md` describe the pre-rebaseline (2026-08-03) state only.
- Reuse `app/`, `etl/`, `registry/`, `app/semantics/`, `app/retrieval/`, and the existing
  tests before adding parallel systems.
- **On Windows**, the Makefile's `PYTHON ?= .venv/bin/python` targets assume WSL/Linux and
  do not resolve from plain Windows Git Bash/PowerShell -- call
  `.venv/Scripts/python.exe -m ...` directly instead (e.g.
  `.venv/Scripts/python.exe -m pytest -q`, `.venv/Scripts/python.exe -m ruff check ...`).
- Standard local gate: source verify -> `scripts/build_data.py --no-parquet` (now also
  builds the KG and lexical index stages) -> full pytest -> `scripts/scan_runtime_compliance.py`.
- Full-source ETL gate: `pytest -q tests/test_etl.py` (and `tests/unit/test_kg.py` for the
  Knowledge Graph build stage specifically).
- Real HTTP gate: start the app, then run `scripts/e2e_smoke.py --base-url http://127.0.0.1:8080`.
- **Also run** (added by the rebaseline, not optional): `python -m eval.run_eval` (640-question
  independent-oracle harness) and `python -m eval.metamorphic` (paraphrase-invariance check).
  `eval/` is excluded from the Docker runtime image but is part of the git repo and the
  required local gate.
- Development/test may use the deterministic non-LLM parser. Production must fail unless
  `APP_ENV=production`, `PLANNER_MODE=hcx`, an HCX key, and a non-default clarification signing
  key are present -- verified empirically this session (the runtime image genuinely refuses to
  start without a key rather than silently degrading).
- Any new gold or policy expectation must become an executable assertion, not only prose.
- **Trust nothing without executing it.** This project's own history is the argument for this
  rule: an eval harness that silently under-graded cross-scope correctness reported a false
  100% for weeks before an adversarial review caught it by re-deriving the check instead of
  trusting its output. See `docs/16_MASTER_PROJECT_NARRATIVE.md` for the full account.
