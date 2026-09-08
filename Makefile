PYTHON ?= python3.11

.PHONY: install lint type unit test

install:
	pip install -e ".[dev]"

lint:
	ruff check src tests

type:
	mypy --strict src/ceiling

unit:
	pytest --cov=ceiling --cov-report=term-missing tests

test: lint type unit
