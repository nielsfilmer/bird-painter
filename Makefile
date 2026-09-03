# Deterministic checks for this repo. `make review-checks` is THE wrapper the
# senior-dev review runs (allowlisted in .claude/settings.json as the one
# narrow command, per CLAUDE.md's permission policy — instead of opening the
# whole interpreter/npx surface).

VENV := .venv/bin

# A throwaway instance for QA / manual poking: off-port, its own archive, no
# microphone, and no FAL_KEY, so it paints placeholder plates and spends
# nothing. Override with `make qa-up QA_PORT=8610`.
QA_PORT ?= 8600
QA_ARCHIVE ?= /tmp/bp-qa-archive
QA_LOG ?= /tmp/bp-qa.log
QA_PID ?= /tmp/bp-qa.pid

.PHONY: lint test test-js review-checks run qa-up qa-down

lint:
	$(VENV)/ruff check bird_painter tests scripts

test:
	$(VENV)/pytest -q

# The wall's layout maths (bird_painter/static/layout.js) — guarded by
# node --test so a density/overlap regression fails here, not by hand.
# Skips gracefully if node isn't installed (Python checks still run).
test-js:
	@if command -v node >/dev/null 2>&1; then \
		node --test bird_painter/static/*.test.js; \
	else \
		echo "test-js: node not found — skipping JS layout tests"; \
	fi

review-checks: lint test test-js

# The wall itself, in the foreground — the normal way to run this thing.
run:
	$(VENV)/python -m bird_painter

# Start / stop the throwaway instance. These exist so the recurring chore of
# hosting an instance is ONE narrow allowlisted command instead of a raw
# interpreter invocation (CLAUDE.md's permission policy: a repo wrapper, not an
# open `python`/`npm run` surface).
qa-up:
	@BP_ARCHIVE_DIR=$(QA_ARCHIVE) BP_ENABLE_LISTENER=false BP_PORT=$(QA_PORT) \
		FAL_KEY= nohup $(VENV)/python -m bird_painter --no-prompt \
		> $(QA_LOG) 2>&1 & echo $$! > $(QA_PID)
	@sleep 4
	@echo "qa instance on http://127.0.0.1:$(QA_PORT) (pid $$(cat $(QA_PID)), log $(QA_LOG))"

qa-down:
	@if [ -f $(QA_PID) ]; then kill $$(cat $(QA_PID)) 2>/dev/null; rm -f $(QA_PID); \
		echo "qa instance stopped"; else echo "no qa instance running"; fi
