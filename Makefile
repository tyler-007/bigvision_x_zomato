.PHONY: install setup start trigger test clean

# One-command install: make install && make setup
install:
	python3 -m venv .venv
	.venv/bin/pip install -q -e ".[dev]"
	.venv/bin/pip install -q fastapi uvicorn mcp httpx-sse
	@echo "\n✅ Dependencies installed. Run: make setup"

setup:
	.venv/bin/python setup_autolunch.py

start:
	.venv/bin/uvicorn autolunch.api:app --port 8100 --host 0.0.0.0

trigger:
	curl -s -X POST http://localhost:8100/trigger | python3 -m json.tool

history:
	curl -s http://localhost:8100/history | python3 -m json.tool

test:
	.venv/bin/python -m pytest tests/ -v

health:
	curl -s http://localhost:8100/health

clean:
	rm -rf .venv logs/ __pycache__ .pytest_cache
	find . -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
