PYTHON ?= python3.11

.PHONY: install lint type unit readme-check test

install:
	pip install -e ".[dev]"

lint:
	ruff check src tests

type:
	mypy --strict src/ceiling

unit:
	pytest --cov=ceiling --cov-report=term-missing --cov-fail-under=85 tests

readme-check:
	$(PYTHON) scripts/readme_check.py

test: lint type unit readme-check
