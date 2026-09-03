// The table model's settings screen — the unit's own knobs, night mode and
// WiFi, drawn on the wall's paper (#123). Loaded by the wall page in panel
// mode only, and mounted only when the loopback-only `/unit` answers — so
// it exists on the unit's own touchscreen and nowhere else. Pure DOM +
// fetch, no framework; every number the screen shows is the server's, and
// every bound a stepper enforces comes from `/unit`'s `bounds`, so page and
// server cannot disagree.
//
// Opening it: a long press (1.5 s) in the bottom-left corner of the wall —
// a place a hand doesn't land by accident, on a screen with no other
// chrome there (the archive button is bottom-right). Closing: the ×, or a
// minute of no touches — except while a join is in flight.
//
// The pure helpers at the bottom are exported for `unit-screen.test.js`;
// nothing above them touches the DOM at import time.

export const OPEN_HOLD_MS = 1500;
export const IDLE_CLOSE_MS = 60_000;
export const CORNER_PX = 120;
export const PUT_DEBOUNCE_MS = 300;
export const CONFIRM_MS = 5000;
export const FIRST_BOOT_OPEN_MS = 20_000;
export const CONNECTIVITY_POLL_MS = 60_000;

const hh = (h) => `${String(h).padStart(2, "0")}:00`;
const pct = (v) => `${Math.round(v * 100)}%`;

// What each knob looks like on screen. Bounds and steps come from the server.
const KNOB_VIEW = {
  CAPTION: { label: "lettering", show: pct },
  UI: { label: "controls", show: pct },
  MAX_LIVE: { label: "birds on the sheet", show: (v) => `${v}` },
  NIGHT_FROM: { label: "from", show: hh, wrap: true },
  NIGHT_TO: { label: "until", show: hh, wrap: true },
  NIGHT_BRIGHTNESS: { label: "night brightness", show: (v) => `${v}%` },
};

const ICON = {
  close: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>',
  minus: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M5 12h14"/></svg>',
  plus: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
  lock: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#8d8065" stroke-width="1.6"><rect x="5" y="10" width="14" height="10" rx="1"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>',
};

// The artboards are 1280×720; sizes below are those numbers in vh / vw / %
// so the 7" and the 10" get the same proportions. Above the night wash
// (z-index 40): the owner opening settings at night to change the night
// schedule should not get the worst-lit view of it — the backlight is
// already at its night level.
const CSS = `
#unit { position: fixed; inset: 0; z-index: 50; display: none; color: #4a3f2e;
  font-family: Georgia, "Times New Roman", serif; background: var(--paper, #ece1c6);
  user-select: none; -webkit-user-select: none; touch-action: manipulation; }
#unit.open { display: block; }
#unit .screen { position: absolute; inset: 0; display: none; }
#unit .screen.on { display: block; }
#unit h2 { position: absolute; left: 0; right: 0; top: 6.1vh; margin: 0; text-align: center;
  font-variant: small-caps; letter-spacing: 0.18em; font-weight: normal; font-size: calc(4.5vmin * var(--ui-scale, 1)); }
#unit .close { position: absolute; top: 14px; right: 18px; width: 52px; height: 52px; display: flex;
  align-items: center; justify-content: center; color: #8d8065; cursor: pointer; }
#unit .grid { position: absolute; left: 7.5%; right: 7.5%; top: 17.2vh; bottom: 3vh; display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 5.5vh 7.5%; overflow-y: auto; overflow-x: hidden; }
@media (orientation: portrait) { #unit .grid { grid-template-columns: minmax(0, 1fr); gap: 4vh 0; } }
#unit .group { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
#unit .group-title { font-variant: small-caps; letter-spacing: 0.14em; font-size: calc(2.64vmin * var(--ui-scale, 1));
  border-bottom: 1px solid rgba(141,128,101,0.45); padding-bottom: 6px; }
#unit .row { display: flex; align-items: center; justify-content: space-between; gap: 24px; min-height: 56px; }
#unit .label { font-variant: small-caps; letter-spacing: 0.08em; font-size: calc(2.08vmin * var(--ui-scale, 1)); color: #8d8065; }
#unit .value { font-size: calc(3.05vmin * var(--ui-scale, 1)); overflow-wrap: anywhere; }
#unit .stepper { display: flex; align-items: center; gap: 14px; flex-shrink: 0; }
#unit .step { display: inline-flex; align-items: center; justify-content: center; width: 48px; height: 48px;
  border: 1px solid #8d8065; cursor: pointer; }
#unit .step.off { opacity: 0.3; }
#unit .stepper .value { min-width: 64px; text-align: center; }
#unit .btn { display: inline-flex; align-items: center; justify-content: center; min-height: 48px; padding: 6px 30px;
  border: 1px solid #8d8065; font-style: italic; font-size: calc(2.3vmin * var(--ui-scale, 1)); background: none;
  color: inherit; font-family: inherit; cursor: pointer; white-space: nowrap; flex-shrink: 0; }
#unit .btn.primary { background: #4a3f2e; color: #f4edda; border-color: #4a3f2e; }
#unit .btn.busy { opacity: 0.5; pointer-events: none; }
#unit .btn.armed { border-color: #4a3f2e; color: #4a3f2e; font-style: normal; }
#unit .switch { position: relative; width: 58px; height: 30px; border: 1px solid #8d8065; border-radius: 15px;
  box-sizing: content-box; padding: 9px 0; background-clip: content-box; margin: 0 -2px; cursor: pointer; flex-shrink: 0; }
#unit .switch .knob { position: absolute; top: 12px; left: 3px; width: 22px; height: 22px; border-radius: 11px; background: #8d8065; }
#unit .switch.on { background: #4a3f2e; border-color: #4a3f2e; }
#unit .switch.on .knob { left: 31px; background: #f4edda; }
#unit .list { position: absolute; left: 15.6%; right: 15.6%; top: 17.8vh; bottom: 16vh; overflow-y: auto; overflow-x: hidden; }
#unit .net { display: flex; align-items: center; justify-content: space-between; gap: 24px; min-height: 56px;
  border-bottom: 1px solid rgba(141,128,101,0.35); padding: 6px 0; }
#unit .net .who { display: flex; align-items: center; gap: 20px; min-width: 0; }
#unit .net .sub { font-style: italic; font-size: calc(2.2vmin * var(--ui-scale, 1)); color: #6b5e45; }
#unit .net .do { display: flex; align-items: center; gap: 18px; flex-shrink: 0; }
#unit .foot { position: absolute; left: 0; right: 0; bottom: 8.3vh; display: flex; justify-content: center; gap: 24px; }
#unit .hint { font-style: italic; letter-spacing: 0.12em; color: #8d8065; text-align: center;
  font-size: calc(2.6vmin * var(--ui-scale, 1)); }
#unit .field { position: absolute; left: 15.6%; right: 15.6%; top: 6.9vh; display: flex; flex-direction: column; gap: 14px; }
#unit .field .ssid { font-size: calc(3.6vmin * var(--ui-scale, 1)); text-align: center; }
#unit .field .pw { display: flex; align-items: center; gap: 16px; border-bottom: 1px solid #8d8065; padding: 8px 4px; }
#unit .field .pw .text { flex-grow: 1; font-size: calc(3.9vmin * var(--ui-scale, 1)); letter-spacing: 0.06em; min-height: 1.3em; overflow-wrap: anywhere; }
#unit .field .pw .text.hidden { letter-spacing: 0.22em; }
#unit .keys { position: absolute; left: 0; right: 0; top: 36.4vh; display: flex; flex-direction: column; align-items: center; gap: 10px; }
#unit .krow { display: flex; gap: 10px; }
#unit .key { display: flex; align-items: center; justify-content: center; width: 7.8vw; height: 7.5vh; min-height: 44px;
  border: 1px solid #8d8065; font-size: calc(3.05vmin * var(--ui-scale, 1)); background: rgba(255,252,240,0.35); cursor: pointer; }
#unit .key:active { background: #4a3f2e; color: #f4edda; }
#unit .key.wide, #unit .key.wider, #unit .key.primary { font-size: calc(2.08vmin * var(--ui-scale, 1)); font-variant: small-caps; letter-spacing: 0.08em; }
#unit .key.wide { width: 10.2vw; }
#unit .key.wider { width: 12.5vw; }
#unit .key.space { width: 40.6vw; }
#unit .key.primary { width: 15.6vw; background: #4a3f2e; color: #f4edda; border-color: #4a3f2e; }
#unit .key.primary.busy { opacity: 0.5; pointer-events: none; }
#unit .status { position: absolute; left: 0; right: 0; bottom: 3vh; }
`;

const KEY_ROWS = [
  ["q", "w", "e", "r", "t", "y", "u", "i", "o", "p"],
  ["a", "s", "d", "f", "g", "h", "j", "k", "l"],
  ["z", "x", "c", "v", "b", "n", "m"],
];
const SYM_ROWS = [
  ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"],
  ["-", "_", "@", "#", "&", "!", "?", ".", ","],
  ["+", "*", "/", ":", ";", "'", "\""],
];

export function mountUnitScreen({ initial = null, onSettings, onConnectivity } = {}) {
  const style = document.createElement("style");
  style.textContent = CSS;
  document.head.appendChild(style);
  const root = document.createElement("div");
  root.id = "unit";
  root.innerHTML = `
    <div class="screen" data-screen="settings">
      <h2>settings</h2><div class="close" data-act="close">${ICON.close}</div>
      <div class="grid"></div>
      <div class="status hint"></div>
    </div>
    <div class="screen" data-screen="network">
      <h2>choose a network</h2><div class="close" data-act="back">${ICON.close}</div>
      <div class="list"></div>
      <div class="foot"><button class="btn" data-act="rescan">look again</button></div>
      <div class="status hint"></div>
    </div>
    <div class="screen" data-screen="password">
      <div class="close" data-act="back">${ICON.close}</div>
      <div class="field">
        <div class="label" style="text-align:center">password for</div>
        <div class="value ssid"></div>
        <div class="pw"><div class="text hidden"></div><button class="btn" data-act="reveal" style="min-height:44px;padding:4px 20px">show</button></div>
      </div>
      <div class="keys"></div>
      <div class="status hint"></div>
    </div>`;
  document.body.appendChild(root);

  const state = {
    open: false, screen: "settings", unit: initial, nets: [], ssid: null, pw: "",
    reveal: false, shift: false, sym: false, idle: null, busy: false,
    pending: new Map(), putTimer: null, armed: null, armTimer: null,
  };
  const $ = (sel, el = root) => el.querySelector(sel);
  const bounds = (key) => state.unit?.bounds?.[key] || { min: 0, max: 0, step: 1 };

  // ---- server ----
  async function load() {
    const res = await fetch("/unit", { cache: "no-store" });
    if (!res.ok) throw new Error(`unit ${res.status}`);
    state.unit = await res.json();
    onConnectivity?.(state.unit.connectivity);
    return state.unit;
  }
  async function put(changes) {
    const res = await fetch("/unit", {
      method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(changes),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `unit ${res.status}`);
    state.unit = data;
    onSettings?.(state.unit);
    onConnectivity?.(state.unit.connectivity);
    return state.unit;
  }
  async function post(path, body) {
    const res = await fetch(path, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body ?? {}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `${path} ${res.status}`);
    return data;
  }
  // Taps on a stepper coalesce: one PUT per key per 300 ms, not one per tap
  // and one file rewrite each (QA on #157). A failed PUT puts the server's
  // numbers back on screen.
  function queuePut(key, value) {
    state.pending.set(key, value);
    clearTimeout(state.putTimer);
    state.putTimer = setTimeout(async () => {
      const changes = Object.fromEntries(state.pending);
      state.pending.clear();
      try {
        await put(changes);
        status("");
      } catch (e) {
        status(`could not save: ${friendly(e.message)}`);
        try { await load(); } catch { /* the screen keeps what it has */ }
      }
      if (state.screen === "settings") renderSettings();
    }, PUT_DEBOUNCE_MS);
  }

  // ---- screens ----
  function show(screen) {
    state.screen = screen;
    root.querySelectorAll(".screen").forEach((s) => s.classList.toggle("on", s.dataset.screen === screen));
    touch();
  }
  function status(text) {
    const el = $(`.screen[data-screen="${state.screen}"] .status`);
    if (el) el.textContent = text || "";
  }

  function stepper(key, value) {
    const b = bounds(key);
    const view = KNOB_VIEW[key];
    const atMin = !view.wrap && value <= b.min;
    const atMax = !view.wrap && value >= b.max;
    return `<div class="stepper" data-key="${key}">
      <div class="step ${atMin ? "off" : ""}" data-act="step" data-dir="-1">${ICON.minus}</div>
      <div class="value">${view.show(value)}</div>
      <div class="step ${atMax ? "off" : ""}" data-act="step" data-dir="1">${ICON.plus}</div></div>`;
  }
  function knobRow(key, value) {
    const view = KNOB_VIEW[key];
    return `<div class="row"><div><div class="label">${view.label}</div><div class="value">${view.show(value)}</div></div>${stepper(key, value)}</div>`;
  }
  function renderSettings() {
    const u = state.unit;
    if (!u) return;
    const s = u.settings;
    const net = u.connectivity || {};
    const online = net.state === "full";
    const armed = state.armed === "restart";
    $(".grid").innerHTML = `
      <div class="group"><div class="group-title">display</div>
        ${knobRow("CAPTION", s.CAPTION)}
        ${knobRow("UI", s.UI)}
        ${knobRow("MAX_LIVE", s.MAX_LIVE)}
        <div class="row"><div><div class="label">orientation</div><div class="value">${s.ROTATE % 180 === 0 ? "portrait" : "landscape"} · ${s.ROTATE}°</div></div><button class="btn" data-act="rotate">rotate</button></div>
      </div>
      <div class="group"><div class="group-title">night</div>
        <div class="row"><div><div class="label">dim at night</div><div class="value">${s.NIGHT_ENABLED ? `${hh(s.NIGHT_FROM)} – ${hh(s.NIGHT_TO)}` : "never"}${u.night?.is_night ? " · dimmed now" : ""}</div></div><div class="switch ${s.NIGHT_ENABLED ? "on" : ""}" data-act="night"><div class="knob"></div></div></div>
        ${knobRow("NIGHT_FROM", s.NIGHT_FROM)}
        ${knobRow("NIGHT_TO", s.NIGHT_TO)}
        ${knobRow("NIGHT_BRIGHTNESS", s.NIGHT_BRIGHTNESS)}
      </div>
      <div class="group"><div class="group-title">network</div>
        <div class="row"><div><div class="label">${online ? "connected to" : net.ssid ? "on, but no internet" : "not connected"}</div><div class="value">${net.ssid ? `${esc(net.ssid)}${net.ip ? ` · ${esc(net.ip)}` : ""}` : "—"}</div></div><button class="btn" data-act="network">change network</button></div>
        <div class="row"><div><div class="label">recorder</div><div class="value">${esc(u.about?.recorder || "this unit")}</div></div></div>
      </div>
      <div class="group"><div class="group-title">about</div>
        <div class="row"><div><div class="label">unit</div><div class="value">${esc(u.about?.unit || "")}</div></div></div>
        <div class="row"><div><div class="label">ears</div><div class="value">${esc(u.about?.ears || "")}</div></div></div>
        <div class="row"><div><div class="label">wall</div><div class="value">${esc(u.about?.wall || "")}</div></div><button class="btn ${armed ? "armed" : ""}" data-act="restart">${armed ? "tap again to restart" : "restart"}</button></div>
      </div>`;
  }
  function renderNetworks() {
    const list = $(".list");
    if (!state.nets.length) {
      list.innerHTML = `<div class="hint" style="padding-top:8vh">no networks in reach yet</div>`;
      return;
    }
    const ip = state.unit?.connectivity?.ip;
    list.innerHTML = state.nets.map((n) => `
      <div class="net" data-ssid="${esc(n.ssid)}">
        <div class="who">${bars(n.signal)}<div><div class="value">${esc(n.ssid)}</div>${n.active ? `<div class="sub">connected${ip ? ` · ${esc(ip)}` : ""}</div>` : ""}</div></div>
        <div class="do">${n.secured ? ICON.lock : ""}<button class="btn" style="min-height:44px;padding:4px 22px" data-act="${n.active ? "forget" : "join"}">${n.active ? "forget" : "join"}</button></div>
      </div>`).join("");
  }
  function renderKeys() {
    const rows = state.sym ? SYM_ROWS : KEY_ROWS;
    const cap = (k) => (state.shift && !state.sym ? k.toUpperCase() : k);
    const keys = (row) => row.map((k) => `<div class="key" data-act="key" data-k="${esc(cap(k))}">${esc(cap(k))}</div>`).join("");
    $(".keys").innerHTML = `
      <div class="krow">${keys(rows[0])}</div>
      <div class="krow">${keys(rows[1])}</div>
      <div class="krow"><div class="key wide ${state.shift ? "primary" : ""}" data-act="shift">shift</div>${keys(rows[2])}<div class="key wide" data-act="delete">delete</div></div>
      <div class="krow"><div class="key wider" data-act="sym">${state.sym ? "abc" : "?123"}</div><div class="key space" data-act="key" data-k=" "></div><div class="key wide" data-act="back">cancel</div><div class="key primary ${state.busy ? "busy" : ""}" data-act="connect">join</div></div>`;
    $(".pw .text").textContent = state.reveal ? state.pw : "•".repeat(state.pw.length);
    $(".pw .text").classList.toggle("hidden", !state.reveal);
    $('[data-act="reveal"]').textContent = state.reveal ? "hide" : "show";
  }

  // ---- actions ----
  function step(key, dir) {
    const cur = state.unit.settings[key];
    const next = nextValue(bounds(key), cur, dir, KNOB_VIEW[key].wrap);
    if (next === cur) return;
    state.unit.settings[key] = next; // optimistic; a failed PUT reloads
    renderSettings();
    queuePut(key, next);
  }
  function resetKeyboard() {
    state.pw = ""; state.reveal = false; state.shift = false; state.sym = false; state.busy = false;
  }
  async function openNetworks(rescan) {
    show("network");
    status(rescan ? "looking…" : "");
    try {
      const data = await fetch(`/unit/wifi${rescan ? "?rescan=1" : ""}`, { cache: "no-store" }).then((r) => r.json());
      state.nets = data.networks || [];
      if (state.unit) state.unit.connectivity = data;
      onConnectivity?.(data);
      renderNetworks();
      status("");
    } catch { status("could not look for networks"); }
  }
  async function connect() {
    state.busy = true;      // the idle timer waits for the join
    clearTimeout(state.idle);
    renderKeys();
    status(`joining ${state.ssid}…`);
    try {
      const r = await post("/unit/wifi/join", { ssid: state.ssid, password: state.pw });
      status(r.message || "connected");
      resetKeyboard();
      await load();
      renderSettings();
      show("settings");
    } catch (e) {
      status(friendly(e.message) || "could not join");
      state.busy = false;
      renderKeys();
      touch();
    }
  }
  function arm(what) {
    // A one-tap reboot is too easy to brush against: the first tap arms
    // the button for a few seconds, the second one acts (QA on #157).
    clearTimeout(state.armTimer);
    state.armed = what;
    state.armTimer = setTimeout(() => { state.armed = null; if (state.screen === "settings") renderSettings(); }, CONFIRM_MS);
    renderSettings();
  }

  const actions = {
    close: () => close(),
    back: () => {
      if (state.screen === "password") { resetKeyboard(); show("network"); }
      else { renderSettings(); show("settings"); }
    },
    step: (el) => step(el.parentElement.dataset.key, Number(el.dataset.dir)),
    night: () => {
      const on = state.unit.settings.NIGHT_ENABLED ? 0 : 1;
      state.unit.settings.NIGHT_ENABLED = on;
      renderSettings();
      queuePut("NIGHT_ENABLED", on);
    },
    rotate: async () => {
      const r = (state.unit.settings.ROTATE + 90) % 360;
      try { await put({ ROTATE: r }); status("turns on the next restart"); }
      catch (e) { status(`could not save: ${friendly(e.message)}`); }
      renderSettings();
    },
    network: () => openNetworks(false),
    rescan: () => openNetworks(true),
    restart: async (el) => {
      if (state.armed !== "restart") return arm("restart");
      state.armed = null;
      el.classList.add("busy");
      status("restarting — back in a minute");
      try { await post("/unit/reboot"); }
      catch (e) { status(friendly(e.message) || "could not restart"); el.classList.remove("busy"); }
    },
    join: (el) => {
      const net = state.nets.find((n) => n.ssid === el.closest(".net").dataset.ssid);
      if (!net) return;
      resetKeyboard();
      state.ssid = net.ssid;
      $(".ssid").textContent = net.ssid;
      show("password");
      renderKeys();
      status("");
      if (!net.secured) return connect();
    },
    forget: async (el) => {
      const ssid = el.closest(".net").dataset.ssid;
      el.classList.add("busy");
      try { await post("/unit/wifi/forget", { ssid }); } catch (e) { status(friendly(e.message)); }
      return openNetworks(false);
    },
    key: (el) => { state.pw += el.dataset.k; if (state.shift) state.shift = false; renderKeys(); },
    delete: () => { state.pw = state.pw.slice(0, -1); renderKeys(); },
    shift: () => { state.shift = !state.shift; renderKeys(); },
    sym: () => { state.sym = !state.sym; state.shift = false; renderKeys(); },
    reveal: () => { state.reveal = !state.reveal; renderKeys(); },
    connect: () => connect(),
  };
  root.addEventListener("click", (ev) => {
    const el = ev.target.closest("[data-act]");
    if (!el) return;
    if (!state.busy) touch();
    const act = actions[el.dataset.act];
    if (act) act(el);
  });

  // ---- open / close / idle ----
  function touch() {
    clearTimeout(state.idle);
    if (state.busy) return; // a join in flight is not idleness
    state.idle = setTimeout(close, IDLE_CLOSE_MS);
  }
  async function open(screen = "settings") {
    if (state.open) return;
    state.open = true;
    root.classList.add("open");
    show(screen);
    status("");
    try {
      await load();
      renderSettings();
      if (screen === "network") openNetworks(true);
    } catch {
      status("this screen only works on the unit itself");
    }
  }
  function close() {
    clearTimeout(state.idle);
    clearTimeout(state.armTimer);
    state.armed = null;
    resetKeyboard();
    state.open = false;
    root.classList.remove("open");
  }
  root.addEventListener("pointerdown", touch, { passive: true });

  // The long press in the bottom-left corner. When it fires, the click the
  // finger's release would produce is swallowed: the plate under the corner
  // must not start its song because the owner opened settings.
  let hold = null;
  const inCorner = (ev) => ev.clientX <= CORNER_PX && ev.clientY >= window.innerHeight - CORNER_PX;
  document.addEventListener("pointerdown", (ev) => {
    if (state.open || ev.target.closest("#archive, #unit") || !inCorner(ev)) return;
    hold = setTimeout(() => {
      hold = null;
      document.addEventListener("click", (c) => { c.stopPropagation(); c.preventDefault(); }, { capture: true, once: true });
      open();
    }, OPEN_HOLD_MS);
  });
  const release = () => { if (hold) { clearTimeout(hold); hold = null; } };
  document.addEventListener("pointerup", release);
  document.addEventListener("pointercancel", release);
  document.addEventListener("pointermove", (ev) => { if (hold && !inCorner(ev)) release(); });

  // First boot in a new house: with no internet after twenty seconds, the
  // network list opens by itself — nobody has to know about the corner.
  // Afterwards the connectivity is re-read once a minute for the wall's
  // offline line (the poll only exists on the unit, where this mounted).
  function watch() {
    const offline = () => state.unit?.connectivity && state.unit.connectivity.state !== "full";
    if (initial) onConnectivity?.(initial.connectivity);
    if (offline()) {
      setTimeout(async () => {
        try { await load(); } catch { return; }
        if (offline() && !state.open) open("network");
      }, FIRST_BOOT_OPEN_MS);
    }
    setInterval(async () => {
      if (state.open) return;
      try { await load(); } catch { /* the service is briefly away */ }
    }, CONNECTIVITY_POLL_MS);
  }
  watch();

  return { open, close, load };
}

// ---- pure helpers (tested in unit-screen.test.js) ----

export function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

// Four signal bars, lit by quartile; #4a3f2e ink, the rest ghosted.
export function bars(signal) {
  const on = "#4a3f2e", off = "rgba(141,128,101,0.35)";
  const lit = signal >= 75 ? 4 : signal >= 50 ? 3 : signal >= 25 ? 2 : 1;
  const rects = [[0, 14, 4], [6, 10, 8], [12, 6, 12], [18, 2, 16]]
    .map(([x, y, h], i) => `<rect x="${x}" y="${y}" width="4" height="${h}" fill="${i < lit ? on : off}"/>`)
    .join("");
  return `<svg width="24" height="18" viewBox="0 0 24 18">${rects}</svg>`;
}

// The next value a stepper lands on: one server-given step, clamped to the
// server's bounds — or, for the hours, wrapped (23:00 + 1 = 00:00).
export function nextValue({ min, max, step }, current, dir, wrap = false) {
  const raw = current + dir * step;
  let next = Math.round(raw * 1000) / 1000;
  if (wrap) {
    const span = max - min + step;
    next = ((next - min) % span + span) % span + min;
  } else {
    next = Math.min(max, Math.max(min, next));
  }
  return next;
}

// A message the owner can act on: the server's detail is kept when it is
// NetworkManager's own sentence; a stack-trace-shaped one is not.
export function friendly(message) {
  const m = String(message || "");
  if (/FileNotFoundError|could not run|NotFound/i.test(m)) return "this unit can't do that from here";
  if (/^unit \d{3}$/.test(m)) return "the wall didn't answer";
  return m;
}
