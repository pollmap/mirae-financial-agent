PYTHON ?= .venv/bin/python
PIP ?= .venv/bin/pip

.PHONY: setup verify build-data test test-fast lint run smoke load-smoke hcx-mock-contract \
	compliance production-preflight production-readiness release-manifest

setup:
	python3 -m venv .venv
	$(PIP) install --disable-pip-version-check -r requirements-dev.txt

verify:
	$(PYTHON) scripts/verify_sources.py

build-data: verify
	$(PYTHON) scripts/build_data.py --no-parquet

test-fast:
	$(PYTHON) -m pytest -q tests/unit tests/contract tests/integration tests/test_hcx.py

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m ruff check app deploy etl scripts tests

run:
	APP_ENV=development PLANNER_MODE=deterministic $(PYTHON) -m uvicorn app.main:app --host 127.0.0.1 --port 8080 --no-access-log

smoke:
	$(PYTHON) scripts/e2e_smoke.py --base-url http://127.0.0.1:8080

load-smoke:
	$(PYTHON) scripts/load_smoke.py --base-url http://127.0.0.1:8080 --requests 100 --concurrency 10

hcx-mock-contract:
	$(PYTHON) -m pytest -q tests/contract/test_hcx_app_e2e.py

compliance:
	$(PYTHON) scripts/scan_runtime_compliance.py

production-preflight:
	$(PYTHON) scripts/production_preflight.py

production-readiness:
	$(PYTHON) scripts/production_preflight.py --check-http

release-manifest:
	$(PYTHON) scripts/generate_release_manifest.py
