# Claude project notes — bird-painter

> The universal half of this (workflow + review discipline) also lives in the
> author's global `~/.claude/CLAUDE.md` so it auto-applies; this file is the
> self-contained, per-repo instantiation (so it travels to other machines /
> teammates who don't share that global file). Keep the two from drifting.
>
> **Source of truth for product / architecture is `PLAN.md`.** Read it before
> making changes — it pins the concept, pipeline, stack, and v0 config.

Persistent context for future Claude sessions on this repo. Read this first.

---

## Workflow (mandatory)

Every task ends with a pull request. Do **not** push directly to `main`.

1. **Work on a feature branch** — branch off `main` with a short descriptive
   name (e.g. `add-x`, `fix-y`).
2. **Commit and push the branch**, then open a PR against `main` via
   `gh pr create`. Title is concise; description summarises the change and
   flags anything the reviewer should look at.
3. **Spawn the review agents — in parallel.** Two reviewers look at the PR at
   once; both must come back clean, and their findings are amendments under
   step 4. **Prefer the `/review-loop` skill when it's available** — it runs
   steps 3–4 end-to-end (parallel spawn, app hosting for QA, the two-round
   cap, durable capture of deferred remarks), and in a repo with this file it
   uses this repo's prompt templates — the `review-prompts` project skill — so
   the static-analysis pass still applies — instead of hand-rolling the
   orchestration. Hand-rolled fallback:
   use the `Agent` tool (`subagent_type: "general-purpose"`) for each.
   - **Senior-developer code review.** Framed as a senior dev reviewing the PR;
     give it the project goals (point it at `PLAN.md` and this file) and have it
     run a **static-analysis pass** (the repo's own linters/type-checkers/SAST,
     scoped to the diff), a **spec-fidelity pass** (the diff against the
     originating issue/spec: missing/partial requirements, scope creep,
     implemented-but-wrong), and the **smell baseline** (a fixed set of Fowler
     code smells as judgement calls), folding all three into its findings — the
     full prompt lives in the `review-prompts` project skill. It posts via
     `gh pr review N -R nielsfilmer/bird-painter --comment` (or
     `--request-changes` if its gh account is allowed — GitHub blocks
     self-review on your own PR, so it falls back to `--comment`; flag blocking
     items explicitly in the body then).
   - **QA agent — client-facing / visually-testable changes only.** Spawn it
     alongside the code reviewer. **The QA subagent can't start a server /
     long-running app itself** (interpreters and `npm run`-style commands are
     denied non-interactively in the subagent sandbox), so a QA agent told to
     "run the app" boots nothing. *Before* spawning it, the orchestrating agent
     **hosts a running instance** (build it first if needed; run this app with
     `.venv/bin/python -m bird_painter <port>` after `.venv/bin/pip install -e .`
     — for QA use an off-port and a throwaway archive dir via
     `BP_ARCHIVE_DIR=/tmp/bp-qa-archive`), captures
     a screenshot, and hands the QA agent **both the live URL and the screenshot
     path** (capture the screenshot before spawning — this harness has no
     "message a running agent" tool, so it's a single launch). The QA agent then
     drives that instance (hit its endpoints / drive it with a browser if the QA
     agent has one) and reads the screenshot, confirming the change **visually +
     functionally**, not just by reading the diff:
     - **Frontend:** pixel-perfect against the design reference (none chosen
       yet — record it here when one exists): spacing, colour, type, the right
       states — and that it actually works: the happy path plus the specific
       change.
     - **Plus what a QAer normally tests:** edge cases, empty/loading/error
       states, invalid input + boundaries, and regressions in adjacent features
       — plus responsive/mobile, keyboard + a11y, and reconnect *where a live
       browser is available* (else verify the wiring from the served output +
       diff and say which you couldn't exercise).
     - Posts findings to the PR like the reviewer. **Skip** when the change
       isn't client-facing or isn't testable in the front end (backend /
       library / config / docs) — say so in the notification.
4. **Address every amendment the review and QA raise before notifying the user —
   including non-blocking nits.** "LGTM with a nit" is not done; fix it,
   re-review on the new commit, notify only when fully clean.
   - **Two-round cap on *novel* nits.** Prime the round-2 reviewer with the
     round-1 review so it verifies the specific fixes. New nits in round 2 →
     notify the user now and mention them. The cap is on novel nits, not
     re-attempts: "you fixed it, but inadequately" is still the round-1 nit.
   - **Code-quality, doc, and naming nits: fix without asking** — that's what
     the reviewer is for.
   - **Only bounce to the user for a product/UX decision** — user-facing copy,
     a default value, behaviour visible in the UI. Internal naming, logs, code
     comments, developer-facing wording are NOT product/UX decisions; fix them.
   - **Off-topic nits → a follow-up issue / separate PR** (one PR = one
     concern). Mention the spawn in the user notification.
   - **Capture every deferred remark the moment you triage it — never leave it
     only in the review thread.** Any remark you are *not* fixing in this PR
     (out-of-scope, later-phase, watch-item, won't-fix-now, observation) must be
     written to a durable tracker as you process the review, before notifying
     the user: a new `follow-up` issue (milestoned), a comment on the relevant
     issue, or — for an in-file caveat — a code comment. A review comment or a
     commit-message line is **not** durable tracking. Default: "if it was worth
     the reviewer raising, it's worth an issue." Mention the filed items in the
     notification.
   Push follow-up commits to the same PR branch; don't open a second PR for
   review fixes on this PR's stated concern.
5. **Merge the PR yourself once it's clean, then notify.** The user removed
   themselves as merge gate for this repo permanently (2026-07-18, see the
   decision log). Merge clean, fully-reviewed PRs (`gh pr merge`) and notify;
   don't hold them for human merge. The review loop (steps 3–4) still gates a
   merge — only genuinely-clean PRs go in.

### Review-agent prompt templates

The full senior-dev review-prompt and QA-prompt templates live in the
**`review-prompts` project skill** (`.claude/skills/review-prompts/SKILL.md`)
— extracted there so this always-loaded file stays lean while the templates
load only at review time. They are the source of truth for the review passes
(static-analysis, spec-fidelity, `PLAN.md` constraints, smell baseline) and
the QA brief; the step-3 bullets above only summarise them. Their repo-static
`<PLACEHOLDER>`s are filled at instantiation, from this file and the
`PLAN.md` it names; only the per-review values (`#N`, `<URL>`,
`<SCREENSHOT PATH>`) get filled when spawning the agents.

The TBD values in this file (how to run the app, the design reference, the
review-checks wrapper) are mirrored in that skill's templates — when one gets
pinned, update **both** this file and the corresponding spot in
`.claude/skills/review-prompts/SKILL.md` in the same change.

### Workflow disciplines

- **One PR = one concern.** Don't tack an orthogonal change onto an open PR;
  branch off `main` for it.
- **No personal info in public docs.** This repo is **public** — strip names,
  emails, account IDs, secrets, "contact me" sections before opening a PR.
- **Update file maps** (README tree / the "File map" below) whenever a file is
  added or removed from a tracked directory.
- **Update the decision log** when a workflow / scope / architecture decision
  changes. Annotate superseded entries so history stays navigable.
- **Teach-it-once.** When the user states a workflow rule, a correction, or a
  standing preference in conversation, write it into this file (or the decision
  log / memory, whichever is the durable home) **in the same turn**, and say
  so in the reply. Being re-taught the same rule in a later session is a
  process bug, not a user quirk. If an unrelated PR is in flight, the rule
  still lands the same turn — as its own micro-commit/PR off `main` (or in
  memory when a PR would be disproportionate); never folded into the unrelated
  PR (one PR = one concern). In-session overrides of a workflow gate
  (e.g. "skip me as merge gate") count double: record the override, with its
  scope, in the same durable home immediately — or it silently expires with
  the session.
- **Third-party dashboard handoffs happen as one batched checklist.** When
  steps must happen in an external web console (hosting panel, Vercel, DNS,
  OAuth app setup…), collect ALL the user-side steps into a single numbered
  checklist, link the provider's official docs instead of narrating their UI
  from memory (menus drift; wrong walkthroughs cost more than no walkthrough),
  and state exactly what the user should paste back — confirmations and
  non-secret IDs only, never a credential the console mints (see the next
  bullet). No step-at-a-time ping-pong.
- **No secrets in chat.** Never ask the user to paste a secret value (API key,
  password, token) into the conversation — transcripts persist it. Have them
  put it in an env file / keychain themselves and reference the path. A
  `! command` run in-session works ONLY if the command text doesn't contain or
  print the secret (its input and output land in the transcript too): e.g.
  `! read -s KEY && echo "KEY=$KEY" >> .env`, or a paste from the clipboard /
  password manager — otherwise do the step outside the session entirely. If a
  secret does land in chat, say so and recommend rotation.
- **Phase progress lives on GitHub, not in a roadmap doc.** Each phase gets a
  milestone (`Phase N — <name>`) and a `phase-tracker`-labelled issue. The
  roadmap states scope; live state comes from milestones/issues — no `- [ ]`
  checkboxes in the roadmap. When a review surfaces a later-phase task, open a
  `follow-up`-labelled issue against the right milestone. Open a phase's tracker
  first; close the tracker + milestone together to mark the phase done.
  - **Starting a phase includes decomposing it.** When a phase's scope spans
    more than a couple of PRs, break it into **tracer-bullet issues** under the
    milestone — vertical slices, each demoable/verifiable on its own and sized
    to one PR (one PR = one concern), never a horizontal layer. Create them in
    dependency order, each stating **"Blocked by: #N"** for the issues that
    gate it (use GitHub's native blocked-by relationship where available; a
    no-blocker issue can start immediately). Agree the slice granularity and
    blocking edges with the user before filing. The tracker issue's
    definition-of-done then *references* those issues instead of restating them
    as checkboxes (file the slices, then edit the tracker body to link them —
    the tracker opens first, before slice numbers exist), and work proceeds
    along the frontier: any open issue whose blockers are all closed. (Adapted
    from mattpocock/skills' `to-tickets`; wide mechanical refactors are the
    exception — sequence those expand–contract: add the new form beside the
    old, migrate call sites in batches, remove the old form — rather than
    forcing a vertical slice.)
  - Tooling: **`/status`** (runs `scripts/status.sh`) prints the live per-phase
    snapshot; **`/phase`** does the lifecycle write ops (`start` = milestone +
    tracker issue + scope decomposition per above, `complete` = close both
    together, `follow-up` = file a deferred task). `scripts/status.sh` is
    allowlisted in `.claude/settings.json` as `Bash(bash scripts/status.sh)`.
- **Permission-friction habits.** Multi-line bodies go through `--body-file` /
  a temp file, never inline in the command (inline multi-line `--body` trips
  approval prompts). Avoid compound `cd X && …` commands — use absolute paths
  so the permission matcher sees one operation.
- **Permission patterns split across global vs project `settings.json` by
  shape:**
  - **Non-aggressive, narrow-scope** (read-only subcommands, single-purpose ops
    whose primary purpose isn't destruction — `Bash(git log:*)`, `Bash(mkdir:*)`,
    `Bash(cp:*)`, `Bash(tar:*)`, `Bash(touch:*)`) → **global** `~/.claude/settings.json`.
  - **Aggressive** (broaden trust across a tool's whole subcommand surface —
    `Bash(git:*)`, `Bash(gh pr:*)`, `Bash(gh issue:*)`) →
    **project** `.claude/settings.json`.
  - **Real security risks AND destructive-by-design — never allowlist**, keep
    `ask`: interpreters (`Bash(node:*)`, `Bash(python:*)`, `Bash(bash:*)` …),
    wildcard package runners (`Bash(npx:*)`, `Bash(npm run:*)` …), shell/remote
    (`Bash(eval:*)`, `Bash(ssh:*)`, `Bash(rsync:*)`), privilege/secret ops
    (`Bash(sudo:*)`, `Bash(gh api:*)`, `Bash(gh auth:*)`, `Bash(gh secret:*)`),
    destructive-by-design (`Bash(rm:*)`, `Bash(dd:*)`, `Bash(shred:*)`).
  - **Workflow gates persist regardless of allowlist** — `gh pr:*` technically
    includes `gh pr merge`/`close`, but "user is the merge gate" still applies.
    The behavioural rules here are the safety net for permissions broader than
    the behaviour we actually want.
- **The reviewer runs deterministic tooling, not just its judgment.** LLM review
  is unreliable at exactly what linters/type-checkers/SAST are reliable at; the
  senior-dev review must run the repo's own checks on the diff and fold them in
  (deduped, PR-introduced-only, auto-nits fixed without asking) — see the
  Static-analysis pass in the `review-prompts` skill. A check it couldn't run is
  reported as a gap, never skipped silently. The policy-consistent way to cut the
  resulting permission prompts is a single narrow repo wrapper (e.g.
  `Bash(make review-checks)` in the project allowlist), not opening the whole
  `npx`/`npm run`/interpreter surface. This repo has **no toolchain yet** —
  when one lands (linter, type-checker, tests), add the wrapper and record it
  here. Alongside it, the review's spec-fidelity pass and smell baseline (both
  adapted from mattpocock/skills' two-axis `code-review`, MIT) cover what
  tooling can't: delivery against the originating spec, and design judgement
  calls.
- **The senior-dev review skips vendored-asset directories by default.** None
  exist in this repo yet. If one is added (a design import, an SDK snapshot,
  third-party tokens), name it here and paste the skip paragraph from the
  template into the review prompt.

---

## What this project is

**bird-painter** is an ambient installation. A local microphone listens
outdoors; BirdNET recognizes which bird species are singing right now; each
newly-heard species is painted by a hosted image model (FLUX `schnell` on
fal.ai) in a fixed vintage-naturalist style; the paintings show on a
full-screen local "wall" and fade out after a few hours so the wall stays a
fresh reflection of what's been heard. **Local ears, cloud brush** — one
Python process on the mic machine runs capture → BirdNET → image API call →
archive + live set → serves the wall (FastAPI). Only the image call leaves the
house. Audience: personal toy, not a public product. Full detail — pipeline,
component choices, v0 config knobs, scope, risks — in `PLAN.md`. Repo:
`nielsfilmer/bird-painter` (public).

## File map

- `README.md` — project stub.
- `pyproject.toml` — Python package + deps (FastAPI/uvicorn/dotenv).
- `.env.example` — env template (FAL_KEY + knob overrides); copy to `.env`.
- `bird_painter/` — the one local service:
  - `config.py` — knobs (defaults = PLAN.md v0 table, env-overridable).
  - `store.py` — permanent archive (files + `meta.jsonl`) + ephemeral live
    view + per-species `last_painted_at` (the cooldown key).
  - `brush.py` — the brush: species → fal FLUX `schnell` REST call →
    painting bytes; house-style prompt template; soft-failure policy.
  - `ears.py` — the ears: BirdNET via `birdnetlib`; `detect_file` /
    `detect_samples` → `Detection`s above the confidence floor.
  - `detect_cli.py` — `python -m bird_painter.detect_cli <clip> [floor]`,
    prints detections (demo/verify the ears).
  - `capture.py` — `MicListener`: records rolling 48 kHz mono windows from
    the mic and feeds each to the ears; soft-failure loop.
  - `listen_cli.py` — `python -m bird_painter.listen_cli`, live mic →
    printed detections (ears + mic only, no painting).
  - `gate.py` — `TriggerGate`: the paint-or-not decision — per-species TTL
    cooldown (via the store) + rolling per-hour cap.
  - `runner.py` — `PaintRunner`: detections → gate → brush → store; the
    callback the mic feeds. Only a successful paint consumes a cap slot.
  - `placeholder.py` — SVG placeholder plates (used when FAL_KEY unset).
  - `web.py` — FastAPI app: wall page, `/api/live`, `/images/*`, `/dev/paint/*`.
  - `static/index.html` — the wall (polling, fade in/out).
  - `__main__.py` — `python -m bird_painter [port]` (port: CLI arg → `BP_PORT`
    → default 8537; `--list-devices` lists mics; sets up INFO logging so the
    listener heartbeat surfaces): runs the
    whole loop — wall + live mic listener painting heard birds (disable the
    mic with `BP_ENABLE_LISTENER=false` for wall-only / tests / QA).
- `.gitignore` — excludes `.claude/settings.local.json` (machine-local
  permission grants; public repo).
- `CLAUDE.md` — this file: per-repo workflow + context.
- `PLAN.md` — product/architecture source of truth (concept, pipeline, stack,
  v0 config, scope, risks, decision log).
- `scripts/status.sh` — live per-phase status snapshot from GitHub
  milestones/issues (backs the `/status` skill).
- `.claude/settings.json` — project permission allowlist.
- `.claude/skills/review-prompts/SKILL.md` — review + QA prompt templates for
  workflow step 3.

## Decision log / source of truth

`PLAN.md` is the canonical product/architecture doc; product/architecture
decisions land there as a dated decision-log section. This file's log covers
workflow/process decisions:

- **2026-07-18** — Project bootstrapped from `claude-project-template`.
- **2026-07-18** — Concept + stack pinned via design grilling; written to
  `PLAN.md` (ambient installation, local Python service, BirdNET + fal FLUX
  `schnell`, ephemeral wall). `PLAN.md`'s own decision log holds the product
  details.
- **2026-07-18** — **User removed themselves as merge gate for this repo,
  permanently.** Claude merges clean, fully-reviewed PRs itself and notifies
  (workflow step 5). Scope: PR merges only; the review loop still gates. Mirror
  of the `crypto-trader` arrangement. Also recorded in global `~/.claude/CLAUDE.md`.

## Phase trackers

Convention: one milestone per phase (`Phase N — <name>`) + one
`phase-tracker`-labelled issue opened at phase start; both closed together at
phase end. Deferred work → `follow-up`-labelled issues against the right
milestone. Current anchor: **Phase 0 — Scaffold** (see the `phase-tracker`
issue under that milestone).
