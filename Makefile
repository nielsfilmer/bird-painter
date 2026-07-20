# Deterministic checks for this repo. `make review-checks` is THE wrapper the
# senior-dev review runs (allowlisted in .claude/settings.json as the one
# narrow command, per CLAUDE.md's permission policy — instead of opening the
# whole interpreter/npx surface).

VENV := .venv/bin

.PHONY: lint test test-js review-checks

lint:
	$(VENV)/ruff check bird_painter tests

test:
	$(VENV)/pytest -q

# The wall's layout maths (bird_painter/static/layout.js) — guarded by
# node --test so a density/overlap regression fails here, not by hand.
# Skips gracefully if node isn't installed (Python checks still run).
test-js:
	@command -v node >/dev/null 2>&1 \
		&& node --test bird_painter/static/*.test.js \
		|| echo "test-js: node not found — skipping JS layout tests"

review-checks: lint test test-js
