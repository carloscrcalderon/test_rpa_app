.PHONY: check format lint test

check:
	uv run ruff format --check src tests
	uv run ruff check src tests
	uv run pylint src
	uv run pytest

format:
	uv run ruff format src tests

lint:
	uv run ruff check src tests
	uv run pylint src

test:
	uv run pytest
