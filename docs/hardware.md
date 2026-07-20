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
  │  Raspberry Pi + USB mic                    │        │  Raspberry Pi Zero 2 W     │
  │  runs the existing bird-painter app:       │  WiFi  │  + Inky Impression 7.3"    │
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
Inky HAT on the same Pi) if you don't want a separate outdoor box — the
software doesn't care. The two-box split is what makes the "recorder outside,
frame in the living room" concept.

---

## Bill of materials

### Frame (living room) — the display

| Part | Recommended | ~€ | Why |
|---|---|---|---|
| E-paper panel | **Pimoroni Inky Impression 7.3" (2025 Edition)** — Spectra 6, **7-colour**, **800×480**, ~12 s full refresh, 40-pin HAT, no soldering | ~85 | Full colour is the whole point — our birds are painterly, so a red/black/white or mono panel would gut them. Purpose-built for photo frames; the newer Spectra 6 panel has more saturated colour + faster refresh than the older ACeP one. |
| Display controller | **Raspberry Pi Zero 2 WH** (with pre-soldered header) | ~20 | The Inky is a HAT — it needs a Pi behind it. The frame only fetches a PNG and pushes it, so a Zero 2 W is plenty. |
| Storage | microSD 16–32 GB (A1) | ~8 | OS for the Zero. |
| Power | USB micro-B PSU (5 V/2.5 A) | ~9 | Wall power; the panel only draws while refreshing. |
| Enclosure | IKEA frame (the board is **174 × 123 mm**, fits a **180 × 130 mm** aperture) or Pimoroni's wooden frame accessory | ~10 | Indoors, so no weatherproofing. |

**Render target for slice #49: 800 × 480, landscape, 7-colour palette** (Spectra
6: black, white, red, green, blue, yellow — the Inky library handles dithering
to that palette).

Alternative if you'd rather not run a Pi for the frame: **Pimoroni Inky Frame
7.3"** — same panel with a Pico 2 W microcontroller aboard, self-contained,
fetches over WiFi. Trade-off: MicroPython, so our Python client (#50) would be
reworked; less flexible. The Impression + Zero 2 W is the more hackable path.

### Recorder (outside / by an open window) — the ears

| Part | Recommended | ~€ | Why |
|---|---|---|---|
| Computer | **Raspberry Pi 4 Model B, 4 GB** (value) — or **Pi 5, 4 GB** (comfortable) | ~55 / ~65 | BirdNET (TF-Lite) needs a Pi 4 minimum; Pi 5 runs TF-Lite ~5× faster than Pi 4 for snappier detection + headroom. A Zero 2 W (512 MB) *can* run BirdNET but is slow and RAM-tight — fine for the frame, not ideal for the ears. |
| Storage | microSD 32 GB (A1/A2) | ~10 | OS + the growing painting archive (see #19-era note: archive is unbounded; a bigger card or a USB SSD if it runs for months). |
| Microphone | **Plug-and-play USB mic, mono** (e.g. a USB lavalier/omni like a Boya BY-series, or a simple USB mini-mic) + **foam windscreen** | ~15–30 | BirdNET wants sensitivity, not hi-fi — flat-ish to ~12 kHz, no distortion. **Mono** (BirdNET processes mono anyway). Avoid separate USB sound-cards (ground-loop buzz). |
| USB extension | 1–2 m USB extension cable | ~5 | Move the mic away from the Pi to dodge the Pi's electromagnetic noise, and to reach the window/eave while the Pi stays dry. |
| Power | Official USB-C PSU (Pi 4: 5 V/3 A; Pi 5: 5 V/5 A) | ~12 | Always-on. |
| Weatherproofing | Under-eave mount + a small vented/IP-rated enclosure for the Pi; mic pointed at the garden with the windscreen; keep the Pi itself dry | ~15 | The mic can face the weather (windscreen on); the Pi should not. Simplest: Pi indoors on the windowsill, mic out through a cracked/adjacent window on the extension. |

---

## Procurement checklist (your side)

One batched order. All parts are EU-available; **Kiwi Electronics (kiwi-electronics.com, NL)**
carries the Pis, Inky, and mics, or split across Pimoroni/The Pi Hut.

- [ ] Inky Impression 7.3" 2025 Edition — https://shop.pimoroni.com/products/inky-impression-7-3
- [ ] Raspberry Pi Zero 2 WH (frame controller) — official reseller
- [ ] Raspberry Pi 4 Model B 4 GB (recorder) — or Pi 5 4 GB — official reseller
- [ ] 2× microSD (32 GB) + a card reader
- [ ] USB mic (mono) + foam windscreen + USB extension cable
- [ ] PSUs: Pi 4/5 USB-C, Zero micro-B
- [ ] Picture frame (~180 × 130 mm aperture) for the Inky
- [ ] (Recorder) small vented enclosure / under-eave mount

**Paste back to me:** just the panel + recorder models you actually buy (so I
pin the `/wall.png` resolution and the ARM install notes to them) — no
credentials or anything the vendor mints.

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
2. Install the Inky library (`pip install inky[rpi]`).
3. Run the slice-#50 client: fetch `http://<recorder>:8537/wall.png` on a timer
   (respecting the panel's ~12 s refresh — update every few minutes, not
   seconds) and push to the panel.

---

## Open notes / decisions

- **Refresh cadence.** The Inky takes ~12 s to redraw and colour e-paper
  shouldn't be hammered — a **few-minutes** update cadence suits an ambient
  frame (the wall's own TTL is hours). The live browser wall keeps its 5 s poll;
  only the frame is slow.
- **Colour fidelity.** Spectra 6 is 7 fixed colours with dithering — the
  vintage-cutout birds will read well but won't be photographic. #49 should
  render at 800×480 and let the Inky library do the palette dithering (don't
  pre-quantise).
- **Outdoor power/rain** is the fiddliest bit — the pragmatic v0 is *mic
  outside, Pi inside*, which sidesteps enclosure/IP concerns entirely.
- **Archive growth** on the recorder's SD (follow-up from earlier phases) — a
  months-long run wants a retention cap or a bigger card / USB SSD.
