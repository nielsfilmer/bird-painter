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
// then multiplied by the global fit scale. Sized so ~3 plates stack in the
// sub-title band on a 4:3/16:9 screen — the "full height first" rule needs
// vertical stacking to be possible at full size; the scale drops below 1 only
// once the whole screen is full. A tight span keeps a minimum size — the
// smallest bucket stays 80% of the largest (16/20) — so no bird renders far
// smaller than its neighbours.
const SIZE_MIN_VMIN = 16, SIZE_SPAN_VMIN = 5;    // plate width 16–20 vmin
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
const GAP_VMIN = 0.2;        // tight spacing between plates
const SPIRAL_STEP = 0.22;    // how far along the spiral each retry walks
const MAX_TRIES = 220;       // spiral samples per plate before giving up
const GROW_FACTOR = 1.12;    // widen-to-fit: widen the oval by this per step…
const GROW_STEPS = 24;       // …up to this many, until the set fits (or caps out)
const FILL_FACTOR = 0.92;    // when width-capped: plates claim at most this share
const SHRINK_RETRIES = 8;    // if the set still can't fit at the max oval,
const SHRINK_STEP = 0.9;     // shrink every plate by this and lay out again
// The cluster is a central oval the spiral fills: newest bird at the centre,
// older ones spiralling outward. Its HEIGHT is fixed — the group always uses
// the full sub-title band — and its WIDTH tracks the content: a few birds form
// a tall, horizontally-compact group in the middle; as more arrive the oval
// widens (widen-to-fit) until it hits the viewport cap, and only then do the
// plates shrink together. So: full height first, expand horizontally, smaller
// only when the screen is full.
const CLUSTER_W_FRAC = 0.92; // oval may widen to at most this fraction of width
const CLUSTER_H_FRAC = 0.88; // oval height: this fraction of the sub-title band
// The first few birds sit in a single horizontal ROW (a wall starting its day
// reads as a neat shelf); once a fourth arrives the oval opens to the full
// band height and the usual full-height-first rule takes over.
const ROW_LIMIT = 3;         // up to this many birds: one horizontal row

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

// One layout pass at a given scale for a batch of entries ({file, index} —
// index is the wall-wide position, used for z; the spiral walks by the LOCAL
// position within the batch). Plates walk a phyllotaxis spiral bounded to a
// central oval (halfW x halfH), clamped to stay on screen (boundW/boundH are
// the viewport half-extents), avoiding everything already in `placed`
// (appended to). Returns how many plates had to settle for an overlap.
function computeLayout(entries, scale, vmin, halfW, halfH, boundW, boundH, placed) {
  let fallbacks = 0;
  entries.forEach(({ file, index }, local) => {
    const h = hash(file);
    const sizeVmin = (SIZE_MIN_VMIN + (h % SIZE_SPAN_VMIN)) * scale;
    const sizePx = sizeVmin * vmin;
    const imageH = sizePx * PLATE_ASPECT;
    const boxW = sizePx + GAP_VMIN * vmin;
    const boxH = imageH + captionPx(imageH) + GAP_VMIN * vmin;
    const jitterA = (((h >>> 8) % 100) / 100 - 0.5) * 0.5; // ±0.25 rad
    // Clamp plate centres to the oval extents (the spiral's reach can exceed
    // 1, and an unbounded x lets birds leak sideways into a row) AND on screen.
    const clampX = Math.min(halfW, Math.max(0, boundW - sizePx / 2));
    const clampY = Math.min(halfH, Math.max(0, boundH - (imageH + captionPx(imageH)) / 2));
    let best = null, bestOverlap = Infinity;
    for (let t = local, tries = 0; tries < MAX_TRIES; tries++, t += SPIRAL_STEP) {
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
  return fallbacks;
}

export function computeCollage(files, W, H, bandTop) {
  // Before the page has a size (a layout race), don't emit zero-size plates —
  // return nothing and let the next poll/resize lay out for real.
  if (W <= 0 || H <= 0) return [];
  const vmin = Math.min(W, H) / 100;
  const bandH = H - bandTop;
  const yOffset = bandTop / 2; // shift the cluster down into the band
  const naturalArea = files.reduce((sum, file) => {
    const s = (SIZE_MIN_VMIN + (hash(file) % SIZE_SPAN_VMIN)) * vmin;
    const imageH = s * PLATE_ASPECT;
    return sum + (s + GAP_VMIN * vmin) * (imageH + captionPx(imageH) + GAP_VMIN * vmin);
  }, 0);
  const maxHalfW = (CLUSTER_W_FRAC * W) / 2;
  const boundW = W / 2, boundH = bandH / 2;
  const maxBoxW = files.reduce((m, file) => {
    const s = (SIZE_MIN_VMIN + (hash(file) % SIZE_SPAN_VMIN)) * vmin;
    return Math.max(m, s + GAP_VMIN * vmin);
  }, 1);
  const maxBoxH = files.reduce((m, file) => {
    const s = (SIZE_MIN_VMIN + (hash(file) % SIZE_SPAN_VMIN)) * vmin;
    const imageH = s * PLATE_ASPECT;
    return Math.max(m, imageH + captionPx(imageH) + GAP_VMIN * vmin);
  }, 1);
  // The rule: the up-to-ROW_LIMIT OLDEST birds keep a single horizontal row
  // across the band centre for good; every newer bird stacks vertically
  // around that shelf. `files` is newest-first, so the row is the tail.
  const entries = files.map((file, index) => ({ file, index }));
  const rowCount = Math.min(ROW_LIMIT, entries.length);
  const rowEntries = entries.slice(entries.length - rowCount);
  const tallEntries = entries.slice(0, entries.length - rowCount);
  const fullHalfH = (CLUSTER_H_FRAC * bandH) / 2;

  // Two phases per pass: the row lays out in a ~zero-height oval (centres
  // pinned to the row axis), then the newer birds spiral in the full-height
  // oval with the row plates as obstacles — so they land above/below the
  // shelf and expand vertically, per the current rules.
  function layoutPass(scale, halfW) {
    const placed = [];
    let fallbacks = computeLayout(rowEntries, scale, vmin, halfW, 1, boundW, boundH, placed);
    fallbacks += computeLayout(tallEntries, scale, vmin, halfW, fullHalfH, boundW, boundH, placed);
    return { placed, fallbacks };
  }

  // Start one plate wide and widen until everything fits (or the cap).
  const halfW0 = Math.min(maxHalfW, maxBoxW / 2);
  let scale = 1, halfW = halfW0, result;
  for (let step = 0, k = 1; step < GROW_STEPS; step++, k *= GROW_FACTOR) {
    halfW = Math.min(maxHalfW, halfW0 * k);
    result = layoutPass(scale, halfW);
    if (result.fallbacks === 0 || halfW >= maxHalfW) break;
  }
  // Width capped and still overlapping → the screen is full: now (and only
  // now) shrink the plates together until the set fits. The seed uses the
  // content's REAL occupied height (a bare row is one plate tall, not the
  // ~zero-height placement oval — a near-zero area would collapse the scale).
  if (result.fallbacks > 0) {
    const seedHalfH = tallEntries.length > 0 ? fullHalfH : maxBoxH / 2;
    const clusterArea = Math.PI * halfW * seedHalfH;
    scale = Math.min(1, Math.sqrt((FILL_FACTOR * clusterArea) / (naturalArea || 1)));
    result = layoutPass(scale, halfW);
    for (let i = 0; i < SHRINK_RETRIES && result.fallbacks > 0; i++) {
      scale *= SHRINK_STEP;
      result = layoutPass(scale, halfW);
    }
  }
  // Preserve the input (newest-first) order in the result — consumers index
  // by position as well as by file.
  result.placed.sort((a, b) => a.index - b.index);
  return result.placed.map(({ box, file, sizeVmin, index }) => ({
    file,
    x: box.x,
    y: box.y + yOffset,
    sizeVmin,
    z: TOP_Z - index,
  }));
}
