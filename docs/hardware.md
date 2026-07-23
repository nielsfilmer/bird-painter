# bird-painter — hardware BOM & setup

Phase 4 takes bird-painter off the laptop and into the room. This is the
**procurement + setup doc** (issue #48): what to buy, why, and how it fits
together. The software slices (#49 `/wall.png` render, #50 e-paper client, #51
recorder-on-a-Pi) follow once the parts below are pinned.

> Prices are rough 2026 figures and drift, and vendors vary by region — treat
> the linked official product pages as the source of truth and buy from a
> [Raspberry Pi Approved Reseller](https://www.raspberrypi.com/resellers/) in
> your own region. See "Sourcing tips" below for the one part that's easy to
> order wrong.

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

## Procurement checklist

- [ ] Waveshare 13.3" E Ink Spectra 6 (E6), 1600×1200, **with HAT+ driver** — https://www.waveshare.com/13.3inch-e-paper-hat-plus-e.htm
- [ ] Raspberry Pi 4 Model B 2 GB (frame controller)
- [ ] Raspberry Pi 4 Model B 4 GB (recorder) — or Pi 5 4 GB
- [ ] 2× microSD (32 GB) + a card reader
- [ ] USB mic (mono) + foam windscreen + USB extension cable
- [ ] PSUs: 2× Pi 4/5 USB-C (frame + recorder)
- [ ] Deep box frame + mat sized to the ~300 × 230 mm panel
- [ ] (Recorder) small vented enclosure / under-eave mount

## Sourcing tips

- **Get the panel *with* the driver.** Waveshare sells the 13.3" Spectra 6 in
  two near-identical listings: the **HAT+ (E) *with* the driver board** (what
  you want) and a **raw panel *without* a driver** (needs a separate driver —
  don't buy this by mistake). There's also a same-size **(B) red/black/white
  3-colour** panel — not the 6-colour Spectra 6. Read the title carefully.
- **Buy Pis from an
  [Approved Reseller](https://www.raspberrypi.com/resellers/) in your region**
  — they carry the boards, official USB-C PSUs, and microSD, usually in one
  order.
- **Prefer an in-region / same-customs-union seller** to avoid import duties,
  VAT-on-import, and courier clearance fees. Ordering the panel direct from the
  manufacturer (e.g. China) is often *not* cheaper once import VAT + a handling
  fee + a multi-week wait are added; a regional distributor or a price-
  comparison search for the *with-driver* SKU usually wins on landed cost and
  speed.
- The mic, windscreen, USB extension, microSD, and picture frame are
  commodities — any general electronics/hardware shop or marketplace has them.

---

## Setup

### Recorder Pi (the ears)

1. Flash **Raspberry Pi OS Lite (64-bit)** with
   [Raspberry Pi Imager](https://www.raspberrypi.com/software/) — in its
   settings, set the hostname (e.g. `birdrecorder`), enable SSH, and
   preconfigure your WiFi + user. Boot and `ssh` in.
2. Install system deps and the app:
   ```bash
   sudo apt update && sudo apt install -y git python3-venv python3-dev libportaudio2
   git clone https://github.com/nielsfilmer/bird-painter && cd bird-painter
   python3 -m venv .venv && .venv/bin/pip install -e .
   ```
   `birdnetlib` pulls TensorFlow/TF-Lite. If the `tensorflow` wheel won't
   install on your Pi, install `tflite-runtime` instead — this is exactly what
   slice **#51** verifies on real ARM hardware.
3. Plug in the USB mic (via the extension cable), then pick it:
   ```bash
   .venv/bin/python -m bird_painter --list-devices
   ```
   Put the index (or a name substring) in `.env` as `BP_INPUT_DEVICE`.
4. Fill in `.env` (copy `.env.example` first): `FAL_KEY=…`, and optionally
   `BP_FAL_MODEL=fal-ai/flux/dev` for nicer paintings (**non-commercial** — see
   Licenses; `schnell` is Apache-2.0) and `BP_LATITUDE`/`BP_LONGITUDE` to filter
   to local species.
5. Smoke-test in the foreground, then browse to `http://birdrecorder.local:8537`
   from your laptop and confirm birds paint as they're heard:
   ```bash
   .venv/bin/python -m bird_painter --no-prompt
   ```
6. Autostart on boot with a `systemd` unit — `/etc/systemd/system/bird-painter.service`:
   ```ini
   [Unit]
   Description=bird-painter recorder
   After=network-online.target
   Wants=network-online.target

   [Service]
   User=pi
   WorkingDirectory=/home/pi/bird-painter
   ExecStart=/home/pi/bird-painter/.venv/bin/python -m bird_painter --no-prompt
   Restart=always

   [Install]
   WantedBy=multi-user.target
   ```
   Then `sudo systemctl enable --now bird-painter`. (Adjust `User`/paths if not
   the default `pi` user.)

### Frame Pi (the display)

1. Flash **Raspberry Pi OS Lite (64-bit)** (hostname e.g. `birdframe`, SSH +
   WiFi as above). `ssh` in.
2. **Power the Pi off**, seat the 13.3" Spectra 6 HAT+ on the 40-pin header,
   power back on. Enable SPI: `sudo raspi-config nonint do_spi 0`, then reboot.
3. Install the app (frame side only needs `httpx`/`pillow`/`numpy`, **not** the
   BirdNET/TF stack), plus the Waveshare driver + its GPIO deps:
   ```bash
   sudo apt update && sudo apt install -y git python3-venv python3-dev python3-pip
   git clone https://github.com/nielsfilmer/bird-painter && cd bird-painter
   python3 -m venv --system-site-packages .venv
   .venv/bin/pip install --no-deps -e . && .venv/bin/pip install httpx pillow numpy
   .venv/bin/pip install spidev gpiozero lgpio          # GPIO stack (Bookworm)
   git clone https://github.com/waveshareteam/e-Paper ~/e-Paper
   ```
   > ⚠️ **The Spectra 6 (E) driver is NOT in the main `waveshare_epd` package.**
   > That package only ships `epd13in3b` (3-colour) and `epd13in3k` (mono). The
   > 6-colour Spectra 6 driver is a **flat `epd13in3E` module** under
   > `e-Paper/E-paper_Separate_Program/13.3inch_e-Paper_E/RaspberryPi/python/lib`.
   > (And it's a different panel from the *grayscale* 13.3", which uses the
   > IT8951 lib — don't grab that.)

   Set `BP_FRAME_DRIVER_PATH` to that `lib` dir so the client can find it, and
   verify the wiring first with Waveshare's own demo:
   ```bash
   DRV=~/e-Paper/E-paper_Separate_Program/13.3inch_e-Paper_E/RaspberryPi/python
   PYTHONPATH=$DRV/lib .venv/bin/python $DRV/examples/epd_13in3E_test.py
   ```
   If that draws the demo, the panel + SPI are good.
4. Point the frame client at the recorder and run it:
   ```bash
   DRV=~/e-Paper/E-paper_Separate_Program/13.3inch_e-Paper_E/RaspberryPi/python
   BP_FRAME_SOURCE=http://birdrecorder.local:8537/wall.png \
   BP_FRAME_INTERVAL_SECONDS=300 \
   BP_FRAME_DRIVER_PATH=$DRV/lib \
   .venv/bin/python -m bird_painter.frame_client
   ```
   It fetches `/wall.png` every few minutes (the panel takes ~25–35 s per full
   redraw, so don't go faster), dithers it to the six panel colours, and only
   redraws when the wall actually changed. Autostart it with its own `systemd`
   unit (`bird-painter-frame.service`, same shape as the recorder's but
   `ExecStart=…/.venv/bin/python -m bird_painter.frame_client` and the
   `BP_FRAME_*` values — including `BP_FRAME_DRIVER_PATH` — as `Environment=`
   lines).

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
