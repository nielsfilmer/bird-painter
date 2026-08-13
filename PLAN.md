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
5. **Display** — save the painting to the archive, add it to the live set, and
   show it on the wall. A sweeper fades out (hides) paintings past their TTL;
   the archive keeps the files for a rolling month (the retention purge).

## Architecture

**Local ears, cloud brush.** Everything that touches the mic or the ML model
for recognition runs locally (that's where the microphone is, and BirdNET is a
local TF-Lite model). The only thing that leaves the house is the image-
generation call — a stateless REST request to a hosted model. The wall is
served locally off the same process.

Audio is analysed and discarded — with ONE deliberate exception: the few
seconds around a detection that actually painted are archived as a WAV beside
the painting (~0.5 MB each) and served on the LAN at `/audio/*`, so clicking a
bird on the wall replays the song that painted it. Nothing leaves the house;
the clip never reaches any cloud service.

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
- **Store** — rolling archive of paintings on disk (`{image file, species,
  born_at}` + optional detection clip), plus an in-process live set. The wall
  reads the live set; TTL expiry hides from the wall without deleting. A
  separate **retention purge** (default 31 days, `BP_RETENTION_DAYS`) deletes
  artifacts outright — at boot, after adds, and hourly from the read path —
  compacting `meta.jsonl` atomically so reboots can't resurrect purged birds.
- **Wall** — a full-screen web page served locally by the Python process
  (framework: **FastAPI**). New painting fades in when its bird is heard;
  expired ones fade out. Subtle per-bird label (species, time heard). Updates by
  **simple polling every few seconds** (SSE is overkill for one local viewer).
  - **Collage, not a grid** (Phase 3): the page itself is the aged-cream paper
    the paintings live on — painting edges are feathered (CSS mask) and
    multiply-blended into the shared paper so nothing reads as a floating
    rectangle. Placement is a phyllotaxis spiral growing from the middle:
    newest bird at the center, older ones spiralling outward, each with a
    stable per-painting size and scatter (hashed from its filename, so layout
    is deterministic across reloads; birds stand upright — no rotation).
    Existing plates glide outward as newer ones arrive.
  - **A shelf first, then vertical growth around it.** The three OLDEST live
    birds hold a single horizontal ROW across the band centre for good — a
    packed shelf, oldest→newest reading left→right, whose members never move
    or swap sides as the wall changes. Every newer bird spirals into the
    full-height oval with the shelf as an obstacle AND a vertical clearance
    (it must sit fully above or below the shelf, never level with it), so the
    group grows vertically. Birds render at their natural size (~16–20 vmin, a
    tight span so no bird renders far smaller than its neighbours); as more
    arrive the oval widens (widen-to-fit, starting one plate wide) until it
    hits the viewport cap, and only then does one global fit-scale drop below
    1 so every plate shrinks together. Plate centres are clamped to the oval
    (the spiral's reach can exceed it — an unbounded x used to flatten the
    group into a row).
  - **Paintings are trimmed to the bird.** FLUX paints the bird small on a
    large flat-white canvas; at store time the white margin is cropped off
    (bounding-box + a small breathing margin, padded back to the plate's 4:5)
    so the bird fills its plate. Fail-soft: SVG placeholders and unreadable
    files are stored untouched. The archive keeps the trimmed painting.
  - **Framed like a naturalist wall-chart.** A fixed title header sits at the
    top (small italic eyebrow "birds outside" + letterspaced "heard recently");
    the collage cluster sits centred below it. Each bird
    carries a **small per-bird label** — species (small-caps) + "heard at
    HH:MM" (italic, 24-hour) — added programmatically as page text, **never
    baked into the image**. The label is a **fixed clock time, not "x min
    ago"**: the e-ink frame only refreshes every few minutes, so a relative
    label would be stale between draws. Birds are painted on plain white, so
    the wall's multiply-blend drops the ground and leaves clean cutouts on the
    shared paper; the birds are sized generously (small caption reserve) so
    they read as the subject.
  - **Birds never overlap, and stand upright (no rotation).** Layout is
    computed globally: each plate takes the first free spot walking the spiral
    (its box = image + caption + margin vs everything already placed, kept
    inside the sub-title band). If any plate can't find a free spot at the
    current size, all plates shrink together (bounded shrink-and-relayout loop)
    until everything fits — so overlap is engineered away, not just unlikely.
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
| Location filter (lat/long) | **off (opt-in)** | Set `BP_LATITUDE`+`BP_LONGITUDE` (both, decimal degrees) to restrict BirdNET to species plausible at that place + season (its meta model uses the current date); unset = global model. Cuts implausible detections |

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
- ~~Archive browser view~~ — shipped 2026-07-27 ("heard this month" overlay,
  browser wall only; the archive is a rolling month under retention).
- ~~Location/season filter on (cut implausible species).~~ **Shipped** — opt-in
  via `BP_LATITUDE`/`BP_LONGITUDE` (see the v0 config table).
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
- **Archive disk growth** — bounded: artifacts (painting + detection clip +
  meta record) are purged after a rolling month (`BP_RETENTION_DAYS`, default
  31). Worst case ≈ 20 paints/hour × 31 days of images+clips — comfortably
  within an SD card for a personal toy.

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
- **2026-07-20** — Location filter shipped (fast-follow off the v0 table).
  Opt-in via `BP_LATITUDE`+`BP_LONGITUDE` (both-or-neither, range-validated);
  threaded into `birdnetlib`'s `Recording`/`RecordingBuffer` as `lat`/`lon`/
  `date` (date = now, so the species list tracks the season). Unset = global
  model (unchanged v0 behaviour).
- **2026-07-20** — Phase 4 (hardware) kicked off. Architecture: one app
  instance on the recorder Pi + a thin e-paper frame client (no pipeline
  split). Panel recommendation: Waveshare 13.3" Spectra 6 (6-colour, 1600×1200) —
  pins the `/wall.png` render target. Full BOM/setup in `docs/hardware.md`.
- **2026-07-20** — Phase 4 slice 2: `/wall.png` server-side collage render
  shipped. The e-paper frame can't run the browser wall, so the collage is
  rastered server-side (Pillow) and served at `/wall.png`, default 1600×1200
  full-colour (the panel driver dithers to 6 colours). Placement reuses the
  layout maths via a Python port of `static/layout.js` (`wall_layout.py`), kept
  in lockstep by a node-vs-Python parity test — so bird positions/sizes are
  identical to the live wall. The raster closely *mirrors* the wall rather than
  being a pixel-identical browser screenshot: it lays the cluster into a
  slightly shorter box (a bottom inset so captions clear the panel edge) and
  hand-matches the header/caption typography from the CSS. Size + caption fonts
  are env-configurable.
- **2026-07-23** — The app binds **0.0.0.0 by default** (`BP_HOST`), so the
  e-paper frame and other devices on the LAN can reach the wall / `/wall.png`.
  Surfaced during the real two-box install: the previous hardcoded
  `127.0.0.1` bind made the recorder unreachable from the frame. `127.0.0.1`
  remains available via `BP_HOST` for a single-machine setup.
- **2026-07-27** — Wall look reworked after live-preview sign-off (user ran it
  on the frame from 2026-07-25): placement is now **full height first → widen
  with count → shrink only when the screen is full** (plates 16–20 vmin,
  centres clamped to the oval), and paintings are **trimmed to the bird** at
  store time so the bird fills its plate. Supersedes the 2026-07-20 "fills
  both axes / big-to-start" rule from PR #59.
- **2026-07-27** — **Detection clips archived + replayable** (owner feature
  request). The pipeline still discards audio by design, except the seconds
  around a painted detection: stored as WAV beside the painting, served on the
  LAN at `/audio/*`, replayed by clicking the bird on the browser wall. The
  clip stays inside the house (never sent to any cloud). Growth bounded by the
  planned 1-month retention purge.
- **2026-07-27** — **Occasion hats** (owner feature request): on special days
  birds are painted wearing a tiny hat, woven into the FLUX prompt (the one
  place in-image content is right — it's the subject, not a label). Public
  holidays (New Year, King's Day, Halloween, Sinterklaas, Christmas) live in
  code; personal days (family birthdays, one-off parties) come ONLY from env
  (`BP_HAT_DAYS`/`BP_HAT_DATES`) so they never enter this public repo.
  Personal days take precedence and always get the party hat.
- **2026-07-27** — **1-month retention purge** (owner request): paintings,
  clips, and meta records older than `BP_RETENTION_DAYS` (default 31) are
  deleted — the archive is a rolling month, superseding v0's "archived
  forever" stance. Purge runs at boot + throttled from the read path, and
  compacts meta.jsonl atomically.
- **2026-07-27** — **Archive browser shipped** (fast-follow): a muted "archive"
  button on the browser wall opens a full-screen "heard this month" overlay —
  everything retention has kept, newest first, paginated, clips playable. The
  e-paper `/wall.png` never shows it (server raster has no DOM; test pins the
  split).
- **2026-07-27** — **Shelf-anchored placement** (owner rule, corrected same
  day): the three OLDEST live birds hold a horizontal row at the band centre
  permanently; newer birds stack vertically around it. Two-phase layout: the
  shelf is PACKED (oldest→newest, left→right, centred), then newer birds
  spiral the full-height oval with the shelf as obstacles and a vertical
  clearance (never level with it); one shared widen/shrink loop.
- **2026-08-04** — **Live detection WebSocket** (owner feature request):
  `/ws/detections` pushes what the ears hear — a `detected` event per
  recognition (carrying the trigger gate's `will_paint` verdict) and a
  `painted` event per painting, with the species name, the time, the image url
  and the detection clip's url (plus a `?download=1` variant that serves the
  clip as an attachment). The wall's poll of `/api/live` is unchanged; this is
  the push side of the same story, for anything that wants to *watch*
  recognition happen. Urls are absolutised per connection, so a LAN client gets
  fetchable links. Fan-out is best-effort: bounded per-client queues drop their
  oldest events rather than ever stalling the mic thread, and neither a broken
  socket nor a failed clip lookup can cost a painting that already landed
  (both producers announce through one guarded path). A client that vanishes
  without closing is only observable via the ASGI receive channel, so the
  endpoint races a receive against its send pump — otherwise a wall running for
  months accumulates zombie subscribers. **A gated detection stays a bare fact**
  (owner decision, 2026-08-04, when the review asked): name, time, confidence,
  `will_paint: false` — no image and no clip, because attaching an older
  painting's would describe a different hearing than the one being announced.
  Stays inside the house like everything else — no
  auth, LAN-scoped, same trust boundary as `/api/live` and `/audio/*`.
- **2026-08-04** — **The API documents itself** (owner request, same day):
  `/api/docs` is a page describing every endpoint and every stream event, with
  a live console wired to `/ws/detections` so the documentation demonstrates
  the thing it documents; `/api` serves the same description as JSON, with the
  instance's live settings. One structure (`api_docs.py`) feeds both, and tests
  assert docs-vs-routes parity in both directions, so the docs can't quietly
  drift from the app. FastAPI's generated `/docs` stays as the OpenAPI
  reference for the REST half — it cannot express a WebSocket, which is why
  the hand-written surface exists.
- **2026-08-04** — **`/dev/paint` is loopback-only** (owner decision: "the API
  should not be completely public, only the websocket should be reachable" —
  narrowed, on being asked, to everything-except-`/dev/paint`, enforced by
  client address). It bypasses the hourly cap and spends real money per call
  with a key set, so on an unauthenticated LAN it was the one endpoint that
  could cost something (issue #66). Off-machine callers get 404 because for
  them the path genuinely isn't routable — not as concealment: `/api` and
  `/api/docs` describe the endpoint and its 404 to anyone who asks. The
  decision is made on the peer address only, never on `X-Forwarded-For`, and
  an unplaceable peer counts as remote, so it fails closed. That required
  turning uvicorn's proxy-header handling **off** (`proxy_headers=False`): it
  is on by default and rewrites the peer address from `X-Forwarded-For`, which
  with `FORWARDED_ALLOW_IPS=*` in the environment handed the endpoint to any
  caller willing to type a header — found in review, with a working exploit,
  before merge. Reaching it from elsewhere is an ssh tunnel's job. Everything else stays open on the network: the wall, the
  archive, the stream, and the images and sounds its events link to — the
  phone and the e-paper frame depend on them.
- **2026-08-04** — **Archived detection clips are cleaned and levelled**
  (owner request: "can you cleanup the actual detected sound better and boost
  it so it can be heard clearer?"). A raw window off a window-facing mic is
  mostly traffic rumble with a bird somewhere in it — the nine clips in the
  archive averaged **-46 dBFS with 75–95% of their energy below 200 Hz**.
  `clip_clean.enhance` now runs before archiving: spectral subtraction against
  a noise profile the clip supplies itself, band-limiting to the band the bird
  actually occupies, then normalise with a soft limiter (ceiling 0.97, never
  full scale).

  **The band is chosen by CONTRAST — how far a bin rises above its own steady
  level, at the 90th percentile over the clip — not by loudness and not by
  absolute transient energy.** Review caught the first two attempts: loudness
  picks the traffic outright, and absolute transient energy also picks the
  traffic (rumble fluctuates, and 40 dB of fluctuating rumble beats a faint
  bird) — it pinned **all nine real clips to the 200 Hz floor**, i.e. the
  cleanup was band-limiting to the noise and deleting the bird. Contrast is
  scale-free, so a faint 6 kHz bird outranks a loud 300 Hz lorry; the 90th
  percentile adds duration, so a door slam or a knock on the mic stand — loud,
  transient, brief — can't win a band a sustained note holds. Growth from the
  peak stops against the metric's own background level, since hiss has contrast
  in every bin and a plain fraction-of-peak threshold would call the whole
  spectrum "the bird".

  Measured after the fix: the nine real clips land on bands from 234 Hz (elf
  owl) to 11.7 kHz (cuckoo), with sub-200 Hz energy down from 75–95% to
  **≤3.6%**, and every clip at -0.3 dBFS.

  **The noise profile comes from the padding, not from statistics.** Round-2
  review found the remaining failure: a bird that sings through its own
  detection becomes its own noise profile and is subtracted away, leaving
  amplified hiss at full level — silently, in the only copy — and a clip is
  detection ± 1.5 s, so a 3 s song hits that case exactly. The fix uses what
  the caller already knows: `detection_clip_wav` passes the detection's
  position, and the padding either side is the same room without the bird —
  both the noise profile and the "which bins are louder during the detection"
  comparison come from it. Measured across bird-fills-20/50/80/100%-of-its-
  detection: 8/8 kept at every level, where before it was erased about half
  the time at 50%. A clip with no quiet moment anywhere, padding included, is
  archived raw rather than cleaned — nothing can learn a room that never goes
  quiet, and a raw clip beats a confident wall of hiss.

  On synthetic mixtures in-band SNR improves 6×–115×, including cases built
  to defeat it (a 300 Hz thump 25× the bird's amplitude; an 800 Hz hum 90×).
  Fail-soft throughout (`BP_ENHANCE_CLIPS=false` archives the raw cut): a clip
  in the archive beats a traceback in the mic thread. The recording policy is
  unchanged — still only the seconds around a painted detection, still never
  leaving the house.
- **2026-08-05** — **The location filter no longer implies the season**
  (owner question: "can we use location without season?"). BirdNET's meta
  model takes a place AND a week; passing a date opted into both. Now
  `BP_LATITUDE`/`BP_LONGITUDE` filter by place only, and the calendar is
  opt-in via `BP_SEASONAL_FILTER` (default off).

  The trigger was a nightingale played into the microphone: identified at 0.87
  confidence, then discarded, because BirdNET's list for this week has
  nightingales gone from the Netherlands. That is not evidence the seasonal
  model is *wrong* — a nightingale singing here in August is genuinely
  unlikely, and the recording was a speaker. What it does show is the failure
  MODE: the filter deletes silently. No detection, no log line, nothing to
  distinguish a filtered bird from a dead microphone — and it cost an hour of
  measuring input devices and spectra to find.

  So the default is chosen on which mistake is cheaper. Place alone still
  rejects the wrong continent, which is what the filter was added for (it
  binned a Broad-winged Hawk the same afternoon). Adding the calendar roughly
  halves the list — 140 species against 259 for Haarlem — and what it removes
  is the early, the late and the unusual, which on a wall meant to reward
  noticing is the bird most worth painting. A false hummingbird is a visible,
  self-correcting error; a deleted real bird is invisible.

  The listener's startup line now names the filter and how many species it
  allows, against the model's own total, so the two stop looking the same:
  `location filter 52.3874, 4.6462 — 259 species of 6522`.
- **2026-08-06** — **Plates that aren't a bird on white are rejected, not hung**
  (owner: "a few weird results from the drawing API", with a screenshot). Two
  failure shapes reach the wall: a photograph OF a painting — a watercolour
  sheet on a desk, pen and signature included, which `trim` can't rescue
  because the desk fills the frame — and a flat block of colour across part of
  the canvas with the bird squeezed beside it. `plate_check.describe_problem`
  measures two things and returns the reason: how much white margin surrounds
  the painting (a bird has ground on every side; a desk photo runs to the
  edge), and what share of the SUBJECT the flattest single colour covers
  (measured against the non-white pixels, so padding can't change the answer).
  The brush asks once more, then gives up.

  **Calibrated over all 277 archived plates, not the 9 that prompted it.** The
  first cut was tuned on the most recent handful and, worse, compared
  post-trim numbers against pre-trim images; review caught it rejecting three
  good birds, including a flawless hummingbird scoring 89% — a quantisation
  boundary counted a warm off-white ground as one enormous flat colour. As
  shipped: 3 of 277 rejected, all genuinely broken, none good.

  **Giving up costs an hourly-cap slot** (`brush.Rejected`, distinct from the
  `None` that means fal was unreachable). A model that paints one species
  wrongly does so deterministically, so free retries on every detection would
  be a spend loop — at a 15 s window, one persistent singer is ~480 paid calls
  an hour against a cap of 20. An outage still retries freely; only a
  reproducible bad plate is charged.

  Known blind spot, accepted: a print photographed on PALE GREY passes, since
  grey that light reads as ground. Tightening that threshold rejects good
  plates painted on warm off-white. A bad plate on the wall is visible and
  fades in three hours; a bird deleted for looking wrong is invisible.
- **2026-08-13** — **The frame wakes when a bird is painted** (owner request:
  "can we retrigger an immediate paint on the frame when a bird is detected?").
  The frame subscribes to the recorder's `/ws/detections` on a daemon thread
  and redraws on a `painted` event, instead of waiting out its 5-minute poll.
  The poll stays as the fallback cadence, and the stream is strictly a
  latency improvement: an unreachable, older, or restarting recorder costs
  freshness, never the picture.

  **A floor between redraws (`BP_FRAME_MIN_SECONDS`, default 90) is the
  load-bearing part**, not the waking. A Spectra 6 full redraw takes ~25–35 s
  and colour e-paper wears with every one; the hourly cap allows 20 paints, and
  a dawn chorus can land several within a minute. So a burst is coalesced into
  ONE redraw showing all of them, rather than a queue of redraws showing them
  one at a time. Birds that arrive while settling are already in the image
  being fetched, so their wake-ups aren't owed another redraw.

  Deliberately a thread with a synchronous WebSocket client rather than
  asyncio: the panel push blocks for half a minute and has no business inside
  an event loop.

  Discovered while building this: **both Pis were running months-old code** —
  the recorder on #79, the frame on #67 — so none of the session's merged work
  was actually in the house, and the recorder's `/ws/detections` 404'd. Merging
  is not deploying; the check script (`scratchpad/check-installation.sh`) now
  exists partly so that gap is visible rather than assumed.
