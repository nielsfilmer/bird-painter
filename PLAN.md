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
  are env-configurable. *(Partly superseded 2026-08-13: this still describes
  the default `style=wall` render, but `style=panel` no longer mirrors the
  browser wall at all — it has its own layout, ground, and typography. See the
  focal-scatter entry below.)*
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

  **A floor between redraws (`BP_FRAME_MIN_SECONDS`, default 90) coalesces
  bursts**, so several birds landing within a minute become ONE redraw showing
  all of them rather than a queue showing them one at a time. Birds that arrive
  while settling are already in the image being fetched (`announce_painted`
  fires after `store.add`), so their wake-ups aren't owed another redraw.

  It is NOT, however, what bounds panel wear — review measured that. The real
  ceiling is the recorder's own `BP_MAX_PAINTS_PER_HOUR` (20): twenty paints
  spaced 90 s apart span 28.5 minutes, which a dawn chorus can reach, so the
  floor rarely fires. Worst case is ~20–32 redraws an hour against ≤12 before
  this change — a 2–3× rise in peak wear, accepted for the immediacy, and the
  knob to turn if the panel starts to show it is the recorder's cap, not this
  floor. Deriving the floor from the measured push duration is the better
  design and is filed rather than guessed at.

  The floor is anchored to the last REDRAW, not the last fetch. Anchoring it to
  the fetch — the first version — delayed roughly 30% of birds by up to 90 s to
  protect a panel that had drawn nothing for an hour, since almost every poll
  in a quiet garden finds an unchanged image.

  Deliberately a thread with a synchronous WebSocket client rather than
  asyncio: the panel push blocks for half a minute and has no business inside
  an event loop.

  Discovered while building this: **both Pis were running months-old code** —
  the recorder on #79, the frame on #67 — so none of the session's merged work
  was actually in the house, and the recorder's `/ws/detections` 404'd. Merging
  is not deploying. Both are now on `main`.
- **2026-08-13** — **The panel layout is a focal scatter, not a grid** (owner,
  dictated after seeing the grid on the panel: "too much like a grid"). An
  anchor is picked inside a central box holding ~30% of the sheet's area; the
  newest bird sits there largest; the five heard before it gather around it a
  step smaller; older birds taper with age rank and are placed wherever the
  sheet is emptiest — which is naturally whatever side the anchor left open,
  so the composition balances and covers the sheet. Jitter is deterministic
  per live set (seeded from the file list): the frame redraws only when the
  bytes change, so a layout that wandered per render would wear the panel for
  nothing; a new bird reseeds the whole composition, one redraw it was
  spending anyway.
- **2026-08-13** — **Panel captions are fixed-size** (owner, on seeing scaled
  ones: "don't resize the text, keep the text the same size"). An earlier
  draft scaled each caption with its plate; a small bird's label then became
  unreadable across a room, which is the only distance the panel is read from.
  Because the type no longer shrinks, a caption can be wider than the bird
  above it, so the layout takes each caption's MEASURED width as a floor on
  that bird's footprint — otherwise two small neighbours overlap each other's
  lettering. The browser wall keeps its own smaller type: it is read at a
  desk, and its layout reserves room for those sizes.
  *(Partly superseded 2026-09-03: the diagnosis stands, but "the browser wall
  is read at a desk" no longer holds. The table model runs the browser wall on
  a portrait panel read from across a room — the same distance that forced
  this decision for the e-paper panel — so the wall gained a `?caption=`
  multiplier that grows the layout's reserve alongside the type. It does NOT
  yet have this entry's other half, the measured-width floor; that gap is
  tracked as #133 and blocks #120. See the table-model entry below.)*
- **2026-08-20** — **A plate's ground is its most common light level.** FLUX
  sometimes paints on a light-grey field inside a white border; keying against
  the border's median then removed nothing and the plate landed on the white
  panel as a grey rectangle. Calibrated against all 200 plates in the archive:
  an earlier rule — the darkest level covering >2% of the plate — fixes the
  grey fields but reads a pale bird's whole body as ground, so hawfinches and
  collared doves rendered as hollow shells. "Most common" separates the two,
  moving the ink share on exactly the four plates that have a grey field.
  Ink bounds come from connected components for the same family of reason: a
  pixel percentile cut a jackdaw's head and tail off, because a thin extremity
  is real ink at a few pixels per row.
- **2026-08-20** — **`/wall.png` takes `style` and `layer`.** `style=panel`
  renders for the e-paper frame (white ground, focal scatter, no title);
  `layer=picture|text` splits that render so the frame can dither the picture
  and stamp the lettering through an unditherable mask — an 8px italic put
  through Floyd–Steinberg is speckle, not type. Defaults render the browser
  wall exactly as before. The frame tells a layer-aware recorder from an older
  one by the text layer's image MODE, not by an error: FastAPI ignores query
  params a route doesn't declare, so an old recorder answers 200 with the
  ordinary wall, and treating that as a mask stamps the panel black.
- **2026-08-20** — **The frame says when it can't find the recorder.** At boot
  it looks for `BP_FRAME_SEARCH_SECONDS` (60) and only then, having found
  nothing, draws a centred "Looking for recorder". The two machines are on one
  network and don't boot in step — the frame is usually first — so until now it
  sat on whatever the panel happened to be holding, with nothing to separate
  "waiting" from "broken".
  - The notice appears ONLY if the search found nothing. A redraw takes ~30 s
    and wears the panel, so announcing a wait that turned out to be four
    seconds costs more than it's worth.
  - The search is a real fetch-and-draw attempt, not a liveness ping, so a
    recorder that IS up costs no extra request — the first success puts its
    wall straight on the panel. There is no cheaper probe worth having: `HEAD
    /wall.png` is refused (405) and `/api` doesn't exist on older recorders, so
    probing it would report a good recorder as missing.
  - A recorder that vanishes AFTER a successful boot does NOT bring the notice
    back: a wall of real birds is better company than an apology, and the frame
    is an ornament, not a status board. It keeps the last wall and retries
    quickly until the recorder returns.
  - The notice is drawn through the same mask-and-stamp path as the wall's
    captions, so its type is crisp rather than dithered into speckle, and it
    reads the right way up on a panel hung portrait.
  - `BP_FRAME_SEARCH_SECONDS=0` disables the notice; the frame still looks, it
    just never says so.
- **2026-09-03** — **The table model is a second deployment shape, not a
  replacement.** Alongside the dev/laptop wall and the Phase 4 two-box
  installation (recorder Pi + e-paper frame fetching `/wall.png`), there is now
  a self-contained unit: one Pi 5 driving an official Touch Display 2, running
  the REAL browser wall in a Chromium kiosk, with a USB microphone in the unit,
  sitting in an open window. It drops `/wall.png` entirely — the frame loads
  the wall page itself. Hardware is bought for **two units**, plus both a 7"
  (720×1280) and a 10" portrait (1200×1920) panel, to be built and compared
  side by side rather than chosen on a spec sheet (#127). Both units leave the
  house to live with friends and family, unattended.
  - All three shapes must keep working. `wall_layout.py` and `/wall.png`
    (`style=wall|panel`, `layer=picture|text`) are the e-paper installation's
    and stay untouched by table-model work; the JS↔Python layout parity test
    is the guard.
  - Storage is a high-endurance microSD, not NVMe, because the DRAM/NAND
    shortage of 2026 put a 500 GB NVMe at ~€119 against ~€38 for a 128 GB
    high-endurance card. The robustness that mattered — surviving a power cut
    in someone else's house — comes from a read-only overlayfs root and an
    archive on its own writable partition (#124), which is medium-independent.
- **2026-09-03** — **The table model's panel tuning is configured by QUERY
  STRING, not `BP_*` env vars.** `?spread=` (a floor on the collage's cluster
  width) and `?caption=` (a multiplier on the lettering) are read by
  `index.html` from `location.search`, against this repo's otherwise-universal
  env-var convention. Reasons, in order of weight:
  - These are properties of the DISPLAY, not of the service. One recorder can
    serve several viewers at once — a phone, a laptop, the panel — and an env
    var would force one panel's tuning onto all of them.
  - The kiosk URL is already where a unit is configured (#120), so this adds
    no new configuration surface and needs no restart to try a value — which
    is the whole point during the 7"-vs-10" comparison (#127).
  - It adds no new *route* and no new service configuration. (It does not, as
    an earlier draft of this entry claimed, leave `/api` untouched: `GET /`
    grew a documented `params` list like `/wall.png`'s, and a guard pins those
    bounds to the constants in `layout.js`. Review round 1 caught the claim.)
  The cost: the values are untrusted input. `normalizePanelOpts` in
  `layout.js` is the single sanitising chokepoint, shared by the module and
  the page so the CSS custom property and the layout's caption reserve can
  never receive different numbers. A `BP_*` default remains available later if
  a unit ever needs one baked in.
- **2026-09-03** — **The table model places birds exactly as the e-paper
  panel does** (owner: "Harmonize the bird placement with the rendered image
  of the e-paper display. The way the birds are placed should be exactly the
  same."). Not by porting `frame_layout` to JavaScript: the panel's focal
  scatter is fed by measurements a browser cannot reproduce — each bird's ink
  found with scipy, each caption measured with the house serif's PIL metrics,
  Mersenne-Twister jitter — so a port could match the arithmetic and still
  not match the picture, and the JS↔Python parity test exists precisely
  because two implementations of one layout drift. Instead the SERVER owns
  the plan: `render.plan_wall` is the one function that decides placement,
  `render_wall_png` draws it, and a new `GET /api/layout` serves it as JSON
  for any viewport. The browser wall's `?style=panel` (the kiosk URL) fetches
  that plan for its own size and applies it — bird-shaped cells, the panel's
  fixed-size type — computing nothing itself; each cell shows
  `/images/<file>?bare=1`, the bird as the frame pastes it (the same ink crop
  and ground key-out, done server-side — review found a client-side crop left
  the plate's ground magnified under the bird as a darker oval). `/wall.png` output is byte-identical before and after the
  extraction (checked across every style × layer × four sizes). The default
  `/` keeps the spiral, so the dev wall and the e-paper installation are
  untouched; `?spread=`/`?caption=` remain the spiral's knobs and don't apply
  in panel mode. **The ground stays the cream paper in the browser** (owner,
  2026-09-03: "the ground stays cream for the webview") — the screen can
  multiply-blend onto paper where e-paper can't, so the two shapes share a
  placement and keep their own grounds. Not a follow-on; decided. For the table model this also retires the spiral's caption-metrics
  gap (#133/#136): the panel plan measures captions server-side.
- **2026-09-03** — **The table model boots without showing a Pi.** Owner:
  "boot without showing any Pi interfaces". From power-on to the wall the
  unit showed the rainbow square, the Pi's plymouth theme, the greeter's
  wallpaper, then a desktop with a taskbar until Chromium came up. Each is
  now the wall's own paper: `disable_splash=1`; a `birdpainter` plymouth
  theme of the same shape as Pi OS's `pix` (one image, no messages),
  pre-rotated to the panel's native portrait because plymouth paints before
  the compositor rotates; the greeter and the desktop wallpaper set to the
  landscape splash; the taskbar and its `lwrespawn` killed from the labwc
  autostart. All of it fails safe — a wrong theme or wallpaper never stops a
  boot, and the kernel line is not touched. The splash is generated on the
  unit from a fixture plate (`scripts/make_splash.py`) so no archive image
  is committed.
- **2026-09-03** — **Night mode: the wall goes dark on a schedule** (#122).
  A backlit panel showing a cream wall is a lamp; the table model lives in
  living rooms. Between `BP_NIGHT_FROM` and `BP_NIGHT_TO` (local hours,
  default 22–7) the service dims a panel's backlight to
  `BP_NIGHT_BRIGHTNESS` percent (default 20) by writing
  `/sys/class/backlight/*/brightness` directly — the Touch Display 2 exposes
  one, group-writable by `video` — and `/api/live` carries `night: true`,
  on which the page fades a dark wash over itself. Two mechanisms because
  they cover different screens: the wash is all a display without a
  backlight knob (an HDMI panel, a laptop) gets, and on the table model the
  two stack. The backlight is written only on transitions, never on every
  tick, so a hand adjustment during the day is not fought (the day level is
  re-read at each dusk; `BP_NIGHT_DAY_BRIGHTNESS` pins it instead). The same
  hour twice means never. The flag rides `/api/live` rather than the
  `/api/unit` the issue sketched: the page polls `/api/live` already, and one
  poll is the budget. On by default everywhere — the recorder and the
  e-paper frame have no backlight and no page in front of anyone, so it
  costs them a boolean. The schedule is per unit (env), and the settings
  screen (#123) will edit it in place.
- **2026-09-03** — **The 7" table model runs bigger type and fewer birds**
  (owner, on seeing the first unit: "increase the font size by 50% and limit
  the amount of birds in the frame to 3"). Two per-unit settings, on the
  kiosk URL / `.env` rather than in code: `?caption=1.5` on `/api/layout`,
  which scales the panel's fixed-size type *through the plan* so the room
  reserved under each bird and the measured caption widths grow with it (a
  CSS-only scale is how #132 put labels on birds), and `BP_WALL_MAX_LIVE=3`.
  The e-paper frame and the 10" keep their own values. Bounds for the
  caption scale (0.5–2) are the same as the spiral's `?caption=`, pinned by
  a test, so one number in a URL means one thing on every wall. The archive
  chrome — corner button, overlay heading and close, "more", card lettering
  — has its own knob, `?ui=` (same bounds; the 7" runs 1.5), because it is
  the page's business rather than the plan's, and a frame may want its
  controls and its captions sized differently (owner, same day: "make this a
  configurable attribute so we can quickly switch per frame size"). The
  two scales live on the kiosk URL (`?caption=`, `?ui=`) and the bird cap in
  `.env` (`BP_WALL_MAX_LIVE`); the table-model install script (#145) will write
  that URL from per-unit variables. One deliberate change for the desktop
  wall: its archive overlay used to scale with `?caption=` by accident and
  now follows `?ui=` only.
