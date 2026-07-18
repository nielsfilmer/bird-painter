# Deterministic checks for this repo. `make review-checks` is THE wrapper the
# senior-dev review runs (allowlisted in .claude/settings.json as the one
# narrow command, per CLAUDE.md's permission policy — instead of opening the
# whole interpreter/npx surface).

VENV := .venv/bin

.PHONY: lint test review-checks

lint:
	$(VENV)/ruff check bird_painter tests

test:
	$(VENV)/pytest -q

review-checks: lint test
