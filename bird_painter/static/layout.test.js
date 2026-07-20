// Deterministic guard for the wall's collage layout — run by `node --test`
// inside `make review-checks`. This is the automated form of the hand-run
// simulation that caught the PR #31 no-overlap regression.

import test from "node:test";
import assert from "node:assert/strict";
import { computeCollage, hash, overlapArea } from "./layout.js";

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

const VIEWPORTS = [
  [1920, 1080], [1280, 800], [375, 812], [812, 375], [2560, 1440],
  [1345, 1245], [716, 801], [100, 100],
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

test("on a wide screen the cluster stays a compact central clump", () => {
  // The bug this fixes: spread scaled with viewport WIDTH, so a wide screen
  // fanned birds edge-to-edge. The cluster should be bounded by the smaller
  // axis, so on 2400x1000 it occupies the middle, not the full width.
  const [W, H] = [2400, 1000];
  const bandTop = 150;
  const vmin = Math.min(W, H) / 100;
  const files = randomFiles(makeRng(5), 12);
  const placed = computeCollage(files, W, H, bandTop);
  let maxX = 0, maxHalfW = 0;
  for (const p of placed) {
    maxX = Math.max(maxX, Math.abs(p.x));
    maxHalfW = Math.max(maxHalfW, (p.sizeVmin * vmin) / 2);
  }
  // Every plate's far edge sits well inside the half-width — a central clump,
  // not a full-bleed fan (the bandH-driven span caps it near ~H, ≪ W/2).
  assert.ok(
    maxX + maxHalfW <= W * 0.4,
    `cluster too wide: reached ${(maxX + maxHalfW).toFixed(0)}px of ${W / 2}px half-width`,
  );
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

test("newest bird (index 0) sits at the band centre, on top", () => {
  const files = randomFiles(makeRng(3), 6);
  const [W, H] = [1280, 800];
  const bandTop = 140;
  const placed = computeCollage(files, W, H, bandTop);
  const newest = placed[0];
  assert.equal(newest.file, files[0]);
  assert.equal(newest.x, 0);
  assert.equal(newest.y, bandTop / 2); // band centre offset
  assert.ok(placed.every(p => p === newest || p.z < newest.z));
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
