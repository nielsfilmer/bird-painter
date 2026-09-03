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

// This is an ESTIMATE, and it is linear in captionScale. The drawn caption is
// NOT: enlarge the type and a long species name wraps onto more lines inside a
// plate whose width didn't change, so the drawing outgrows the reserve. That
// is why CAPTION_SCALE_MAX is 2 rather than something more generous — see
// there. Fixing it properly needs the caption's real wrapped size, which needs
// text metrics this DOM-free module doesn't have; #133 and #136 own that, and
// both block shipping a tuned URL to a unit (#120).
//
// Measuring it from the page was tried and reverted in review: `.plate` has a
// 1.6s width transition, so a rect read after writing the width returns the
// PREVIOUS layout's width, and feeding that back shrank plates, which wrapped
// captions harder, which shrank plates — a 41px bird under a 669px caption.
function captionPx(imageHeightPx, captionScale = 1) {
  return Math.max(
    CAPTION_FLOOR_PX * captionScale,
    imageHeightPx * (CAPTION_ALLOWANCE - 1) * captionScale,
  );
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

// --- Panel tuning ----------------------------------------------------------
//
// Per-installation knobs, passed through computeCollage's `opts`. Both default
// to exactly today's behaviour, so the desktop wall and the e-paper
// `/wall.png` render are unchanged unless a caller opts in.
//
// The table model is why these exist. On a tall portrait panel (720x1280 on
// the 7", 1200x1920 on the 10") widen-to-fit stops as soon as the set fits,
// and a portrait viewport has so much vertical room that it fits early — so
// the collage settles into a central column using ~58% of the width and the
// birds stay smaller than the panel allows. `spread` sets a FLOOR on the
// cluster's width. Two consequences worth knowing before turning it:
//   - Below about 0.58 it does nothing on either panel, because widen-to-fit
//     already reaches that far unaided. The useful band is ~0.7 to the cap.
//   - It is inert entirely at ROW_LIMIT birds or fewer: those are a packed,
//     deliberately immovable shelf that ignores the cluster width.
//
// `captionScale` multiplies the lettering. It lives here and not only in CSS
// because the layout reserves room for the caption under each plate — grow the
// type without growing the reserve and labels land on the bird below.
//
// Declared AFTER CLUSTER_W_FRAC on purpose: normalizePanelOpts closes over it,
// and while a module-scope call would today still resolve (layout.js finishes
// evaluating before any importer's body runs), sitting above its own
// dependency is one refactor away from a temporal-dead-zone ReferenceError.
const DEFAULT_SPREAD = 0;        // 0 = no floor; widen-to-fit decides alone
const DEFAULT_CAPTION_SCALE = 1; // 1 = today's clamp() sizes
// The archive chrome's scale (?ui=). Not a layout input at all — the layout
// never sees the overlay — but sanitised here so every knob a kiosk URL can
// carry goes through the one chokepoint, with the caption knob's bounds.
const DEFAULT_UI_SCALE = 1;
// Capped at 2, not higher: above roughly 2x the linear reserve above stops
// covering the wrapped caption and labels land on birds. QA measured the wall
// clean at 1, 1.7 and 2 on both panels, and broken from 2.5 up.
//
// KNOWN LIMIT, and 2 does not fix it everywhere. The driver is SMALL VMIN,
// not a short viewport — an earlier version of this note said "short" and QA
// disproved it: 375x812 is TALLER in aspect than the 7" panel and still
// breaks from about caption 1.3 (at 2: eighteen labels on ink, one plate
// 829px down a 812px page). Plates are sized in vmin, so a small vmin gives a
// small plate; the type is clamped in px and does not shrink with it; a long
// name therefore wraps onto more lines in a narrower plate. The panels are
// safe because their vmin is 7.2 and 12; a 375-wide window's is 3.75.
//
// Bounding the scale by the band was tried and does not work either, because
// the band check calls this same blind estimate — it computes 52px where the
// wrapped caption draws 106px. Nothing here can see wrapping; that is #136,
// which blocks #120.
//
// So: this knob is for the tall portrait panels it was built for, and it is
// not protected against being set on a small-vmin viewport.
const CAPTION_SCALE_MIN = 0.5, CAPTION_SCALE_MAX = 2;

// The ONE place these values are sanitised. index.html calls it too, so the
// CSS `--caption-scale` it sets and the caption reserve the layout computes
// are always the same number — a page that clamped differently from the layout
// would reserve room for type it isn't drawing, and labels would collide.
//
// Values arrive from a URL query string, so treat them as untrusted. Anything
// non-numeric falls back to the default; anything numeric but out of range
// CLAMPS (99 becomes the cap, not the default) — a caller who asked for more
// than we allow wants as much as we allow, not the baseline. Absent params are
// handled explicitly rather than leaning on `Number(null) === 0`, which
// happens to land on DEFAULT_SPREAD by coincidence and would stop doing so the
// day that default changes.
export function normalizePanelOpts(opts = {}) {
  const clamp = (raw, lo, hi, fallback) => {
    if (raw === undefined || raw === null || raw === "") return fallback;
    const n = Number(raw);
    return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : fallback;
  };
  return {
    spread: clamp(opts.spread, 0, CLUSTER_W_FRAC, DEFAULT_SPREAD),
    captionScale: clamp(
      opts.captionScale, CAPTION_SCALE_MIN, CAPTION_SCALE_MAX,
      DEFAULT_CAPTION_SCALE,
    ),
    uiScale: clamp(
      opts.uiScale, CAPTION_SCALE_MIN, CAPTION_SCALE_MAX, DEFAULT_UI_SCALE,
    ),
  };
}

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
function computeLayout(entries, scale, vmin, halfW, halfH, boundW, boundH, placed, clearHalfH = 0, capFor = (file, imageH) => captionPx(imageH)) {
  let fallbacks = 0;
  entries.forEach(({ file, index }, local) => {
    const h = hash(file);
    const sizeVmin = (SIZE_MIN_VMIN + (h % SIZE_SPAN_VMIN)) * scale;
    const sizePx = sizeVmin * vmin;
    const imageH = sizePx * PLATE_ASPECT;
    const boxW = sizePx + GAP_VMIN * vmin;
    const boxH = imageH + capFor(file, imageH) + GAP_VMIN * vmin;
    const jitterA = (((h >>> 8) % 100) / 100 - 0.5) * 0.5; // ±0.25 rad
    // Clamp plate centres to the oval extents (the spiral's reach can exceed
    // 1, and an unbounded x lets birds leak sideways into a row) AND on screen.
    const clampX = Math.min(halfW, Math.max(0, boundW - sizePx / 2));
    const clampY = Math.min(halfH, Math.max(0, boundH - (imageH + capFor(file, imageH)) / 2));
    let best = null, bestOverlap = Infinity;
    for (let t = local, tries = 0; tries < MAX_TRIES; tries++, t += SPIRAL_STEP) {
      const angle = t * GOLDEN_ANGLE + jitterA;
      const reach = Math.sqrt(t) / Math.sqrt(MAX_INDEX);
      let x = Math.cos(angle) * reach * halfW;
      let y = Math.sin(angle) * reach * halfH;
      x = Math.max(-clampX, Math.min(clampX, x));
      y = Math.max(-clampY, Math.min(clampY, y));
      if (clearHalfH > 0) {
        // Owner rule: newer birds go ABOVE or BELOW the shelf, never level
        // with it — push the centre out of the shelf band (its half-height
        // plus this plate's), screen bounds permitting.
        const minY = Math.min(clearHalfH + boxH / 2, clampY);
        if (Math.abs(y) < minY) y = (y < 0 || (y === 0 && Math.sin(angle) < 0)) ? -minY : minY;
      }
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

export function computeCollage(files, W, H, bandTop, opts = {}) {
  // Before the page has a size (a layout race), don't emit zero-size plates —
  // return nothing and let the next poll/resize lay out for real.
  if (W <= 0 || H <= 0) return [];
  const { spread, captionScale } = normalizePanelOpts(opts);
  const capFor = (file, imageH) => captionPx(imageH, captionScale);
  const vmin = Math.min(W, H) / 100;
  const bandH = H - bandTop;
  const yOffset = bandTop / 2; // shift the cluster down into the band
  const naturalArea = files.reduce((sum, file) => {
    const s = (SIZE_MIN_VMIN + (hash(file) % SIZE_SPAN_VMIN)) * vmin;
    const imageH = s * PLATE_ASPECT;
    return sum + (s + GAP_VMIN * vmin) * (imageH + capFor(file, imageH) + GAP_VMIN * vmin);
  }, 0);
  const maxHalfW = (CLUSTER_W_FRAC * W) / 2;
  // The width floor. At spread 0 this is 0 and the widen-to-fit loop below is
  // untouched, which is what keeps the default layout bit-identical.
  const minHalfW = Math.min(maxHalfW, (spread * W) / 2);
  const boundW = W / 2, boundH = bandH / 2;
  const maxBoxW = files.reduce((m, file) => {
    const s = (SIZE_MIN_VMIN + (hash(file) % SIZE_SPAN_VMIN)) * vmin;
    return Math.max(m, s + GAP_VMIN * vmin);
  }, 1);
  const maxBoxH = files.reduce((m, file) => {
    const s = (SIZE_MIN_VMIN + (hash(file) % SIZE_SPAN_VMIN)) * vmin;
    const imageH = s * PLATE_ASPECT;
    return Math.max(m, imageH + capFor(file, imageH) + GAP_VMIN * vmin);
  }, 1);
  // The rule: the up-to-ROW_LIMIT OLDEST birds keep a single horizontal row
  // across the band centre for good; every newer bird stacks vertically
  // around that shelf. `files` is newest-first, so the row is the tail.
  const entries = files.map((file, index) => ({ file, index }));
  const rowCount = Math.min(ROW_LIMIT, entries.length);
  const rowEntries = entries.slice(entries.length - rowCount);
  const tallEntries = entries.slice(0, entries.length - rowCount);
  const fullHalfH = (CLUSTER_H_FRAC * bandH) / 2;

  // The shelf is PACKED, not spiralled: oldest→newest runs left→right,
  // centred as a block — so its members never swap sides as the wall grows
  // (the oval's width doesn't touch it). A shelf wider than the screen counts
  // as fallbacks so the shared shrink loop scales everyone down.
  function placeRow(entries, scale, placed) {
    const boxes = entries.map(({ file, index }) => {
      const sizePx = (SIZE_MIN_VMIN + (hash(file) % SIZE_SPAN_VMIN)) * scale * vmin;
      const imageH = sizePx * PLATE_ASPECT;
      return {
        file, index, sizePx,
        boxW: sizePx + GAP_VMIN * vmin,
        boxH: imageH + capFor(file, imageH) + GAP_VMIN * vmin,
      };
    });
    // entries is a slice of the newest-first list; oldest-last. Reverse so the
    // shelf reads oldest→newest, left→right.
    const ordered = boxes.slice().reverse();
    const totalW = ordered.reduce((sum, b) => sum + b.boxW, 0);
    let fallbacks = 0;
    let cursor = -totalW / 2;
    for (const b of ordered) {
      const x = cursor + b.boxW / 2;
      cursor += b.boxW;
      if (Math.abs(x) + b.sizePx / 2 > boundW) fallbacks++;
      placed.push({
        box: { x, y: 0, w: b.boxW, h: b.boxH },
        file: b.file, sizeVmin: b.sizePx / vmin, index: b.index,
      });
    }
    return fallbacks;
  }

  // Two phases per pass: the shelf packs onto the row axis, then the newer
  // birds spiral the full-height oval with the shelf as obstacles AND a
  // vertical clearance (they must sit fully above or below it — never level).
  function layoutPass(scale, halfW) {
    const placed = [];
    let fallbacks = placeRow(rowEntries, scale, placed);
    const rowClearHalf = placed.reduce((m, p) => Math.max(m, p.box.h / 2), 0);
    fallbacks += computeLayout(tallEntries, scale, vmin, halfW, fullHalfH, boundW, boundH, placed, rowClearHalf, capFor);
    return { placed, fallbacks };
  }

  // Start one plate wide (or the spread floor, whichever is wider) and widen
  // until everything fits (or the cap).
  const halfW0 = Math.max(minHalfW, Math.min(maxHalfW, maxBoxW / 2));
  let scale = 1, halfW = halfW0, result;
  for (let step = 0, k = 1; step < GROW_STEPS; step++, k *= GROW_FACTOR) {
    halfW = Math.min(maxHalfW, Math.max(minHalfW, halfW0 * k));
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

// --- Drag-to-scroll for the archive overlay (panel mode) --------------------
//
// A pure state machine, no DOM: index.html feeds it pointer events and
// applies what it returns. Extracted from the page for the same reason
// computeCollage was — so `node --test` can walk it. Three of the five
// commits on PR #146 were same-evening fixes to this logic found by a finger
// on the real panel (pointer capture stole taps from the close button; the
// swallow flag ate the tap after a swipe), which is exactly the kind of bug a
// table of cases catches and a reader doesn't.
//
// Why the page scrolls its own overlay at all: on the first unit a genuine
// touchscreen (Goodix, libinput "touch") tapped fine but Chromium never turned
// a drag into a scroll — and a hold selected text, i.e. it saw a mouse.
// Pointer Events arrive either way, so the overlay is scrolled from them,
// with `touch-action: none` on it so the browser's own pan can't compete.
//
// Contract, per gesture:
//   down()  — accepts the gesture (false for a non-primary mouse button);
//             a new gesture always clears the swallow flag.
//   move()  — null until movement passes the threshold; then the scrollTop to
//             apply, with `capture: true` on the first such move so the page
//             takes pointer capture ONLY once this is a drag (capturing on
//             down retargets the release, and the click, away from whatever
//             was tapped).
//   up()    — ends the gesture; if it moved, the click the release produces
//             is the drag's own and must be swallowed.
//   cancel()— ends the gesture; no click will follow, so nothing to swallow.
//   click() — true exactly once for the drag's own click; a tap never sets it.
export const DRAG_THRESHOLD_PX = 6;

export function createDragScroller(threshold = DRAG_THRESHOLD_PX) {
  let drag = null;     // { id, y, top, moved }
  let swallow = false; // the click the last drag's own release will produce
  return {
    down({ id, y, scrollTop, pointerType = "touch", button = 0 }) {
      if (pointerType === "mouse" && button !== 0) return false;
      swallow = false;
      drag = { id, y, top: scrollTop, moved: false };
      return true;
    },
    move({ id, y, buttons = 1 }) {
      if (!drag || id !== drag.id) return null;
      // A mouse released outside the window sends no pointerup; it comes
      // back with no buttons held. Without this, a bare hover would capture
      // and scroll-follow the cursor (review of #146, N1).
      if (buttons === 0) { drag = null; return null; }
      const dy = y - drag.y;
      if (!drag.moved && Math.abs(dy) < threshold) return null;
      const capture = !drag.moved;
      drag.moved = true;
      return { scrollTop: drag.top - dy, capture };
    },
    up({ id }) {
      if (!drag || id !== drag.id) return;
      swallow = drag.moved;
      drag = null;
    },
    cancel({ id }) {
      if (drag && id === drag.id) drag = null;
    },
    click() {
      const s = swallow;
      swallow = false;
      return s;
    },
    get dragging() { return drag !== null; },
  };
}
