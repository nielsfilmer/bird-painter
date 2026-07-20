# bird-painter — hardware BOM & setup

Phase 4 takes bird-painter off the laptop and into the room. This is the
**procurement + setup doc** (issue #48): what to buy, why, and how it fits
together. The software slices (#49 `/wall.png` render, #50 e-paper client, #51
recorder-on-a-Pi) follow once the parts below are pinned.

> Prices are rough 2026 figures and drift — treat the linked official product
> pages as the source of truth. Parts are chosen for EU availability
> (Kiwi Electronics NL is the natural distributor for a Dutch build; Pimoroni
> UK and The Pi Hut ship to the EU).

---

## The two boxes

Per the Phase 4 architecture decision (tracker #47): **one app instance, thin
frame client.** Nothing about the pipeline splits.

```
  ┌─ RECORDER (outside / by a window) ─────────┐        ┌─ FRAME (living room) ──────┐
  │  Raspberry Pi + USB mic                    │        │  Raspberry Pi 4B           │
  │  runs the existing bird-painter app:       │  WiFi  │  + Waveshare 13.3" Spectra6│
  │  capture → BirdNET → fal paint → store     │ ─────▶ │  thin client: GET /wall.png│
  │  + serves /api/live and /wall.png          │        │  every few min → e-paper   │
  └────────────────────────────────────────────┘        └────────────────────────────┘
```

- The **recorder** is just the app we already have, running headless on a Pi
  with a mic. It gains one new endpoint (`/wall.png`, slice #49) — a
  server-rendered raster of the "heard recently" collage, because an e-paper
  frame can't run the browser wall.
- The **frame** is a dumb display: a small Pi with the e-paper HAT running a
  ~30-line client (slice #50) that fetches `/wall.png` on a timer and pushes it
  to the panel. It does no capture, no painting.
- Both sit on the home WiFi; only the fal image call leaves the house
  (unchanged — "local ears, cloud brush").

You can also collapse both roles onto **one** indoor Pi (mic on the windowsill,
the e-paper HAT on the same Pi) if you don't want a separate outdoor box — the
software doesn't care. The two-box split is what makes the "recorder outside,
frame in the living room" concept.

---

## Bill of materials

### Frame (living room) — the display

| Part | Recommended | ~€ | Why |
|---|---|---|---|
| E-paper panel | **Waveshare 13.3" E Ink Spectra 6 (E6)** — **6-colour**, **1600×1200 (~200 PPI)**, SPI, with the HAT+ standard driver (Pi 4B/5) | ~200–260 | The big one on purpose: 5× the pixels of the 7.3" (1600×1200 = 1.92 M px vs 800×480 = 0.38 M). Colour e-paper is 6-colour in the current generation (Spectra 6; the older 7-colour ACeP is discontinued and looked worse), but at ~200 PPI the dithering reads as continuous tone — a fine art-print look that stays true to the naturalist-plate aesthetic, no glow, near-zero idle power. |
| Display controller | **Raspberry Pi 4 Model B (2 GB)** | ~45 | The 13.3" HAT+ driver targets the Pi 4B/5 40-pin+ header. The frame's own job (fetch a PNG, push over SPI) is trivial — 2 GB is plenty; a Zero 2 W *may* drive the raw panel but the packaged HAT+ is spec'd for Pi 4/5, so a Pi 4 is the safe pick. |
| Storage | microSD 16–32 GB (A1) | ~8 | OS for the frame Pi. |
| Power | Official Pi 4 USB-C PSU (5 V/3 A) | ~9 | Wall power; the panel only draws while refreshing. |
| Enclosure | Deep box frame sized to the ~**300 × 230 mm** panel (13.3" ≈ 270 × 200 mm active area) — an IKEA Ribba/Sannahed deep frame or a custom mat | ~15–25 | Indoors, no weatherproofing. Bigger than the 7.3", so a larger frame + a mat to hide the bezel. |

**Render target for slice #49: 1600 × 1200, landscape, 6-colour palette**
(Spectra 6: black, white, red, green, blue, yellow — render full-colour at
1600×1200 and let the panel's driver library dither; don't pre-quantise). The
extra resolution is what buys the art-print look, so the collage render should
target the full 1600×1200.

Notes on the bigger panel:
- **Refresh is slower** than the 7.3" — a 13.3" Spectra 6 full update is on the
  order of **~25–35 s**. Fine for an ambient frame updated every few minutes;
  it just means no snappy updates (which we don't want anyway).
- **Cost/size step up** (~€200+ vs ~€85, and a bigger Pi) — the price of
  keeping the paper feel *and* getting real resolution. If that's too much, the
  fallback is a full-colour matte IPS panel running the browser wall directly
  (drops the `/wall.png` render), at the cost of the paper/no-glow quality —
  see "Display alternatives" below.

### Display alternatives (considered, not chosen)

- **Full-colour matte IPS + Pi (kiosk browser).** ~10–13" 1920×1200 matte IPS
  (~€80–120) + a Pi showing the existing wall page in a kiosk browser. Full
  16-M colour, sharp, and it **drops slice #49 entirely** (the frame loads the
  real wall). Trade-off: it's a backlit screen — glows, higher power, less
  "painting on paper." Chosen against because the paper/ambient quality is core
  to the concept.
- **Repurposed tablet.** Cheapest; same kiosk-browser approach; most
  screen-like, plus battery/burn-in caveats.

### Recorder (outside / by an open window) — the ears

| Part | Recommended | ~€ | Why |
|---|---|---|---|
| Computer | **Raspberry Pi 4 Model B, 4 GB** (value) — or **Pi 5, 4 GB** (comfortable) | ~55 / ~65 | BirdNET-Pi officially runs on a Pi 3B+/Zero 2 W and up, but our recorder also paints + serves, so a Pi 4 gives comfortable headroom; Pi 5 runs TF-Lite ~5× faster than Pi 4 for snappier detection. A Zero 2 W (512 MB) works but is slow and RAM-tight — not ideal for the ears. |
| Storage | microSD 32 GB (A1/A2) | ~10 | OS + the growing painting archive (see #19-era note: archive is unbounded; a bigger card or a USB SSD if it runs for months). |
| Microphone | **Plug-and-play USB mic, mono** (e.g. a USB lavalier/omni like a Boya BY-series, or a simple USB mini-mic) + **foam windscreen** | ~15–30 | BirdNET wants sensitivity, not hi-fi — flat-ish to ~12 kHz, no distortion. **Mono** (BirdNET processes mono anyway). Avoid separate USB sound-cards (ground-loop buzz). |
| USB extension | 1–2 m USB extension cable | ~5 | Move the mic away from the Pi to dodge the Pi's electromagnetic noise, and to reach the window/eave while the Pi stays dry. |
| Power | Official USB-C PSU (Pi 4: 5 V/3 A; Pi 5: 5 V/5 A) | ~12 | Always-on. |
| Weatherproofing | Under-eave mount + a small vented/IP-rated enclosure for the Pi; mic pointed at the garden with the windscreen; keep the Pi itself dry | ~15 | The mic can face the weather (windscreen on); the Pi should not. Simplest: Pi indoors on the windowsill, mic out through a cracked/adjacent window on the extension. |

---

## Procurement checklist (your side)

One batched order. All parts are EU-available; **Kiwi Electronics (kiwi-electronics.com, NL)**
carries the Pis, e-paper panels, and mics, or split across Pimoroni/The Pi Hut.

- [ ] Waveshare 13.3" E Ink Spectra 6 (E6), 1600×1200, with HAT+ driver — https://www.waveshare.com/13.3inch-e-paper-hat-plus-e.htm
- [ ] Raspberry Pi 4 Model B 2 GB (frame controller) — official reseller
- [ ] Raspberry Pi 4 Model B 4 GB (recorder) — or Pi 5 4 GB — official reseller
- [ ] 2× microSD (32 GB) + a card reader
- [ ] USB mic (mono) + foam windscreen + USB extension cable
- [ ] PSUs: 2× Pi 4/5 USB-C (frame + recorder)
- [ ] Deep box frame + mat sized to the ~300 × 230 mm panel
- [ ] (Recorder) small vented enclosure / under-eave mount

**Paste back to me:** just the panel + recorder models you actually buy (so I
pin the `/wall.png` resolution and the ARM install notes to them) — no
credentials or anything the vendor mints.

---

## Buying in the Netherlands

Prefer **NL/EU vendors** — post-Brexit, UK shops (Pimoroni, The Pi Hut) add
customs + import VAT + handling + delay at the border, and Waveshare-direct
(China) means import clearance and ~2–3 weeks. Everything below is NL/EU with
VAT included.

| What | Where (NL) | Note |
|---|---|---|
| **Waveshare 13.3" Spectra 6 panel** | **Amazon.nl** — get the **"with HAT+ Standard Driver HAT"** SKU (ASIN **B0DPBW2R25**), *not* the raw "without Driver Board" (B0DPBTT286, which needs a separate driver) | The reliable in-stock path. Antratek (Rotterdam, the NL Waveshare distributor) lists it domestically but was **out of stock + raw-panel-only** at time of writing — set a back-in-stock alert there if you'd rather buy domestic, but don't wait on it. |
| **Raspberry Pi 4B** (2 GB frame + 4 GB recorder), **PSUs, microSD** | **Kiwi Electronics** (kiwi-electronics.com) — official Pi reseller, ships from NL (PostNL/DHL), same-day if ordered before 17:00 | One order covers both Pis + USB-C PSUs + SD cards. Other NL approved resellers: RaspberryStore, SOS Solutions, Antratek, Elektor. |
| **USB mic (mono) + foam windscreen** | SOS Solutions / Elektronica voor jou (mini USB mic), or bol.com / Amazon.nl | Commodity — cheapest via bol.com/Amazon.nl. Windscreen from bol.com/Amazon.nl. |
| **microSD (32 GB)** | TinyTronics (tinytronics.nl, cheap) or with the Kiwi order | Either works. |
| **Deep box frame + mat** | IKEA (Sännahed/Ribba deep frame) + a cut mat; bol.com for a custom mat | Sized to the ~300 × 230 mm panel. |

**Simplest split:** one **Kiwi Electronics** order (both Pis + PSUs + SD) + one
**Amazon.nl** order (the panel, HAT+ SKU) + an **IKEA/bol.com** run (frame, mic,
windscreen). All NL/EU, no customs.

---

## Setup outline (the detail lands in slices #50 / #51)

**Recorder Pi:**
1. Flash Raspberry Pi OS Lite (64-bit) — headless, SSH + WiFi preconfigured via
   Raspberry Pi Imager.
2. `sudo apt install python3-venv libportaudio2`, clone the repo,
   `pip install -e .` (birdnetlib pulls a TF-Lite wheel — #51 verifies the ARM
   wheel installs; a Pi build may need `tflite-runtime`).
3. Pick the mic: `python -m bird_painter --list-devices`, set `BP_INPUT_DEVICE`.
4. Put `FAL_KEY` (and optional `BP_FAL_MODEL=fal-ai/flux/dev`) in `.env`.
5. Autostart via a `systemd` unit (`bird_painter.service`) so it runs on boot.

**Frame Pi:**
1. Flash Raspberry Pi OS Lite, enable SPI.
2. Install the panel driver — Waveshare's **`epd13in3E`** driver for the 13.3" Spectra 6 HAT+ (E). NB: that's a different panel from Waveshare's *grayscale* 13.3" (which uses the IT8951 controller) — don't grab the IT8951 lib.
3. Run the slice-#50 client: fetch `http://<recorder>:8537/wall.png` on a timer
   (respecting the panel's ~25–35 s refresh — update every few minutes, not
   seconds) and push to the panel.

---

## Open notes / decisions

- **Refresh cadence.** The 13.3" Spectra 6 takes ~25–35 s for a full redraw, and colour e-paper
  shouldn't be hammered — a **few-minutes** update cadence suits an ambient
  frame (the wall's own TTL is hours). The live browser wall keeps its 5 s poll;
  only the frame is slow.
- **Colour fidelity.** Spectra 6 is 6 fixed colours (black, white, red, green,
  blue, yellow) with dithering — the vintage-cutout birds will read well but
  won't be photographic. #49 should render at 1600×1200 and let the panel's driver library
  do the palette dithering (don't pre-quantise).
- **Outdoor power/rain** is the fiddliest bit — the pragmatic v0 is *mic
  outside, Pi inside*, which sidesteps enclosure/IP concerns entirely.
- **Archive growth** on the recorder's SD (follow-up from earlier phases) — a
  months-long run wants a retention cap or a bigger card / USB SSD.
