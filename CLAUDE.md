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
   **Review and QA agents always run on Opus** (`model: "opus"` on the Agent
   call) regardless of what model the main session runs — user instruction
   2026-07-27, standing.
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
   - **Decide product/UX calls yourself too — the user is on auto mode**
     (instruction 2026-08-04, standing, supersedes the earlier "bounce
     product/UX decisions to the user"). User-facing copy, defaults, visible
     behaviour: pick the option you'd defend, state the choice and the
     reasoning in the notification, and keep going. Internal naming, logs, code
     comments and developer-facing wording were never worth asking about
     anyway. Ask only when proceeding either way would be unsafe or
     hard-to-reverse, or when the answer genuinely can't be inferred (a fact
     only the user has — an account, a physical constraint, a preference with
     no defensible default). A stream of permission prompts is the failure
     mode being corrected here, not thoroughness.
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
  - **Use the file tools, not the shell, for file work** (user complaint
    2026-08-04: "you are still asking for permission all the time"). `Read`,
    `Edit`, `Write`, `Grep`, `Glob` ride the session's edit mode and prompt
    rarely; `python3 - <<PY … PY` heredocs, `sed -i`, `cat`, `head`, `tail` and
    `grep` are Bash — and interpreters are deliberately never allowlisted, so
    every such patch is its own approval dialog. Editing files by shelling out
    to Python is the single biggest source of prompt spam.
  - **Stay in the main checkout.** A `git worktree` outside the repo root puts
    every path beyond the reach of the allowlisted `make` wrappers, forcing raw
    `.venv/bin/pytest --rootdir=…` / `.venv/bin/ruff <path>` invocations that
    each prompt. Use a branch and `git stash`; reach for a worktree only when
    two branches genuinely must be live at once, and expect the friction.
  - **Recurring chores get a `make` target, then an allowlist entry** — that's
    the policy-consistent way to cut prompts (never a blanket `Bash(python:*)`).
    Present targets: `review-checks` / `lint` / `test` / `test-js`, `run`
    (the wall), `qa-up` / `qa-down` (a throwaway instance on an off-port with
    its own archive, no mic, no key — what the QA agent gets pointed at).
  - **One browser origin, forever: `http://127.0.0.1:8600`** (user complaint
    2026-08-04, with a screenshot of "Allow Claude to act on
    http://192.168.1.244:8621?"). The Browser pane approves access
    **per origin**, so every new host:port asks again — and a session that
    hosts instances on 8600, 8601, 8603, 8604, 8605, 8620, 8621 asks seven
    times. `make qa-up` defaults to 8600 for exactly this reason: point the
    browser there and nowhere else. **Never browse the LAN address** — check
    off-machine behaviour with `curl` (no approval), which is also the honest
    test, since what's being verified is the peer address, not the rendering.
  - **"Auto mode" is not blanket approval.** The session's accept-edits mode
    covers file edits, not Bash; this file's own policy keeps interpreters,
    package runners and destructive commands at `ask` on purpose. The way to
    fewer prompts is fewer shell calls, not a wider allowlist.
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
  `npx`/`npm run`/interpreter surface. **This repo's wrapper is
  `make review-checks`** (= `make lint`, ruff + `make test`, pytest — see the
  Makefile), allowlisted in `.claude/settings.json`; the senior-dev review
  runs it on every PR. Alongside it, the review's spec-fidelity pass and smell
  baseline (both
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

- `README.md` — public README: what it is, quickstart, hardware pointer,
  and the Licenses section (code MIT; BirdNET model CC BY-NC-SA / non-commercial).
- `LICENSE` — MIT (repo code).
- `pyproject.toml` — Python package + deps (FastAPI/uvicorn/dotenv).
- `.env.example` — env template (FAL_KEY + knob overrides); copy to `.env`.
- `bird_painter/` — the one local service:
  - `api_docs.py` — the API's self-description: one structure describing
    every endpoint + WebSocket event (with examples), served as JSON by
    `/api` and rendered by `/api/docs`.
  - `clip_clean.py` — `enhance(...)`: makes an archived detection clip
    listenable — spectral subtraction against the clip's own noise profile,
    band-limiting to the band the bird actually occupies (found per clip from
    which bins CHANGE, not which are loudest), then normalise + soft-limit.
    Fail-soft: returns the raw samples on anything unexpected.
  - `config.py` — knobs (defaults = PLAN.md v0 table, env-overridable).
  - `store.py` — permanent archive (files + `meta.jsonl`) + ephemeral live
    view + per-species `last_painted_at` (the cooldown key).
  - `brush.py` — the brush: species → fal FLUX `schnell` REST call →
    painting bytes; house-style prompt template; soft-failure policy.
  - `ears.py` — the ears: BirdNET via `birdnetlib`; `detect_file` /
    `detect_samples` → `Detection`s above the confidence floor. Optional
    location filter (lat/lon); the SEASONAL half of it is opt-in
    (`BP_SEASONAL_FILTER`) because it drops out-of-week singers silently.
  - `detect_cli.py` — `python -m bird_painter.detect_cli <clip> [floor]`,
    prints detections (demo/verify the ears).
  - `capture.py` — `MicListener`: records rolling 48 kHz mono windows from
    the mic and feeds each to the ears; soft-failure loop. Also
    `device_name()` / `list_input_devices()` — the shared mic-device helpers
    (used by both CLIs).
  - `listen_cli.py` — `python -m bird_painter.listen_cli`, live mic →
    printed detections (ears + mic only, no painting); prints the selected
    input device + how to change it (`BP_INPUT_DEVICE`), and
    `--list-devices` lists mics.
  - `events.py` — `EventHub` + the event shapes for the live detection stream
    (`/ws/detections`): thread-safe fan-out from the mic thread to WebSocket
    subscribers, bounded per-client queues, small replay backlog.
  - `gate.py` — `TriggerGate`: the paint-or-not decision — per-species TTL
    cooldown (via the store) + rolling per-hour cap.
  - `runner.py` — `PaintRunner`: detections → gate → brush → store; the
    callback the mic feeds. Only a successful paint consumes a cap slot.
  - `night.py` — night mode (#122): `NightSchedule` (local hours, may wrap
    midnight; same hour twice = never), `Backlight` (percent in, sysfs
    integers out), `NightWatch` (a daemon thread; writes the backlight ONLY
    on transitions, so a hand adjustment is not fought; `is_night` is what
    `/api/live` reports and the page dims itself on). The day level is the
    panel's own as seen by day, or `BP_NIGHT_DAY_BRIGHTNESS`; a start inside
    the window reads the panel only while it is still brighter than the
    night level, so a restart after the dim can't take 20% for "day".
    `watch_from_config` drives `BP_NIGHT_BACKLIGHT` or the first
    `/sys/class/backlight/*` (a laptop has one too and would be dimmed; this
    Mac and the recorder's headless Pi have none, so there the watch keeps
    state only).
  - `unit.py` — the table model's own settings and network (#123): the one
    reader/writer of `~/.config/bird-painter/unit.conf` (CAPTION, UI,
    MAX_LIVE, ROTATE — the install script's file) and of the `.env` lines
    the screen may change (BP_WALL_MAX_LIVE, BP_NIGHT_*; the key is never
    touched — merges keep every other line byte for byte). `LiveSettings`
    is the runtime copy the routes read (the frozen Config stays what the
    process started with); `apply` writes both files and it. `connectivity`
    / `join` / `forget` / `reboot` wrap `nmcli` and `systemctl reboot` as
    argv lists (an SSID is touchscreen input), under the polkit rule the
    install script grants the unit's user. Paths override with
    `BP_UNIT_CONF` / `BP_ENV_FILE` so a QA instance never writes into a
    developer's home.
  - `occasions.py` — occasion hats: public-holiday table + `hat_for(...)`;
    personal days come only from env (`BP_HAT_DAYS`/`BP_HAT_DATES`), never
    committed (public repo).
  - `placeholder.py` — SVG placeholder plates (used when FAL_KEY unset).
  - `plate_check.py` — `describe_problem(...)`: is this a bird on white, or has
    the model drifted (a photo of a painting on a desk, a flat block)? Two
    measures calibrated over the whole archive — white margin per side, and the
    flattest colour's share OF THE SUBJECT (padding-invariant, so it reads the
    same before and after `trim`). Returns the reason, not a bool; fail-soft
    (anything unreadable is kept).
  - `trim.py` — `trim_to_bird(...)`: crops the flat-white margin off a painting
    at store time (padded back to 4:5) so the bird fills its plate; fail-soft
    on SVG placeholders/unreadable files.
  - `web.py` — FastAPI app via `create_app(config)` factory (no import-time
    side effects; uvicorn uses `factory=True`): wall page, `/api/live`,
    `/api/archive`, `/wall.png`, `/api/layout` (the placement `/wall.png`
    draws, as JSON, for any viewport — what the table model's browser wall
    fetches with `?style=panel` so it places birds exactly as the frame does;
    `?caption=` scales the panel's fixed-size type THROUGH the plan, so the
    reserve grows with it — the 7" runs 1.5),
    `/images/*` (`?bare=1` for the bird as the frame pastes it — ink crop,
    ground keyed to alpha — which is what panel mode shows), `/audio/*`
    (`?download=1` for an
    attachment), `/ws/detections` (live event stream), `/api` + `/api/docs`
    (the API's own documentation), `/unit` GET/PUT + `/unit/wifi` +
    `/unit/wifi/join|forget` + `/unit/reboot` (the settings screen's API,
    loopback-only like `/dev`), `/dev/paint/*` (loopback only, via the
    `LocalOnly` ASGI guard on `LOCAL_ONLY_PREFIXES` — `/dev`, and `/unit`
    for #123 — 404 from
    off-machine; it skips the cap and spends money).
  - `wall_layout.py` — Python port of `static/layout.js`'s `computeCollage`
    (the collage placement maths), so `/wall.png` places birds identically to
    the live wall. A parity test keeps the two in sync.
  - `fonts.py` — the house serif's candidate paths + `first_existing`. Its own
    module so `frame_client` (on the frame Pi, installed `--no-deps`, no scipy)
    can find a font without importing `render`.
  - `frame_layout.py` — `compute_frame_scatter(...)`: the e-paper panel's own
    placement (`style=panel`), a focal scatter rather than the wall's spiral —
    newest bird largest on an anchor in a central box, the five before it
    around it, older ones smaller and further out, deterministic per live set
    so the panel doesn't redraw for a reshuffle.
  - `render.py` — `plan_wall(...)` decides WHERE everything goes for a wall
    of a given size (band, vmin, fixed caption sizes, placements, and each
    bird's ink box) — one function, so `/wall.png` and `/api/layout` cannot
    disagree. `render_wall_png(...)` draws that plan to a PNG server-side
    (Pillow) for the e-paper frame — cream paper + feather-masked
    multiply-blended birds + captions + header; full-colour (the panel dithers).
    `style=panel` swaps in the panel's white ground, the focal scatter, and
    birds cropped to their own ink; `layer=picture|text` splits the render so
    the frame can dither the picture and stamp unditherable lettering on top.
    Ink measurements are cached per (path, mtime): the browser asks for the
    panel plan on every poll.
  - `frame_client.py` — `python -m bird_painter.frame_client`: the thin e-paper
    frame client (runs on the frame Pi). At boot it looks for the recorder for
    `BP_FRAME_SEARCH_SECONDS` (60) and, only if it finds nothing, draws a
    centred "Looking for recorder" notice — the two machines don't boot in step
    and a redraw costs ~30 s of panel wear. A recorder that vanishes later
    doesn't bring the notice back — the last wall stays up. Then fetches the
    recorder's `/wall.png` on a slow timer, dithers to the Spectra 6
    six-colour palette, pushes to the
    panel via the Waveshare `epd13in3E` driver (imported lazily — hardware-only,
    so the module imports/tests without it); redraws only when the wall changed.
    Also WATCHES the recorder's `/ws/detections` on a daemon thread and redraws
    as soon as a bird is painted (`BP_FRAME_WAKE_ON_PAINT`), with a floor
    between redraws (`BP_FRAME_MIN_SECONDS`) so a burst coalesces into one —
    the panel takes ~30 s per redraw and wears with each. The stream is a
    nicety: without it the frame still polls, so an unreachable or older
    recorder costs latency, not the picture. NB the frame installs `--no-deps`,
    so `websockets` must be installed explicitly there.
  - `static/unit-screen.js` — the table model's settings screen (#123),
    drawn on the wall's paper from the design canvas: display knobs as
    steppers (lettering, controls, birds on the sheet, orientation), the
    night group, network (list in reach, join with an on-screen keyboard,
    forget), about + restart. Loaded by the wall only in panel mode and only
    when `/unit` answers (i.e. on the unit itself); opened by a 1.5 s press
    in the bottom-left corner; closes on × or a minute idle (not during a
    join). Every number it shows is the server's — bounds and steps too,
    from `/unit`'s `bounds`, so a stepper can't drift from the server. Taps
    coalesce into one PUT per 300 ms; a failed PUT reloads the server's
    numbers; restart needs a second tap. With no internet at boot it opens
    the network list by itself after 20 s, and keeps the wall's
    "offline — still listening…" line current. Its pure helpers (stepper
    maths, escaping, signal bars, error wording) are unit-tested in
    `static/unit-screen.test.js` (`make test-js`).
  - `static/api-docs.html` — the documentation page: renders `/api` and
    carries a live console wired to `/ws/detections`.
  - `static/index.html` — the wall (polling, fade in/out); imports the layout
    module and applies it to the plate DOM. Reads the table model's panel
    tuning from the query string (`?spread=`, `?caption=`, `?ui=`) and sets
    the CSS `--caption-scale` from the SAME normalised value the layout uses,
    so the type and the room reserved for it can't disagree; `?ui=` sizes the
    archive chrome on its own (`--ui-scale`), in both modes — the desktop
    wall's archive no longer follows `?caption=`, which it once did by
    accident. `?style=panel` (the
    table model's kiosk URL) computes nothing locally: it fetches the frame's
    plan from `/api/layout` for its own viewport and applies it — positions,
    bird-shaped cells, ink crops, the panel's fixed-size type — so the screen
    places birds exactly as the e-paper does.
  - `static/layout.js` — pure collage-layout maths (`computeCollage`): spiral
    placement, no-overlap, crowding scale. No DOM — unit-tested. Also
    `normalizePanelOpts`: the single sanitising chokepoint for the panel
    tuning (`spread` — a floor on the cluster width; `captionScale` — a
    multiplier on the lettering AND on the caption reserve). Both default to
    the wall's long-standing look, so the dev wall and the e-paper
    installation are unaffected.
  - `static/layout.test.js` — `node --test` guard for the layout (overlap-free
    across random sets/viewports, determinism, on-screen); run by
    `make test-js` inside `make review-checks`.
  - `__main__.py` — `python -m bird_painter [port]` (port: CLI arg → `BP_PORT`
    → default 8537; `--list-devices` lists mics; in a TTY with the listener on
    and no `BP_INPUT_DEVICE` pinned, prompts to pick the mic — `--no-prompt`
    skips; sets up INFO logging so the listener heartbeat surfaces): runs the
    whole loop — wall + live mic listener painting heard birds (disable the
    mic with `BP_ENABLE_LISTENER=false` for wall-only / tests / QA).
- `.gitignore` — excludes `.claude/settings.local.json` (machine-local
  permission grants; public repo).
- `CLAUDE.md` — this file: per-repo workflow + context.
- `docs/hardware.md` — Phase 4 hardware BOM + setup (recorder Pi + mic +
  Inky Impression e-paper frame; procurement checklist).
- `docs/wall.png` — README hero image: the app's own `/wall.png` render of a
  set of real mic detections (regenerate with `render_wall_png` if the wall's
  look changes materially).
- `PLAN.md` — product/architecture source of truth (concept, pipeline, stack,
  v0 config, scope, risks, decision log).
- `Makefile` — `make review-checks` (= lint via ruff + test via pytest +
  test-js via `node --test` for the wall layout); the deterministic-check
  wrapper the senior-dev review runs. `test-js` skips gracefully if node is
  absent.
- `tests/fixtures/plates/` — five real plates (downscaled) pinning thresholds
  calibrated over the whole archive: three for the plate check (one good, one
  desk photo, one grey block) and two for the panel's ground detection (one
  painted on a grey field inside a white border, one pale bird on true white —
  the false positive an earlier rule produced). `data/` is gitignored and
  purged monthly, so without these the calibration would have no durable
  evidence.
- `tests/` — pytest suite (store, gate, runner, brush, placeholder, web API,
  event hub + detection WebSocket, API docs (incl. docs-vs-routes drift
  guards), wall-layout port + JS-parity, /wall.png render incl. its
  style/layer params, the panel's focal scatter (`test_frame_layout.py`), the
  frame client's fetch/dither/stamp cycle, painting trim, night mode's
  schedule/backlight/transitions (`test_night.py`), the unit's settings
  files and nmcli wrappers (`test_unit.py`); import-purity
  regression).
  Always injects absolute tmp archive dirs.
- `scripts/status.sh` — live per-phase status snapshot from GitHub
  milestones/issues (backs the `/status` skill).
- `scripts/setup-table-model.sh` — installs the table model ON the unit
  (Pi 5 + Touch Display 2): apt deps, checkout, venv, the ears' backend
  (`tflite-runtime`, else `ai-edge-litert` behind a two-file
  `tflite_runtime` shim — Python 3.13 has no tflite-runtime wheel — else
  full `tensorflow`), the two undeclared deps birdnetlib needs on 3.13
  (`audioop-lts`, `audioread`), `.env` with the mic pinned by name and NO
  key, the `bird-painter` systemd unit, the Chromium kiosk wrapper
  (`~/.local/bin/bird-kiosk`, cache in RAM, `--password-store=basic`) in the
  labwc autostart behind an output rotation (`BP_ROTATE`, default 90 =
  landscape), an invisible cursor theme set through labwc's session
  environment (Wayland has no `unclutter`; this hides the compositor's
  pointer and Chromium's alike), autologin and no blanking. Idempotent;
  re-run to update. The per-unit values it was run with (`OUTPUT`,
  `ROTATE`, `CAPTION`, `UI`, `MAX_LIVE`) persist on the unit in
  `~/.config/bird-painter/unit.conf` — not a repo file — so a bare re-run
  keeps them. First run on the first unit: 2026-09-03.
- `scripts/polkit/50-birdframe-unit.rules` — the polkit rule the install
  script installs (user name substituted): NetworkManager's actions and
  logind's reboot for the unit's user only, so the service — which has no
  login session — can join a network and restart the unit from its screen.
- `scripts/memcheck.py` — peak-RSS measurement of the Python side on a
  unit (#121's number). On the first unit with LiteRT: 261 MB with the
  Analyzer loaded, 331 MB after a clip cleanup; the whole running service
  sits at ~290 MB (its RSS per `systemctl status bird-painter` on that
  unit, 2026-09-03). Full TensorFlow measured 641 MB on the dev machine.
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
- **2026-08-04** — **User is on auto mode: Claude decides, including
  product/UX calls.** "Stop asking for permission all the time, you are on auto
  mode." Supersedes the earlier rule to bounce user-facing copy / defaults /
  visible behaviour to the user (workflow step 4). Claude picks the option it
  would defend, says so in the notification, and continues; questions are for
  unsafe or hard-to-reverse actions, or facts only the user has. Applies to
  this repo; mirrored in memory as a cross-repo preference.
- **2026-07-27** — **Review/QA agents always run on Opus** (`model: "opus"`),
  regardless of the main session's model. User instruction, standing.

## Phase trackers

Convention: one milestone per phase (`Phase N — <name>`) + one
`phase-tracker`-labelled issue opened at phase start; both closed together at
phase end. Deferred work → `follow-up`-labelled issues against the right
milestone. Current anchor: **Phase 0 — Scaffold** (see the `phase-tracker`
issue under that milestone).
