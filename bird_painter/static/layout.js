// Pure collage-layout math for the wall — no DOM, so it can be unit-tested
// with `node --test` (bird_painter/static/layout.test.js). index.html imports
// computeCollage() and applies the result to the plate elements.
//
// Given the live files (newest-first), the viewport, and the y where the title
// band ends, computeCollage returns one placement per file:
//   { file, x, y, sizeVmin, tilt, z }
// x/y are pixels relative to the viewport centre (the plates use
// translate(-50%,-50%) translate(x,y)). Birds never overlap: each takes the
// first free spot walking a phyllotaxis spiral from the band centre; if the
// set can't fit at the current size, all plates shrink together until it does.

const GOLDEN_ANGLE = 2.399963229728653; // radians, 137.5°
const SIZE_MIN_VMIN = 22, SIZE_SPAN_VMIN = 13;   // plate width 22–34 vmin
const MAX_INDEX = 12;                            // matches the wall's live cap
const PLATE_ASPECT = 5 / 4;                      // painted image is 4:5 portrait
const TOP_Z = 200;
const GAP_VMIN = 0.8;        // tight spacing — a dense cutout cluster
const SPIRAL_STEP = 0.22;    // how far along the spiral each retry walks
const MAX_TRIES = 220;       // spiral samples per plate before giving up
const FILL_FACTOR = 0.78;    // plates may claim at most this share of the cluster
const SHRINK_RETRIES = 8;    // if any plate still couldn't find a free spot,
const SHRINK_STEP = 0.9;     // shrink everyone by this and lay out again
// The cluster is a central oval whose size is driven by the SMALLER viewport
// axis, so it stays a compact clump in the middle instead of fanning out to
// the edges on a wide screen. CLUSTER_ASPECT lets it be a little wider than
// tall (like the reference wall-chart).
const CLUSTER_SPAN = 0.86;   // cluster fills this fraction of the limiting axis
const CLUSTER_ASPECT = 1.35; // cluster oval is this much wider than tall

export function hash(str) {
  let h = 2166136261;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

export function overlapArea(a, b) {
  const w = Math.min(a.x + a.w / 2, b.x + b.w / 2)
          - Math.max(a.x - a.w / 2, b.x - b.w / 2);
  const h = Math.min(a.y + a.h / 2, b.y + b.h / 2)
          - Math.max(a.y - a.h / 2, b.y - b.h / 2);
  return Math.max(0, w) * Math.max(0, h);
}

// One layout pass at a given scale. Places plates on a phyllotaxis spiral
// bounded to a central oval (halfW x halfH), clamped to stay on screen
// (boundW/boundH are the viewport half-extents). Returns the chosen boxes
// plus how many plates had to settle for an overlapping spot (fallbacks).
function computeLayout(files, scale, vmin, halfW, halfH, boundW, boundH) {
  const placed = [];
  let fallbacks = 0;
  files.forEach((file, index) => {
    const h = hash(file);
    const sizeVmin = (SIZE_MIN_VMIN + (h % SIZE_SPAN_VMIN)) * scale;
    const sizePx = sizeVmin * vmin;
    const boxW = sizePx + GAP_VMIN * vmin;
    const boxH = sizePx * PLATE_ASPECT + GAP_VMIN * vmin;
    const jitterA = (((h >>> 8) % 100) / 100 - 0.5) * 0.5; // ±0.25 rad
    const tilt = ((h >>> 16) % 15) - 7;                    // -7..+7 deg
    const clampX = Math.max(0, boundW - sizePx / 2);
    const clampY = Math.max(0, boundH - sizePx * PLATE_ASPECT / 2);
    let best = null, bestOverlap = Infinity;
    for (let t = index, tries = 0; tries < MAX_TRIES; tries++, t += SPIRAL_STEP) {
      const angle = t * GOLDEN_ANGLE + jitterA;
      const reach = Math.sqrt(t) / Math.sqrt(MAX_INDEX);
      let x = Math.cos(angle) * reach * halfW;
      let y = Math.sin(angle) * reach * halfH;
      x = Math.max(-clampX, Math.min(clampX, x));
      y = Math.max(-clampY, Math.min(clampY, y));
      const box = { x, y, w: boxW, h: boxH };
      const overlap = placed.reduce((s, o) => s + overlapArea(box, o.box), 0);
      if (overlap === 0) { best = box; bestOverlap = 0; break; }
      if (overlap < bestOverlap) { best = box; bestOverlap = overlap; }
    }
    if (bestOverlap > 0) fallbacks++;
    placed.push({ box: best, file, sizeVmin, tilt, index });
  });
  return { placed, fallbacks };
}

export function computeCollage(files, W, H, bandTop) {
  // Before the page has a size (a layout race), don't emit zero-size plates —
  // return nothing and let the next poll/resize lay out for real.
  if (W <= 0 || H <= 0) return [];
  const vmin = Math.min(W, H) / 100;
  const bandH = H - bandTop;
  const yOffset = bandTop / 2; // shift the cluster down into the band
  // Cluster extents: a central oval sized by the smaller axis, so a wide
  // screen gets a compact clump (not an edge-to-edge fan), capped at the
  // viewport so it always fits.
  const span = Math.min(W, bandH) * CLUSTER_SPAN;
  const halfW = Math.min(W, span * CLUSTER_ASPECT) / 2;
  const halfH = Math.min(bandH, span) / 2;
  const naturalArea = files.reduce((sum, file) => {
    const s = (SIZE_MIN_VMIN + (hash(file) % SIZE_SPAN_VMIN)) * vmin;
    return sum + (s + GAP_VMIN * vmin) * (s * PLATE_ASPECT + GAP_VMIN * vmin);
  }, 0);
  // Size plates to fill the cluster oval (not the whole viewport), so they
  // pack densely in the middle rather than shrinking to dots on a big screen.
  const clusterArea = Math.PI * halfW * halfH;
  let scale = Math.min(1, Math.sqrt((FILL_FACTOR * clusterArea) / (naturalArea || 1)));
  const boundW = W / 2, boundH = bandH / 2;
  let result = computeLayout(files, scale, vmin, halfW, halfH, boundW, boundH);
  for (let i = 0; i < SHRINK_RETRIES && result.fallbacks > 0; i++) {
    scale *= SHRINK_STEP;
    result = computeLayout(files, scale, vmin, halfW, halfH, boundW, boundH);
  }
  return result.placed.map(({ box, file, sizeVmin, tilt, index }) => ({
    file,
    x: box.x,
    y: box.y + yOffset,
    sizeVmin,
    tilt,
    z: TOP_Z - index,
  }));
}
