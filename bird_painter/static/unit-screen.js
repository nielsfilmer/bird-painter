// The table model's settings screen — the unit's own knobs, night mode and
// WiFi, drawn on the wall's paper (#123). Loaded by the wall page in panel
// mode only; it talks to the loopback-only `/unit` endpoints, so it does
// nothing useful (and shows nothing) when the page is opened from another
// machine. Pure DOM + fetch, no framework; every number the screen shows
// comes from the server, never from the page's own guess.
//
// Opening it: a long press (1.5 s) in the bottom-left corner of the wall —
// a place a hand doesn't land by accident, on a screen with no other
// chrome. Closing: the ×, or a minute of no touches.

const OPEN_HOLD_MS = 1500;
const IDLE_CLOSE_MS = 60_000;
const CORNER_PX = 120;

const STEPS = {
  CAPTION: { step: 0.1, min: 0.5, max: 2, show: (v) => `${Math.round(v * 100)}%`, label: "lettering" },
  UI: { step: 0.1, min: 0.5, max: 2, show: (v) => `${Math.round(v * 100)}%`, label: "controls" },
  MAX_LIVE: { step: 1, min: 1, max: 12, show: (v) => `${v}`, label: "birds on the sheet" },
  NIGHT_FROM: { step: 1, min: 0, max: 23, show: (v) => `${String(v).padStart(2, "0")}:00`, label: "from" },
  NIGHT_TO: { step: 1, min: 0, max: 23, show: (v) => `${String(v).padStart(2, "0")}:00`, label: "until" },
  NIGHT_BRIGHTNESS: { step: 5, min: 5, max: 100, show: (v) => `${v}%`, label: "night brightness" },
};

const ICON = {
  close: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>',
  minus: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M5 12h14"/></svg>',
  plus: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
  lock: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#8d8065" stroke-width="1.6"><rect x="5" y="10" width="14" height="10" rx="1"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>',
};

function bars(signal) {
  const on = "#4a3f2e", off = "rgba(141,128,101,0.35)";
  const lit = signal >= 75 ? 4 : signal >= 50 ? 3 : signal >= 25 ? 2 : 1;
  const rects = [[0, 14, 4], [6, 10, 8], [12, 6, 12], [18, 2, 16]]
    .map(([x, y, h], i) => `<rect x="${x}" y="${y}" width="4" height="${h}" fill="${i < lit ? on : off}"/>`)
    .join("");
  return `<svg width="24" height="18" viewBox="0 0 24 18">${rects}</svg>`;
}

const CSS = `
#unit { position: fixed; inset: 0; z-index: 30; display: none; color: #4a3f2e;
  font-family: Georgia, "Times New Roman", serif; background: var(--paper, #ece1c6);
  user-select: none; -webkit-user-select: none; touch-action: manipulation; }
#unit.open { display: block; }
#unit .screen { position: absolute; inset: 0; display: none; }
#unit .screen.on { display: block; }
#unit h2 { position: absolute; left: 0; right: 0; top: 6vh; margin: 0; text-align: center;
  font-variant: small-caps; letter-spacing: 0.18em; font-weight: normal; font-size: calc(2.7vmin * var(--ui-scale, 1)); }
#unit .close { position: absolute; top: 14px; right: 18px; width: 52px; height: 52px; display: flex;
  align-items: center; justify-content: center; color: #8d8065; cursor: pointer; }
#unit .grid { position: absolute; left: 7.5%; right: 7.5%; top: 17vh; bottom: 4vh; display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 3vh 7.5%; overflow-y: auto; }
#unit .group { display: flex; flex-direction: column; gap: 10px; }
#unit .group-title { font-variant: small-caps; letter-spacing: 0.14em; font-size: calc(1.6vmin * var(--ui-scale, 1));
  border-bottom: 1px solid rgba(141,128,101,0.45); padding-bottom: 6px; }
#unit .row { display: flex; align-items: center; justify-content: space-between; gap: 24px; min-height: 56px; }
#unit .label { font-variant: small-caps; letter-spacing: 0.08em; font-size: calc(1.25vmin * var(--ui-scale, 1)); color: #8d8065; }
#unit .value { font-size: calc(1.85vmin * var(--ui-scale, 1)); overflow-wrap: anywhere; }
#unit .stepper { display: flex; align-items: center; gap: 14px; }
#unit .step { display: inline-flex; align-items: center; justify-content: center; width: 48px; height: 48px;
  border: 1px solid #8d8065; cursor: pointer; }
#unit .step.off { opacity: 0.3; }
#unit .stepper .value { min-width: 64px; text-align: center; }
#unit .btn { display: inline-flex; align-items: center; justify-content: center; min-height: 48px; padding: 6px 30px;
  border: 1px solid #8d8065; font-style: italic; font-size: calc(1.4vmin * var(--ui-scale, 1)); background: none;
  color: inherit; font-family: inherit; cursor: pointer; white-space: nowrap; }
#unit .btn.primary { background: #4a3f2e; color: #f4edda; border-color: #4a3f2e; }
#unit .btn.busy { opacity: 0.5; pointer-events: none; }
#unit .switch { position: relative; width: 58px; height: 30px; border: 1px solid #8d8065; border-radius: 15px;
  box-sizing: content-box; padding: 9px 0; background-clip: content-box; margin: 0 -2px; cursor: pointer; }
#unit .switch .knob { position: absolute; top: 12px; left: 3px; width: 22px; height: 22px; border-radius: 11px; background: #8d8065; }
#unit .switch.on { background: #4a3f2e; border-color: #4a3f2e; }
#unit .switch.on .knob { left: 31px; background: #f4edda; }
#unit .list { position: absolute; left: 15.6%; right: 15.6%; top: 17.8vh; bottom: 16vh; overflow-y: auto; }
#unit .net { display: flex; align-items: center; justify-content: space-between; gap: 24px; min-height: 56px;
  border-bottom: 1px solid rgba(141,128,101,0.35); padding: 6px 0; }
#unit .net .who { display: flex; align-items: center; gap: 20px; min-width: 0; }
#unit .net .sub { font-style: italic; font-size: calc(1.35vmin * var(--ui-scale, 1)); color: #6b5e45; }
#unit .net .do { display: flex; align-items: center; gap: 18px; }
#unit .foot { position: absolute; left: 0; right: 0; bottom: 8vh; display: flex; justify-content: center; gap: 24px; }
#unit .hint { font-style: italic; letter-spacing: 0.12em; color: #8d8065; text-align: center;
  font-size: calc(1.55vmin * var(--ui-scale, 1)); }
#unit .field { position: absolute; left: 15.6%; right: 15.6%; top: 7vh; display: flex; flex-direction: column; gap: 14px; }
#unit .field .pw { display: flex; align-items: center; gap: 16px; border-bottom: 1px solid #8d8065; padding: 8px 4px; }
#unit .field .pw .text { flex-grow: 1; font-size: calc(2.3vmin * var(--ui-scale, 1)); letter-spacing: 0.06em; min-height: 1.3em; overflow-wrap: anywhere; }
#unit .field .pw .text.hidden { letter-spacing: 0.22em; }
#unit .keys { position: absolute; left: 0; right: 0; top: 36vh; display: flex; flex-direction: column; align-items: center; gap: 10px; }
#unit .krow { display: flex; gap: 10px; }
#unit .key { display: flex; align-items: center; justify-content: center; width: 7.8vw; height: 7.5vh; min-height: 44px;
  border: 1px solid #8d8065; font-size: calc(1.85vmin * var(--ui-scale, 1)); background: rgba(255,252,240,0.35); cursor: pointer; }
#unit .key:active { background: #4a3f2e; color: #f4edda; }
#unit .key.wide { width: 10.2vw; font-size: calc(1.25vmin * var(--ui-scale, 1)); font-variant: small-caps; letter-spacing: 0.08em; }
#unit .key.wider { width: 12.5vw; }
#unit .key.space { width: 40.6vw; }
#unit .key.primary { width: 15.6vw; background: #4a3f2e; color: #f4edda; border-color: #4a3f2e; }
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

export function mountUnitScreen({ initial = null, onSettings } = {}) {
  const style = document.createElement("style");
  style.textContent = CSS;
  document.head.appendChild(style);
  const root = document.createElement("div");
  root.id = "unit";
  root.innerHTML = `
    <div class="screen settings" data-screen="settings">
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
        <div class="value" style="text-align:center"><span class="ssid"></span></div>
        <div class="pw"><div class="text hidden"></div><button class="btn" data-act="reveal" style="min-height:44px;padding:4px 20px">show</button></div>
      </div>
      <div class="keys"></div>
      <div class="status hint"></div>
    </div>`;
  document.body.appendChild(root);

  const state = { open: false, screen: "settings", unit: initial, nets: [], ssid: null, pw: "", reveal: false, shift: false, sym: false, idle: null };
  const $ = (sel, el = root) => el.querySelector(sel);

  // ---- server ----
  async function load() {
    const r = await fetch("/unit", { cache: "no-store" });
    if (!r.ok) throw new Error(`unit ${r.status}`);
    state.unit = await r.json();
    return state.unit;
  }
  async function put(changes) {
    const r = await fetch("/unit", { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(changes) });
    if (!r.ok) throw new Error(`unit ${r.status}`);
    state.unit = await r.json();
    onSettings?.(state.unit);
    return state.unit;
  }
  async function post(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body ?? {}) });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || `${path} ${r.status}`);
    return data;
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
    const spec = STEPS[key];
    return `<div class="stepper" data-key="${key}">
      <div class="step ${value <= spec.min ? "off" : ""}" data-act="step" data-dir="-1">${ICON.minus}</div>
      <div class="value">${spec.show(value)}</div>
      <div class="step ${value >= spec.max ? "off" : ""}" data-act="step" data-dir="1">${ICON.plus}</div></div>`;
  }
  function renderSettings() {
    const u = state.unit;
    if (!u) return;
    const s = u.settings;
    const hh = (h) => `${String(h).padStart(2, "0")}:00`;
    const net = u.connectivity || {};
    const online = net.state === "full";
    $(".grid").innerHTML = `
      <div class="group"><div class="group-title">display</div>
        <div class="row"><div><div class="label">lettering</div><div class="value">${STEPS.CAPTION.show(s.CAPTION)}</div></div>${stepper("CAPTION", s.CAPTION)}</div>
        <div class="row"><div><div class="label">controls</div><div class="value">${STEPS.UI.show(s.UI)}</div></div>${stepper("UI", s.UI)}</div>
        <div class="row"><div><div class="label">birds on the sheet</div><div class="value">${s.MAX_LIVE}</div></div>${stepper("MAX_LIVE", s.MAX_LIVE)}</div>
        <div class="row"><div><div class="label">orientation</div><div class="value">${s.ROTATE % 180 === 0 ? "portrait" : "landscape"} · ${s.ROTATE}°</div></div><button class="btn" data-act="rotate">rotate</button></div>
      </div>
      <div class="group"><div class="group-title">night</div>
        <div class="row"><div><div class="label">dim at night</div><div class="value">${s.NIGHT_ENABLED ? `${hh(s.NIGHT_FROM)} – ${hh(s.NIGHT_TO)}` : "never"}${u.night?.is_night ? " · dimmed now" : ""}</div></div><div class="switch ${s.NIGHT_ENABLED ? "on" : ""}" data-act="night"><div class="knob"></div></div></div>
        <div class="row"><div><div class="label">from</div><div class="value">${hh(s.NIGHT_FROM)}</div></div>${stepper("NIGHT_FROM", s.NIGHT_FROM)}</div>
        <div class="row"><div><div class="label">until</div><div class="value">${hh(s.NIGHT_TO)}</div></div>${stepper("NIGHT_TO", s.NIGHT_TO)}</div>
        <div class="row"><div><div class="label">night brightness</div><div class="value">${s.NIGHT_BRIGHTNESS}%</div></div>${stepper("NIGHT_BRIGHTNESS", s.NIGHT_BRIGHTNESS)}</div>
      </div>
      <div class="group"><div class="group-title">network</div>
        <div class="row"><div><div class="label">${online ? "connected to" : net.ssid ? "on, but no internet" : "not connected"}</div><div class="value">${net.ssid ? `${esc(net.ssid)}${net.ip ? ` · ${net.ip}` : ""}` : "—"}</div></div><button class="btn" data-act="network">change network</button></div>
        <div class="row"><div><div class="label">recorder</div><div class="value">${esc(u.about?.recorder || "this unit")}</div></div></div>
      </div>
      <div class="group"><div class="group-title">about</div>
        <div class="row"><div><div class="label">unit</div><div class="value">${esc(u.about?.unit || "")}</div></div></div>
        <div class="row"><div><div class="label">ears</div><div class="value">${esc(u.about?.ears || "")}</div></div></div>
        <div class="row"><div><div class="label">wall</div><div class="value">${esc(u.about?.wall || "")}</div></div><button class="btn" data-act="restart">restart</button></div>
      </div>`;
  }
  function renderNetworks() {
    const list = $(".list");
    if (!state.nets.length) {
      list.innerHTML = `<div class="hint" style="padding-top:8vh">no networks in reach yet</div>`;
      return;
    }
    list.innerHTML = state.nets.map((n) => `
      <div class="net" data-ssid="${esc(n.ssid)}">
        <div class="who">${bars(n.signal)}<div><div class="value">${esc(n.ssid)}</div>${n.active ? `<div class="sub">connected${state.unit?.connectivity?.ip ? ` · ${state.unit.connectivity.ip}` : ""}</div>` : ""}</div></div>
        <div class="do">${n.secured ? ICON.lock : ""}<button class="btn" style="min-height:44px;padding:4px 22px" data-act="${n.active ? "forget" : "join"}">${n.active ? "forget" : "join"}</button></div>
      </div>`).join("");
  }
  function renderKeys() {
    const rows = state.sym ? SYM_ROWS : KEY_ROWS;
    const cap = (k) => (state.shift && !state.sym ? k.toUpperCase() : k);
    const row = (keys, extra = "") => `<div class="krow">${extra}${keys.map((k) => `<div class="key" data-act="key" data-k="${esc(cap(k))}">${esc(cap(k))}</div>`).join("")}</div>`;
    $(".keys").innerHTML = `
      ${row(rows[0])}
      ${row(rows[1])}
      <div class="krow"><div class="key wider wide ${state.shift ? "primary" : ""}" data-act="shift">shift</div>${rows[2].map((k) => `<div class="key" data-act="key" data-k="${esc(cap(k))}">${esc(cap(k))}</div>`).join("")}<div class="key wider wide" data-act="delete">delete</div></div>
      <div class="krow"><div class="key wide" data-act="sym" style="width:12.5vw">${state.sym ? "abc" : "?123"}</div><div class="key space" data-act="key" data-k=" "></div><div class="key wider wide" data-act="back">cancel</div><div class="key primary wide" data-act="connect">join</div></div>`;
    $(".pw .text").textContent = state.reveal ? state.pw : "•".repeat(state.pw.length);
    $(".pw .text").classList.toggle("hidden", !state.reveal);
    $('[data-act="reveal"]').textContent = state.reveal ? "hide" : "show";
  }

  // ---- actions ----
  async function step(key, dir) {
    const spec = STEPS[key];
    const cur = state.unit.settings[key];
    const next = Math.min(spec.max, Math.max(spec.min, Math.round((cur + dir * spec.step) * 100) / 100));
    if (next === cur) return;
    state.unit.settings[key] = next; // optimistic, then the server's word
    renderSettings();
    try { await put({ [key]: next }); status(""); } catch (e) { status(`could not save: ${e.message}`); }
    renderSettings();
  }
  async function openNetworks(rescan) {
    show("network");
    status(rescan ? "looking…" : "");
    try {
      const data = await fetch(`/unit/wifi${rescan ? "?rescan=1" : ""}`, { cache: "no-store" }).then((r) => r.json());
      state.nets = data.networks || [];
      if (state.unit) state.unit.connectivity = data;
      renderNetworks();
      status("");
    } catch (e) { status("could not look for networks"); }
  }
  async function connect() {
    const btn = $('[data-act="connect"]');
    btn.classList.add("busy");
    status(`joining ${state.ssid}…`);
    try {
      const r = await post("/unit/wifi/join", { ssid: state.ssid, password: state.pw });
      status(r.message || "connected");
      state.pw = "";
      await load();
      show("settings");
      renderSettings();
    } catch (e) {
      status(e.message || "could not join");
      btn.classList.remove("busy");
    }
  }

  root.addEventListener("click", async (ev) => {
    const el = ev.target.closest("[data-act]");
    if (!el) return;
    touch();
    const act = el.dataset.act;
    if (act === "close") return close();
    if (act === "back") return state.screen === "password" ? show("network") : (renderSettings(), show("settings"));
    if (act === "step") return step(el.parentElement.dataset.key, Number(el.dataset.dir));
    if (act === "night") { const on = !state.unit.settings.NIGHT_ENABLED; state.unit.settings.NIGHT_ENABLED = on ? 1 : 0; renderSettings(); try { await put({ NIGHT_ENABLED: on ? 1 : 0 }); } catch (e) { status(`could not save: ${e.message}`); } renderSettings(); return; }
    if (act === "rotate") { const r = (state.unit.settings.ROTATE + 90) % 360; try { await put({ ROTATE: r }); status("turns on the next restart"); } catch (e) { status(`could not save: ${e.message}`); } renderSettings(); return; }
    if (act === "network") return openNetworks(false);
    if (act === "rescan") return openNetworks(true);
    if (act === "restart") { el.classList.add("busy"); status("restarting — back in a minute"); try { await post("/unit/reboot"); } catch (e) { status(e.message || "could not restart"); el.classList.remove("busy"); } return; }
    if (act === "join") {
      const net = state.nets.find((n) => n.ssid === el.closest(".net").dataset.ssid);
      state.ssid = net.ssid;
      if (!net.secured) { state.pw = ""; show("password"); renderKeys(); return connect(); }
      state.pw = ""; state.reveal = false; state.shift = false; state.sym = false;
      $(".ssid").textContent = net.ssid;
      show("password"); renderKeys(); status("");
      return;
    }
    if (act === "forget") { const ssid = el.closest(".net").dataset.ssid; el.classList.add("busy"); try { await post("/unit/wifi/forget", { ssid }); } catch (e) { status(e.message); } return openNetworks(false); }
    if (act === "key") { state.pw += el.dataset.k; if (state.shift) state.shift = false; renderKeys(); return; }
    if (act === "delete") { state.pw = state.pw.slice(0, -1); renderKeys(); return; }
    if (act === "shift") { state.shift = !state.shift; renderKeys(); return; }
    if (act === "sym") { state.sym = !state.sym; state.shift = false; renderKeys(); return; }
    if (act === "reveal") { state.reveal = !state.reveal; renderKeys(); return; }
    if (act === "connect") return connect();
  });

  // ---- open / close / idle ----
  function touch() {
    clearTimeout(state.idle);
    state.idle = setTimeout(close, IDLE_CLOSE_MS);
  }
  async function open(screen = "settings") {
    if (state.open) return;
    state.open = true;
    root.classList.add("open");
    show(screen);
    status("");
    try { await load(); renderSettings(); if (screen === "network") openNetworks(true); }
    catch { status("this screen only works on the unit itself"); }
  }
  function close() {
    clearTimeout(state.idle);
    state.open = false;
    root.classList.remove("open");
  }
  root.addEventListener("pointerdown", touch, { passive: true });

  // The long press in the bottom-left corner.
  let hold = null;
  document.addEventListener("pointerdown", (ev) => {
    if (state.open || ev.target.closest("#archive, #unit")) return;
    if (ev.clientX > CORNER_PX || ev.clientY < window.innerHeight - CORNER_PX) return;
    hold = setTimeout(() => { hold = null; open(); }, OPEN_HOLD_MS);
  });
  const release = () => { if (hold) { clearTimeout(hold); hold = null; } };
  document.addEventListener("pointerup", release);
  document.addEventListener("pointercancel", release);
  document.addEventListener("pointermove", (ev) => { if (hold && (ev.clientX > CORNER_PX || ev.clientY < window.innerHeight - CORNER_PX)) release(); });

  return { open, close, load };
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}
