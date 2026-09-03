// Deterministic guard for the wall's collage layout — run by `node --test`
// inside `make review-checks`. This is the automated form of the hand-run
// simulation that caught the PR #31 no-overlap regression.

import test from "node:test";
import assert from "node:assert/strict";
import { computeCollage, hash, normalizePanelOpts, overlapArea } from "./layout.js";

const PLATE_ASPECT = 5 / 4;
const SLUGS = [
  "european-robin", "great-tit", "blue-tit", "dunnock", "song-thrush",
  "eurasian-wren", "common-chaffinch", "willow-warbler", "eurasian-blackcap",
  "house-sparrow", "common-blackbird", "goldcrest", "chiffchaff",
];

// Small deterministic PRNG so the test is reproducible (no Math.random).
function makeRng(seed) {
  let s = seed >>> 0;
  return () => {
    s = (Math.imul(s, 1103515245) + 12345) >>> 0;
    return s / 0xffffffff;
  };
}

function randomFiles(rng, n) {
  return Array.from({ length: n }, () => {
    const ts = 1784000000 + Math.floor(rng() * 1e6);
    const slug = SLUGS[Math.floor(rng() * SLUGS.length)];
    const hex = Math.floor(rng() * 0xffffffff).toString(16).padStart(8, "0");
    return `${ts}_${slug}_${hex}.jpg`;
  });
}

// Visible plate footprint (image only, no gap): what the eye sees overlap.
function footprint(p, vmin) {
  const w = p.sizeVmin * vmin;
  return { x: p.x, y: p.y, w, h: w * PLATE_ASPECT };
}

// The last two are the table model's panels — Raspberry Pi Touch Display 2 at
// 7" and 10", both natively portrait. They are pinned here (not just swept
// randomly) because the table model is read from across a room and a
// regression on either one is a regression on a physical object in somebody
// else's living room.
const PANEL_7IN = [720, 1280];
const PANEL_10IN = [1200, 1920];
const VIEWPORTS = [
  [1920, 1080], [1280, 800], [375, 812], [812, 375], [2560, 1440],
  [1345, 1245], [716, 801], [100, 100], PANEL_7IN, PANEL_10IN,
];

test("no two birds ever visibly overlap, across random sets and viewports", () => {
  for (const [W, H] of VIEWPORTS) {
    const bandTop = Math.max(64, Math.min(0.2 * H, 180));
    const vmin = Math.min(W, H) / 100;
    const rng = makeRng(0x1234 ^ (W * 31 + H));
    for (const n of [1, 4, 6, 8, 10, 12]) {
      for (let rep = 0; rep < 40; rep++) {
        const files = randomFiles(rng, n);
        const placed = computeCollage(files, W, H, bandTop);
        for (let i = 0; i < placed.length; i++) {
          for (let j = i + 1; j < placed.length; j++) {
            const ov = overlapArea(footprint(placed[i], vmin), footprint(placed[j], vmin));
            assert.ok(
              ov <= 0.5,
              `overlap ${ov.toFixed(1)}px² at ${W}x${H} n=${n} rep=${rep}`,
            );
          }
        }
      }
    }
  }
});

test("the first three birds form a single horizontal row", () => {
  // Placement rule: a wall starting its day reads as a neat shelf — up to
  // ROW_LIMIT birds sit in one horizontal row (centres level), and the row
  // must never fan to the screen edges.
  const [W, H] = [1920, 1080];
  const bandTop = 150;
  const vmin = Math.min(W, H) / 100;
  for (let seed = 1; seed <= 20; seed++) {
    for (const n of [1, 2, 3]) {
      const placed = computeCollage(randomFiles(makeRng(seed), n), W, H, bandTop);
      const ys = placed.map(p => p.y);
      const ySpread = Math.max(...ys) - Math.min(...ys);
      assert.ok(
        ySpread <= 3,
        `seed ${seed} n=${n}: not a row — y spread ${ySpread.toFixed(1)}px`,
      );
      let xReach = 0;
      for (const p of placed) {
        xReach = Math.max(xReach, Math.abs(p.x) + (p.sizeVmin * vmin) / 2);
      }
      assert.ok(xReach <= W * 0.35, `seed ${seed} n=${n}: row too wide`);
    }
    // The fourth bird ends row mode: vertical span opens up.
    const four = computeCollage(randomFiles(makeRng(seed), 4), W, H, bandTop);
    const ys4 = four.map(p => p.y);
    assert.ok(
      Math.max(...ys4) - Math.min(...ys4) > 100,
      `seed ${seed}: 4 birds still flat`,
    );
  }
});

test("newer birds stack vertically around the anchored shelf", () => {
  // The corrected rule: the three OLDEST birds keep the horizontal row at the
  // band centre for good; every newer bird sits fully above or below it —
  // never level with it (the n=12 case regressed this once: birds slotted
  // into lateral shelf gaps on a full wall).
  const [W, H] = [1920, 1080];
  const bandTop = 150;
  for (let seed = 1; seed <= 20; seed++) {
    for (const n of [4, 6, 8, 12]) {
      const placed = computeCollage(randomFiles(makeRng(seed), n), W, H, bandTop);
      const shelf = placed.slice(n - 3); // input is newest-first; oldest = tail
      for (const p of shelf) {
        assert.ok(
          Math.abs(p.y - bandTop / 2) <= 1,
          `seed ${seed} n=${n}: shelf bird drifted ${p.y.toFixed(1)}`,
        );
      }
      // The shelf's horizontal order is FIXED: oldest→newest, left→right —
      // members must never swap sides as the wall grows.
      for (let i = 0; i + 1 < shelf.length; i++) {
        assert.ok(
          shelf[shelf.length - 1 - i].x < shelf[shelf.length - 2 - i].x,
          `seed ${seed} n=${n}: shelf order reshuffled`,
        );
      }
      for (const p of placed.slice(0, n - 3)) {
        assert.ok(
          Math.abs(p.y - bandTop / 2) >= 200,
          `seed ${seed} n=${n}: newer bird level with the shelf (y=${p.y.toFixed(0)})`,
        );
      }
      const ys = placed.map(p => p.y);
      assert.ok(
        Math.max(...ys) - Math.min(...ys) >= (n >= 6 ? 450 : 200),
        `seed ${seed} n=${n}: group not growing vertically`,
      );
    }
  }
});

test("on a short wide screen the width cap leaves side margins", () => {
  // The width cap (CLUSTER_W_FRAC of the viewport) keeps a short, wide
  // display from fanning the birds edge-to-edge into one full-width band —
  // the widen-to-fit loop stops at that cap, leaving side margins.
  const [W, H] = [2400, 1000];
  const bandTop = 150;
  const vmin = Math.min(W, H) / 100;
  for (let seed = 1; seed <= 20; seed++) {
    const placed = computeCollage(randomFiles(makeRng(seed), 12), W, H, bandTop);
    let reach = 0;
    for (const p of placed) {
      reach = Math.max(reach, Math.abs(p.x) + (p.sizeVmin * vmin) / 2);
    }
    assert.ok(
      reach <= W * 0.48,
      `seed ${seed}: cluster fanned too wide — reached ${reach.toFixed(0)}px of ${W / 2}px half-width`,
    );
  }
});

test("no bird renders far smaller than the largest (minimum-size floor)", () => {
  // The circled-birds complaint: the small hash bucket looked tiny next to the
  // big one. All plates share one global scale, so the ratio is purely the
  // hash-bucket spread — the smallest must stay a healthy fraction of the
  // largest. Guards against widening SIZE_SPAN back out.
  const [W, H] = [1600, 1050];
  const bandTop = 150;
  for (let seed = 1; seed <= 20; seed++) {
    const placed = computeCollage(randomFiles(makeRng(seed), 12), W, H, bandTop);
    if (placed.length < 2) continue;
    const sizes = placed.map(p => p.sizeVmin);
    const min = Math.min(...sizes), max = Math.max(...sizes);
    assert.ok(
      min >= max * 0.8,
      `seed ${seed}: smallest bird ${min.toFixed(1)}vmin is under 80% of largest ${max.toFixed(1)}vmin`,
    );
  }
});

test("layout is deterministic for the same files + viewport", () => {
  const files = randomFiles(makeRng(7), 9);
  const a = computeCollage(files, 1280, 800, 120);
  const b = computeCollage(files, 1280, 800, 120);
  assert.deepEqual(a, b);
});

test("every plate stays on screen and below the title band", () => {
  const [W, H] = [1280, 800];
  const bandTop = 140;
  const vmin = Math.min(W, H) / 100;
  const files = randomFiles(makeRng(99), 12);
  for (const p of computeCollage(files, W, H, bandTop)) {
    const f = footprint(p, vmin);
    assert.ok(f.x - f.w / 2 >= -W / 2 - 0.5 && f.x + f.w / 2 <= W / 2 + 0.5, "on-screen x");
    assert.ok(f.y + f.h / 2 <= H / 2 + 0.5, "on-screen bottom");
    // top of the plate must clear the title band (y measured from centre)
    assert.ok(f.y - f.h / 2 >= bandTop - H / 2 - 0.5, "below the title");
  }
});

test("newest bird is on top; the shelf belongs to the oldest three", () => {
  const [W, H] = [1280, 800];
  const bandTop = 140;
  // With three or fewer birds, the newest sits ON the shelf…
  const three = computeCollage(randomFiles(makeRng(3), 3), W, H, bandTop);
  assert.ok(Math.abs(three[0].y - bandTop / 2) <= 3);
  // …with more, the newest is OFF the shelf but still z-topmost.
  const six = computeCollage(randomFiles(makeRng(3), 6), W, H, bandTop);
  assert.ok(Math.abs(six[0].y - bandTop / 2) >= 100, "newest crowded the shelf");
  assert.ok(six.every(p => p === six[0] || p.z < six[0].z), "newest not on top");
});

test("a zero-size viewport yields no placements (no 0-size plates)", () => {
  const files = randomFiles(makeRng(1), 5);
  assert.deepEqual(computeCollage(files, 0, 0, 0), []);
  assert.deepEqual(computeCollage(files, 800, 0, 0), []);
});

test("hash is stable and unsigned", () => {
  assert.equal(hash("european-robin"), hash("european-robin"));
  assert.ok(hash("x") >= 0 && hash("x") <= 0xffffffff);
});

// --- Table-model panel tuning (spread / captionScale) -----------------------

// Horizontal extent of the placed birds, as a fraction of the viewport.
function widthUsed(placements, W, vmin) {
  const left = Math.min(...placements.map(p => p.x - (p.sizeVmin * vmin) / 2));
  const right = Math.max(...placements.map(p => p.x + (p.sizeVmin * vmin) / 2));
  return (right - left) / W;
}

test("panel opts default to today's layout, exactly", () => {
  for (const [W, H] of [PANEL_7IN, PANEL_10IN, [1280, 800]]) {
    const files = randomFiles(makeRng(7), 12);
    const bandTop = 140;
    const bare = computeCollage(files, W, H, bandTop);
    // Omitted, empty, and explicitly-default opts must all be identical: the
    // desktop wall and the e-paper /wall.png render must not shift because a
    // knob exists.
    assert.deepEqual(computeCollage(files, W, H, bandTop, {}), bare);
    assert.deepEqual(
      computeCollage(files, W, H, bandTop, { spread: 0, captionScale: 1 }), bare);
  }
});

test("spread widens the collage on a portrait panel", () => {
  for (const [W, H] of [PANEL_7IN, PANEL_10IN]) {
    const files = randomFiles(makeRng(11), 12);
    const bandTop = 140;
    const vmin = Math.min(W, H) / 100;
    const narrow = widthUsed(computeCollage(files, W, H, bandTop), W, vmin);
    const wide = widthUsed(
      computeCollage(files, W, H, bandTop, { spread: 0.8 }), W, vmin);
    // The default leaves a lot of the panel's width unused — that is the
    // finding this knob exists for. Assert the direction, not a magic number.
    assert.ok(narrow < 0.7, `default already wide on ${W}x${H} (${narrow})`);
    assert.ok(wide > narrow + 0.15, `spread did not widen ${W}x${H}`);
  }
});

test("panel opts never push a bird off screen", () => {
  for (const [W, H] of [PANEL_7IN, PANEL_10IN]) {
    const bandTop = 140;
    const vmin = Math.min(W, H) / 100;
    for (const spread of [0, 0.4, 0.8, 0.92]) {
      for (const captionScale of [1, 1.7, 4]) {
        const placements = computeCollage(
          randomFiles(makeRng(13), 12), W, H, bandTop, { spread, captionScale });
        for (const p of placements) {
          const f = footprint(p, vmin);
          assert.ok(f.x - f.w / 2 >= -W / 2 - 0.5, `off left @${spread}/${captionScale}`);
          assert.ok(f.x + f.w / 2 <= W / 2 + 0.5, `off right @${spread}/${captionScale}`);
          assert.ok(f.y + f.h / 2 <= H / 2 + 0.5, `off bottom @${spread}/${captionScale}`);
          assert.ok(f.y - f.h / 2 >= bandTop - H / 2 - 0.5, `over the title @${spread}/${captionScale}`);
        }
      }
    }
  }
});

test("a bigger caption reserves more room, so labels can't land on the bird below", () => {
  const [W, H] = PANEL_7IN;
  const bandTop = 140;
  const files = randomFiles(makeRng(17), 8);
  const plain = computeCollage(files, W, H, bandTop, { spread: 0.8 });
  const big = computeCollage(files, W, H, bandTop, { spread: 0.8, captionScale: 2.5 });
  // Same set, same spread: larger lettering must either space the birds
  // further apart vertically or shrink them — never leave the gaps unchanged.
  const spanY = ps => Math.max(...ps.map(p => p.y)) - Math.min(...ps.map(p => p.y));
  const size = ps => Math.max(...ps.map(p => p.sizeVmin));
  assert.ok(
    spanY(big) > spanY(plain) + 0.5 || size(big) < size(plain) - 0.01,
    "caption scale changed neither spacing nor plate size",
  );
  assert.equal(plain.length, big.length);
});

test("panel opts are sanitised, not trusted (they come from a query string)", () => {
  const [W, H] = PANEL_7IN;
  const bandTop = 140;
  const files = randomFiles(makeRng(19), 6);
  const bare = computeCollage(files, W, H, bandTop);
  for (const junk of [
    { spread: "nonsense", captionScale: "nonsense" },
    { spread: null, captionScale: null },
    { spread: NaN, captionScale: NaN },
    { spread: -5, captionScale: 0 },
  ]) {
    assert.deepEqual(computeCollage(files, W, H, bandTop, junk), bare,
      `junk opts ${JSON.stringify(junk)} did not fall back to defaults`);
  }
  // Out-of-range values clamp rather than run away.
  assert.deepEqual(normalizePanelOpts({ spread: 99, captionScale: 99 }),
    { spread: 0.92, captionScale: 4 });
  assert.deepEqual(normalizePanelOpts({}), { spread: 0, captionScale: 1 });
});
