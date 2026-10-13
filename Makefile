.PHONY: install test lint format run mcp rag-index rag-eval clean

PYTHON ?= python
PIP ?= $(PYTHON) -m pip
RUFF ?= $(PYTHON) -m ruff
PYTEST ?= $(PYTHON) -m pytest
UVICORN ?= $(PYTHON) -m uvicorn

install:
	$(PIP) install -e ".[dev]"

test:
	$(PYTEST)

lint:
	$(RUFF) check .
	$(RUFF) format --check .

format:
	$(RUFF) check . --fix
	$(RUFF) format .

run:
	$(UVICORN) app.main:app --host 127.0.0.1 --port 8000 --reload

mcp:
	$(PYTHON) -m app.mcp.server

rag-index:
	$(PYTHON) -m app.rag.index_cli

rag-eval:
	$(PYTHON) -m app.rag.evaluation --dataset data/evaluation/rag_evaluation.v1.json --reranker lexical

clean:
	rm -rf __pycache__ build dist *.egg-info .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
