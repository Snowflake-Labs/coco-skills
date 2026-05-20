# Entity Resolution Integration Tests — 3-Phase Structure
#
# Phase 1 (setup)    — Create source tables in Snowflake from fixture SQL
# Phase 2 (invoke)   — Invoke the skill via cortex -p (slow, LLM-driven)
# Phase 3 (validate) — Verify the Snowflake objects the skill created
#
# Functional Tests (domain profiles):
#   make setup-pharma     — Phase 1 only for pharma
#   make invoke-pharma    — Phase 2 only for pharma
#   make validate-pharma  — Phase 3 only for pharma
#   make pharma           — All 3 phases for pharma
#   make setup-all        — Phase 1 for all domains
#   make invoke-all       — Phase 2 for all domains
#   make validate-all     — Phase 3 for all domains
#   make all              — All 3 phases, all domains
#
# Benchmark Tests (academic ER datasets — see INTEGRATION-TESTING.md):
#   make bench-setup-dblp-acm       — Load DBLP-ACM benchmark data
#   make bench-invoke-dblp-acm      — Run all techniques on DBLP-ACM
#   make bench-validate-dblp-acm    — Evaluate DBLP-ACM results (P/R/F1)
#   make bench-dblp-acm             — All 3 phases for DBLP-ACM
#   make bench-setup-all            — Load all benchmark datasets
#   make bench-invoke-all           — Run all techniques on all datasets
#   make bench-validate-all         — Evaluate all benchmark results
#   make bench-all                  — All 3 phases, all benchmarks
#
# Cleanup:
#   make clean            — Drop the ephemeral test schema
#
# Environment:
#   Set SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD, etc.
#   or use:  make pharma ENV=.env
#   which loads the .env file via uv's --env-file flag.

ENV ?= .env
UV_RUN = uv run --env-file $(ENV)
PYTEST = $(UV_RUN) pytest -v --tb=short

DOMAINS = pharma financial healthcare retail generic
BENCHMARKS = dblp-acm dblp-scholar walmart-amazon amazon-google abt-buy

.PHONY: all clean $(DOMAINS) \
        setup-all invoke-all validate-all \
        $(addprefix setup-,$(DOMAINS)) \
        $(addprefix invoke-,$(DOMAINS)) \
        $(addprefix validate-,$(DOMAINS)) \
        bench-all bench-setup-all bench-invoke-all bench-validate-all \
        $(addprefix bench-,$(BENCHMARKS)) \
        $(addprefix bench-setup-,$(BENCHMARKS)) \
        $(addprefix bench-invoke-,$(BENCHMARKS)) \
        $(addprefix bench-validate-,$(BENCHMARKS)) \
        sync

# ── Per-domain: all 3 phases ─────────────────────────────────────────────

pharma: setup-pharma invoke-pharma validate-pharma
financial: setup-financial invoke-financial validate-financial
healthcare: setup-healthcare invoke-healthcare validate-healthcare
retail: setup-retail invoke-retail validate-retail
generic: setup-generic invoke-generic validate-generic

# ── Phase 1: setup ───────────────────────────────────────────────────────

setup-all: $(addprefix setup-,$(DOMAINS))

setup-pharma:
	$(PYTEST) tests/phase1_setup/test_setup_pharma.py

setup-financial:
	$(PYTEST) tests/phase1_setup/test_setup_financial.py

setup-healthcare:
	$(PYTEST) tests/phase1_setup/test_setup_healthcare.py

setup-retail:
	$(PYTEST) tests/phase1_setup/test_setup_retail.py

setup-generic:
	$(PYTEST) tests/phase1_setup/test_setup_generic.py

# ── Phase 2: invoke skill ───────────────────────────────────────────────

invoke-all: $(addprefix invoke-,$(DOMAINS))

invoke-pharma:
	$(PYTEST) tests/phase2_invoke/test_invoke_pharma.py

invoke-financial:
	$(PYTEST) tests/phase2_invoke/test_invoke_financial.py

invoke-healthcare:
	$(PYTEST) tests/phase2_invoke/test_invoke_healthcare.py

invoke-retail:
	$(PYTEST) tests/phase2_invoke/test_invoke_retail.py

invoke-generic:
	$(PYTEST) tests/phase2_invoke/test_invoke_generic.py

# ── Phase 3: validate results ───────────────────────────────────────────

validate-all: $(addprefix validate-,$(DOMAINS))

validate-pharma:
	$(PYTEST) tests/phase3_validate/test_validate_pharma.py

validate-financial:
	$(PYTEST) tests/phase3_validate/test_validate_financial.py

validate-healthcare:
	$(PYTEST) tests/phase3_validate/test_validate_healthcare.py

validate-retail:
	$(PYTEST) tests/phase3_validate/test_validate_retail.py

validate-generic:
	$(PYTEST) tests/phase3_validate/test_validate_generic.py

# ── All domains, all phases ──────────────────────────────────────────────

all: setup-all invoke-all validate-all

# ════════════════════════════════════════════════════════════════════════
# Benchmark Integration Tests — Academic ER Datasets
# See INTEGRATION-TESTING.md for dataset details and methodology.
# ════════════════════════════════════════════════════════════════════════

# ── Per-benchmark: all 3 phases ────────────────────────────────────────

bench-dblp-acm: bench-setup-dblp-acm bench-invoke-dblp-acm bench-validate-dblp-acm
bench-dblp-scholar: bench-setup-dblp-scholar bench-invoke-dblp-scholar bench-validate-dblp-scholar
bench-walmart-amazon: bench-setup-walmart-amazon bench-invoke-walmart-amazon bench-validate-walmart-amazon
bench-amazon-google: bench-setup-amazon-google bench-invoke-amazon-google bench-validate-amazon-google
bench-abt-buy: bench-setup-abt-buy bench-invoke-abt-buy bench-validate-abt-buy

# ── Benchmark Phase 1: setup ──────────────────────────────────────────

bench-setup-all: $(addprefix bench-setup-,$(BENCHMARKS))

bench-setup-dblp-acm:
	$(PYTEST) tests/phase1_setup/test_setup_bench_dblp_acm.py

bench-setup-dblp-scholar:
	$(PYTEST) tests/phase1_setup/test_setup_bench_dblp_scholar.py

bench-setup-walmart-amazon:
	$(PYTEST) tests/phase1_setup/test_setup_bench_walmart_amazon.py

bench-setup-amazon-google:
	$(PYTEST) tests/phase1_setup/test_setup_bench_amazon_google.py

bench-setup-abt-buy:
	$(PYTEST) tests/phase1_setup/test_setup_bench_abt_buy.py

# ── Benchmark Phase 2: invoke skill ───────────────────────────────────

bench-invoke-all: $(addprefix bench-invoke-,$(BENCHMARKS))

bench-invoke-dblp-acm:
	$(PYTEST) tests/phase2_invoke/test_invoke_bench_dblp_acm.py

bench-invoke-dblp-scholar:
	$(PYTEST) tests/phase2_invoke/test_invoke_bench_dblp_scholar.py

bench-invoke-walmart-amazon:
	$(PYTEST) tests/phase2_invoke/test_invoke_bench_walmart_amazon.py

bench-invoke-amazon-google:
	$(PYTEST) tests/phase2_invoke/test_invoke_bench_amazon_google.py

bench-invoke-abt-buy:
	$(PYTEST) tests/phase2_invoke/test_invoke_bench_abt_buy.py

# ── Benchmark Phase 3: validate results ───────────────────────────────

bench-validate-all: $(addprefix bench-validate-,$(BENCHMARKS))

bench-validate-dblp-acm:
	$(PYTEST) tests/phase3_validate/test_validate_bench_dblp_acm.py

bench-validate-dblp-scholar:
	$(PYTEST) tests/phase3_validate/test_validate_bench_dblp_scholar.py

bench-validate-walmart-amazon:
	$(PYTEST) tests/phase3_validate/test_validate_bench_walmart_amazon.py

bench-validate-amazon-google:
	$(PYTEST) tests/phase3_validate/test_validate_bench_amazon_google.py

bench-validate-abt-buy:
	$(PYTEST) tests/phase3_validate/test_validate_bench_abt_buy.py

# ── All benchmarks, all phases ─────────────────────────────────────────

bench-all: bench-setup-all bench-invoke-all bench-validate-all

# ── Cleanup ──────────────────────────────────────────────────────────────

clean:
	@if [ -f .test_schema ]; then \
		SCHEMA=$$(cat .test_schema); \
		echo "Dropping schema $$SCHEMA ..."; \
		$(UV_RUN) python3 -c "import os, snowflake.connector; \
			c=snowflake.connector.connect( \
				account=os.environ['SNOWFLAKE_ACCOUNT'], \
				user=os.environ['SNOWFLAKE_USER'], \
				password=os.environ['SNOWFLAKE_PASSWORD'], \
				warehouse=os.environ.get('SNOWFLAKE_WAREHOUSE','COMPUTE_WH'), \
				database=os.environ.get('SNOWFLAKE_DATABASE','ENTITY_RESOLUTION_TEST'), \
				role=os.environ.get('SNOWFLAKE_ROLE'), \
				authenticator=os.environ.get('SNOWFLAKE_AUTHENTICATOR','snowflake')); \
			c.cursor().execute('DROP SCHEMA IF EXISTS $$SCHEMA CASCADE'); \
			print('Dropped $$SCHEMA'); c.close()"; \
		rm -f .test_schema; \
	else \
		echo "No .test_schema file found — nothing to clean."; \
	fi

# ── Dependency sync ──────────────────────────────────────────────────────

sync:
	uv lock
