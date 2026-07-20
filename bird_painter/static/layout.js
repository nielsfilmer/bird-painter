// Pure collage-layout math for the wall — no DOM, so it can be unit-tested
// with `node --test` (bird_painter/static/layout.test.js). index.html imports
// computeCollage() and applies the result to the plate elements.
//
// Given the live files (newest-first), the viewport, and the y where the title
// band ends, computeCollage returns one placement per file:
//   { file, x, y, sizeVmin, z }
// x/y are pixels relative to the viewport centre (the plates use
// translate(-50%,-50%) translate(x,y)). Birds never overlap: each takes the
// first free spot walking a phyllotaxis spiral from the band centre; if the
// set can't fit at the current size, all plates shrink together until it does.

const GOLDEN_ANGLE = 2.399963229728653; // radians, 137.5°
// Per-plate width, hashed from the filename: SIZE_MIN + (hash % SIZE_SPAN),
// then multiplied by the global fit scale. This is the "big but not huge"
// starting size: a handful of birds render at (roughly) this size and the
// scale stays 1; as the wall fills past what the cluster can hold at full
// size, the fill scale drops below 1 and every plate shrinks together (rule:
// big to start, smaller when crowded). A tight span keeps a minimum size —
// the smallest bucket stays ~82% of the largest — so no bird renders far
// smaller than its neighbours.
const SIZE_MIN_VMIN = 22, SIZE_SPAN_VMIN = 5;    // plate width 22–26 vmin
const MAX_INDEX = 12;                            // matches the wall's live cap
const PLATE_ASPECT = 5 / 4;                      // painted image is 4:5 portrait
// The plate's box also reserves room for the caption below the image (species
// + "heard …"), so a bird never sits on the label of the one below it. The
// reserve is the LARGER of a fraction of the image height and a fixed pixel
// floor — the caption font is clamped, so on small plates/viewports it stops
// scaling down and a pure ratio would under-reserve (labels then spill onto
// the bird below).
const CAPTION_ALLOWANCE = 1.1;
const CAPTION_FLOOR_PX = 26;

function captionPx(imageHeightPx) {
  return Math.max(CAPTION_FLOOR_PX, imageHeightPx * (CAPTION_ALLOWANCE - 1));
}
const TOP_Z = 200;
const GAP_VMIN = 0.5;        // tight spacing — a dense cutout cluster
const SPIRAL_STEP = 0.22;    // how far along the spiral each retry walks
const MAX_TRIES = 220;       // spiral samples per plate before giving up
const FILL_FACTOR = 0.92;    // plates may claim at most this share of the cluster
const SHRINK_RETRIES = 8;    // if any plate still couldn't find a free spot,
const SHRINK_STEP = 0.9;     // shrink everyone by this and lay out again
// The cluster is a central oval the spiral grows into: newest bird at the
// centre, older ones spiralling outward. It fills most of BOTH viewport axes
// so the collage uses the whole screen (target: a 16:9 display) instead of
// huddling in the middle — but the width is also capped to a multiple of the
// height (CLUSTER_ASPECT) so an ultrawide screen doesn't fan the birds
// edge-to-edge into one thin band.
const CLUSTER_W_FRAC = 0.92; // cluster spans this fraction of the width…
const CLUSTER_H_FRAC = 0.9;  // …and this fraction of the sub-title band height
const CLUSTER_ASPECT = 1.7;  // …but never wider than this × its half-height

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
    const imageH = sizePx * PLATE_ASPECT;
    const boxW = sizePx + GAP_VMIN * vmin;
    const boxH = imageH + captionPx(imageH) + GAP_VMIN * vmin;
    const jitterA = (((h >>> 8) % 100) / 100 - 0.5) * 0.5; // ±0.25 rad
    const clampX = Math.max(0, boundW - sizePx / 2);
    const clampY = Math.max(0, boundH - (imageH + captionPx(imageH)) / 2);
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
    placed.push({ box: best, file, sizeVmin, index });
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
  // Cluster extents: a central oval filling most of BOTH axes, so the collage
  // uses the whole screen. The width is capped to CLUSTER_ASPECT × the
  // half-height so an ultrawide display doesn't fan the birds into a thin band.
  const halfH = (CLUSTER_H_FRAC * bandH) / 2;
  const halfW = Math.min((CLUSTER_W_FRAC * W) / 2, halfH * CLUSTER_ASPECT);
  const naturalArea = files.reduce((sum, file) => {
    const s = (SIZE_MIN_VMIN + (hash(file) % SIZE_SPAN_VMIN)) * vmin;
    const imageH = s * PLATE_ASPECT;
    return sum + (s + GAP_VMIN * vmin) * (imageH + captionPx(imageH) + GAP_VMIN * vmin);
  }, 0);
  // Plates render at their natural "big but not huge" size (scale 1) until the
  // set can't fit the cluster oval at full size; then the scale drops below 1
  // and everyone shrinks together — so the wall gets smaller as it fills, never
  // bigger than the starting size on a near-empty wall.
  const clusterArea = Math.PI * halfW * halfH;
  let scale = Math.min(1, Math.sqrt((FILL_FACTOR * clusterArea) / (naturalArea || 1)));
  const boundW = W / 2, boundH = bandH / 2;
  let result = computeLayout(files, scale, vmin, halfW, halfH, boundW, boundH);
  for (let i = 0; i < SHRINK_RETRIES && result.fallbacks > 0; i++) {
    scale *= SHRINK_STEP;
    result = computeLayout(files, scale, vmin, halfW, halfH, boundW, boundH);
  }
  return result.placed.map(({ box, file, sizeVmin, index }) => ({
    file,
    x: box.x,
    y: box.y + yOffset,
    sizeVmin,
    z: TOP_Z - index,
  }));
}
