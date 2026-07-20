# bird-painter — PLAN

Source of truth for product and architecture. Read this before making changes.
Decisions are recorded in the decision log at the bottom; the always-loaded
`CLAUDE.md` points here.

---

## What it is

**bird-painter is an ambient installation.** A microphone on a local machine
listens to the outdoors. An AI birdsong recognizer identifies which bird
species are actually out there right now. Each newly-heard species is painted
by an AI image model in a consistent house style, and the paintings appear on a
full-screen "wall" meant to be left running on a screen in the room. Paintings
stay on the wall for a few hours, then fade out — so the wall is always a fresh
reflection of what's been singing outside recently.

No typing, no controls. The environment is the input.

Audience: **personal / installation toy** — one machine, one room, the owner and
whoever's around. Not a public multi-user product. No accounts, no billing, no
inbound public traffic. (If it ever grows into a public product, that's a
separate, later decision — see Non-goals.)

## The pipeline

Four stages left to right, with a **trigger gate** (the debounce/cap logic)
sitting between recognize and generate:

```
[ mic ] --> capture --> recognize (BirdNET) --[trigger gate]--> generate (FLUX) --> display (wall)
             local        local                                   cloud API          local web page
```

1. **Capture** — a continuous `sounddevice.InputStream` (PortAudio's own
   thread) fills a ring buffer; the listen loop pulls rolling windows off it.
   Capture and analysis run on separate threads, so audio arriving during an
   analysis is buffered, not dropped — gapless windows.
2. **Recognize** — run BirdNET locally on each window; emit `(species,
   confidence, time)` for detections above a confidence floor.
3. **Trigger gate** — a detection paints a bird only if that species is off
   cooldown (see the precise rule below), subject to a global per-hour cap
   (cost ceiling).
4. **Generate** — call a hosted image model with a prompt built from the
   species + the fixed house style; get back a painting.
5. **Display** — save the painting to the permanent archive, add it to the live
   set, and show it on the wall. A sweeper fades out (hides) paintings past
   their TTL; the archive keeps the files.

## Architecture

**Local ears, cloud brush.** Everything that touches the mic or the ML model
for recognition runs locally (that's where the microphone is, and BirdNET is a
local TF-Lite model). The only thing that leaves the house is the image-
generation call — a stateless REST request to a hosted model. The wall is
served locally off the same process.

- **One Python process** on the mic machine runs the whole loop: capture →
  BirdNET → trigger/debounce → image API call → archive + live set → serves the
  wall web page.
- **Language: Python**, because BirdNET (`birdnetlib`) and the audio stack are
  Python-native. The image call is a plain REST request; the wall is a served
  HTML/JS page. Keeping it one process keeps a personal toy simple.
- **Not** using Vercel / Next.js / the Vercel AI SDK. Those are for hosted JS/TS
  web apps; this is a local Python service. (The repo's session tooling happens
  to surface Vercel skills — ignore them here.)

### Components

- **Non-bird filter** — BirdNET's label set isn't birds-only; alongside ~6400
  birds it carries 11 machine/human/environment pseudo-classes (Dog, Engine,
  Gun, Siren, Fireworks, Noise, Power tools, Human vocal/non-vocal/whistle,
  Environmental) and ~86 non-bird animals (frogs, toads, crickets, katydids,
  coneheads, mammals) so it can say "not a bird". The ears drop all of these,
  matching on the scientific name (common names like "Squirrel Cuckoo" are
  bird traps); only birds reach the wall. A test re-derives the denylist from
  the shipped label file so a birdnetlib upgrade can't silently break it.
- **Recognizer** — BirdNET via [`birdnetlib`](https://github.com/joeweiss/birdnetlib),
  a clean Python wrapper around Cornell Lab's BirdNET-Analyzer. Runs fully
  local and offline, no API key, trained on ~6000 species, returns species +
  confidence + timestamps.
- **Brush (image model)** — **FLUX `schnell`** (Black Forest Labs) via
  **fal.ai**, called over REST from Python. Chosen for lowest cost-per-image
  (fractions of a cent) — which matters on an all-day loop — with quality that's
  already lovely for stylized birds. Upgrade path: FLUX `dev`/`pro` if `schnell`
  underwhelms. Needs one API key, kept in a local `.env` (never committed, never
  pasted into chat).
- **Store** — permanent archive of every painting on disk (`{image file,
  species, born_at}`), plus an in-process live set. The wall reads the live set;
  expiry hides from the wall but never deletes the archived file.
- **Wall** — a full-screen web page served locally by the Python process
  (framework: **FastAPI**). New painting fades in when its bird is heard;
  expired ones fade out. Subtle per-bird label (species, time heard). Updates by
  **simple polling every few seconds** (SSE is overkill for one local viewer).
  - **Collage, not a grid** (Phase 3): the page itself is the aged-cream paper
    the paintings live on — painting edges are feathered (CSS mask) and
    multiply-blended into the shared paper so nothing reads as a floating
    rectangle. Placement is a phyllotaxis spiral growing from the middle:
    newest bird at the center, older ones drifting outward, each with a
    stable per-painting size (~24–36 vmin), tilt, and scatter (hashed from
    its filename, so layout is deterministic across reloads). Existing
    plates glide outward as newer ones arrive.
  - **Framed like a naturalist wall-chart.** A fixed title header sits at the
    top (small italic eyebrow "birds outside" + letterspaced "heard recently");
    a compact cluster sits centred below it — a central oval sized by the
    SMALLER viewport axis, so a wide screen gets a dense clump in the middle,
    not birds fanned edge-to-edge. **No per-bird text** — a bird is a bare
    cutout, its species only in the image `alt` for screen readers. Birds are
    painted on plain white, so the wall's multiply-blend drops the ground and
    leaves clean cutouts on the shared paper.
  - **Birds never overlap.** Layout is computed globally: each plate takes the
    first free spot walking the spiral (its box = image + margin vs everything
    already placed, kept inside the sub-title band). If any plate can't find a
    free spot at the current size, all plates shrink together (bounded
    shrink-and-relayout loop) until everything fits — so overlap is engineered
    away, not just unlikely. The tilt applies to the painting image only.
    (Transient exception, accepted: a freshly-expired plate crossfades out
    where it stood, so a gliding live plate can briefly pass over it.)

## House style

Every bird is rendered in **one fixed house style — vintage naturalist
illustration** (Audubon-style hand-painted field-guide plate, aged paper). A
single style makes the wall read as one cohesive collection rather than a
grab-bag of outputs. It's a one-line prompt template, trivial to swap later
(watercolor, ink, oil…).

- Prompt is built from the species' **common + scientific name** plus the style
  template. The bird is painted **isolated on flat pure white** (so the wall
  cutout-blends it cleanly, no paper vignette) with a hard **no-text** tail and
  no "field-guide plate / Audubon" style words — those make FLUX bake in
  engraved captions and an aged-paper ground. `schnell` follows this loosely;
  `fal-ai/flux/dev` (via `BP_FAL_MODEL`) obeys it far better — recommended if
  text/paper still leak through.
- Accepted limitation: FLUX won't perfectly render every one of ~6000 species,
  especially rare ones. It takes artistic license. For a personal toy that's
  charm, not a defect.

## v0 configuration (defaults)

Tune after watching it run a real day. The two that matter most in practice are
the confidence floor (too low → wrong birds on the wall) and the per-hour cap
(the cost ceiling).

| Knob | Default | Rationale |
|---|---|---|
| Paint TTL (= species repaint cooldown) | **3 hours** | keeps the wall a fresh set; a species repaints only once TTL has elapsed since it was last painted |
| BirdNET confidence floor | **0.6** | filters weak guesses; a wrong-species painting is the worst failure mode. Clamped to birdnetlib's `[0.01, 0.99]` (a `0` or `1.0` is coerced + warned); the filter is strict `>`, so a detection exactly at the floor is excluded |
| Analysis window | **~15 s rolling** | BirdNET's native chunk; steady detection |
| Max paints / hour | **20** | hard ceiling so a loud dawn chorus can't run away the API bill |
| Wall collage | **up to ~12 live** | full but not cramped; overflow → oldest fades first |
| Location filter (lat/long/week) | **off** | BirdNET can weight by location/season to cut implausible species — nice later, skip for v0 |

**Trigger rule, precisely:** a BirdNET detection with confidence ≥ floor paints
the species **iff** (a) it's been at least TTL since that species was last
painted (`now − last_painted_at[species] ≥ TTL`), and (b) the rolling per-hour
paint count is under the cap. The cooldown keys on a per-species
`last_painted_at` timestamp, **not** on whether a painting is still on the
wall — so wall overflow eviction (below) can never shorten the cooldown by
letting an evicted-but-unexpired species repaint early. TTL doubles as the
repaint cooldown — one knob, not two.

## Scope

**v0 (the core loop):** capture → BirdNET → debounced/capped trigger → fal FLUX
`schnell` → archive + live set → one full-screen auto-updating wall. That's the
whole magic; ship it first.

**Fast-follows (post-v0):**
- Archive browser view (scroll everything ever painted — the archive already
  exists from v0).
- Location/season filter on (cut implausible species).
- Style switcher; `dev`/`pro` quality tier.
- Per-bird metadata richness (confidence, sonogram, time-of-day trends).

**Non-goals (v0, and mostly forever for a personal toy):**
- Public multi-user product: accounts, auth, billing, inbound public traffic,
  abuse/moderation. If traction ever justifies it, that's a deliberate later
  pivot, not v0 creep.
- Self-hosting the image model (no home GPU diffusion; the cloud call is cheap
  and stateless).
- Streaming mic audio off-machine (recognition stays local).

## Risks / open questions

- **Mic quality & placement** — outdoor birdsong through a window/indoor mic may
  be faint or noisy; detection quality depends heavily on this. Empirical, tune
  the confidence floor and mic gain once running.
- **`schnell` fidelity on rare species** — may need `dev`/`pro` for some birds;
  cheap to A/B later.
- **Cost feel** — 20 paints/hour × fractions of a cent is trivial, but confirm
  actual fal pricing before leaving it running unattended for days.
- **BirdNET false positives** in noisy environments — the confidence floor is
  the main defense; location filter (fast-follow) helps.
- **Image-API failure / outage** — fal could be slow, error, or rate-limit. v0
  policy: on a failed paint, log it, don't retry aggressively, don't consume the
  hourly cap slot, and don't mark the species painted (so it retries naturally
  on the next detection). No painting simply means no new bird on the wall — a
  soft failure, never a crash.
- **Archive disk growth** — the permanent on-disk archive grows unbounded (every
  bird ever painted is kept). Trivial for a personal toy at 20 paints/hour, but
  name it: no retention/pruning in v0; add an archive cap or size-based prune as
  a fast-follow if it ever matters.

## Decision log

- **2026-07-18** — Concept pinned via design grilling. bird-painter is an
  ambient installation: local mic → BirdNET recognition → fal FLUX `schnell`
  painting → local full-screen ephemeral wall. Audience: personal toy (not a
  public product). Architecture: one local Python process, "local ears, cloud
  brush." Recognizer: BirdNET via `birdnetlib`. Image model: FLUX `schnell` on
  fal.ai. State: permanent disk archive + ephemeral live view (paintings hide
  after TTL, files kept). Display: single full-screen auto-updating wall (FastAPI
  + polling). Style: fixed vintage-naturalist. v0 config defaults approved as
  tabled above. Archive browser, location filter, style switcher deferred to
  fast-follows.
- **2026-07-20** — Phase 4 (hardware) kicked off. Architecture: one app
  instance on the recorder Pi + a thin e-paper frame client (no pipeline
  split). Panel recommendation: Inky Impression 7.3" (6-colour, 800×480) —
  pins the `/wall.png` render target. Full BOM/setup in `docs/hardware.md`.
