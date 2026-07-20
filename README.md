# bird-painter

An ambient installation: a microphone listens outdoors, BirdNET recognises
which birds are singing right now, each newly-heard species is painted by a
hosted image model in a fixed vintage-naturalist style, and the paintings show
on a full-screen local "wall" that fades them out after a few hours — so the
wall stays a fresh reflection of what's been heard. **Local ears, cloud
brush:** one Python process does capture → recognition → image call → archive
→ serves the wall; only the image call leaves the house.

The full concept, pipeline, and design decisions live in [`PLAN.md`](PLAN.md).

## How it works

```
mic → BirdNET (local) → trigger gate → FLUX image (fal.ai) → archive → wall
```

- **Ears:** [BirdNET](https://birdnet.cornell.edu/) via `birdnetlib`, running
  locally. Non-bird labels (noise, human, machine, insects) are filtered out.
- **Trigger gate:** a per-species cooldown + a per-hour cap, so a chatty bird
  can't spam the wall or the image bill.
- **Brush:** [FLUX](https://blackforestlabs.ai/) on [fal.ai](https://fal.ai),
  a single stateless REST call, in a fixed house style.
- **Wall:** a full-screen web page served locally — a collage of the currently
  "live" paintings that fade in when heard and out after their TTL.

## Quickstart

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
cp .env.example .env          # then put your fal.ai key in FAL_KEY
.venv/bin/python -m bird_painter
```

Open the wall at `http://127.0.0.1:8537`. Without a `FAL_KEY` it runs in a
placeholder mode (the wall works, birds paint as simple plates). Run with the
mic listener disabled via `BP_ENABLE_LISTENER=false` for wall-only / testing.

Configuration knobs (TTL, confidence floor, per-hour cap, mic device, image
model, …) are environment variables — see [`.env.example`](.env.example) and
`bird_painter/config.py`.

## Hardware

To run it as a standalone installation (a recorder + an e-paper frame), see the
generic hardware BOM & setup guide in [`docs/hardware.md`](docs/hardware.md). The
frame can't run the browser wall, so the app also serves the collage rendered
server-side as a PNG at `/wall.png` (default 1600×1200, configurable) for a thin
client to fetch and display.

## Development

```bash
make review-checks    # ruff + pytest + the wall-layout node tests
```

## Licenses

The **source code in this repository is MIT-licensed** (see [`LICENSE`](LICENSE)).

But the *running system* depends on third-party models whose licenses are more
restrictive than MIT — most importantly, **bird-painter as a whole is intended
for personal / non-commercial use**, because:

| Component | License | Note |
|---|---|---|
| This repo's code | **MIT** | Do what you like. |
| `birdnetlib` (code) | Apache-2.0 | The Python wrapper. |
| **BirdNET models** | **CC BY-NC-SA 4.0** | **Non-commercial**, attribution, share-alike. This is the binding constraint — running the recognition is non-commercial use. |
| **FLUX.1 [schnell]** (default brush) | Apache-2.0 | Commercial use permitted. |
| **FLUX.1 [dev]** (`BP_FAL_MODEL=fal-ai/flux/dev`) | Non-commercial | Switching to `dev` makes image generation non-commercial too. |

If you use bird-painter, honour those upstream licenses (BirdNET attribution +
non-commercial in particular). This project is a personal toy and is provided
as-is.
