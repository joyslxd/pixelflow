PIXELFLOW_CONFIG_ENV ?= dev
PYTHON ?= uv run python

install:
	uv sync

dev:
	PIXELFLOW_CONFIG_ENV=$(PIXELFLOW_CONFIG_ENV) PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 $(PYTHON) -m app.gateway.run --reload

gateway:
	PIXELFLOW_CONFIG_ENV=$(PIXELFLOW_CONFIG_ENV) PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 $(PYTHON) -m app.gateway.run

gateway-prod:
	PIXELFLOW_CONFIG_ENV=prod PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 $(PYTHON) -m app.gateway.run

test:
	PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run pytest tests/ -v

test-blocking-io:
	PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run pytest tests/blocking_io -q --tb=short

lint:
	uvx ruff check .
	uvx ruff format --check .

format:
	uvx ruff check . --fix && uvx ruff format .

detect-blocking-io:
	@PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run python ../scripts/detect_blocking_io_static.py --output ../.deer-flow/blocking-io-findings.json
