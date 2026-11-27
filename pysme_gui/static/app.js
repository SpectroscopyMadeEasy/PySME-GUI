"use strict";

/* ------------------------------------------------------------------ helpers */

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

/* Build DOM nodes. All data goes in through textContent, never innerHTML. */
function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "class") node.className = value;
    else if (key in node && key !== "list") node[key] = value;
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function fmt(value, digits = 4) {
  if (value === null || value === undefined) return "–";
  if (typeof value !== "number") return String(value);
  return Number.isInteger(value) ? String(value) : Number(value.toPrecision(digits + 2)).toString();
}
const fixed = (value, digits) => (typeof value === "number" ? value.toFixed(digits) : "–");

/* The address a start gives holds the access token. It is kept for this tab and
   taken out of the address, so that it does not stay in the address bar or get
   copied or bookmarked with it. */
function takeToken() {
  const address = new URL(location.href);
  const given = address.searchParams.get("token");
  try {
    if (!given) return sessionStorage.getItem("pysme-token") || "";
    sessionStorage.setItem("pysme-token", given);
    address.searchParams.delete("token");
    history.replaceState(null, "", address);
  } catch (error) { /* no storage: the address stays as it is */ }
  return given || "";
}
const TOKEN = takeToken();
const NATIVE = Boolean(window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.native);

async function request(path, body) {
  const options = { headers: { "X-PySME-Token": TOKEN } };
  if (body !== undefined) {
    options.method = "POST";
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch("api/" + path, options);
  } catch (error) {
    throw new Error("Lost the connection to the PySME process. Restart the app.");
  }
  const data = await response.json().catch(() => ({ error: response.statusText }));
  /* A copied address opens such a page: the token is not part of it. */
  if (response.status === 403 && data.error === "Missing or wrong access token") {
    throw new Error("This page is not connected to PySME. Start PySME again (from the menu or its shortcut): it opens a page that is.");
  }
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

/* Kept for problem reports. */
const recentErrors = [];
function rememberError(message) {
  recentErrors.push(`${new Date().toLocaleTimeString()}  ${message}`);
  if (recentErrors.length > 20) recentErrors.shift();
}
window.addEventListener("error", (e) => rememberError(`${e.message} (${e.filename}:${e.lineno})`));
window.addEventListener("unhandledrejection", (e) => rememberError(String(e.reason?.stack || e.reason)));

function toast(message, kind = "info", reportable = false) {
  if (kind === "error") rememberError(message);
  const close = h("button", { title: "Dismiss", onclick: () => node.remove() }, "✕");
  const text = h("div", {}, message);
  if (reportable) text.append(h("div", {}, h("button", { class: "link", onclick: () => { node.remove(); showReport(); } }, "Report this problem…")));
  const node = h("div", { class: "toast " + kind }, text, close);
  $("#toasts").append(node);
  while ($("#toasts").children.length > 4) $("#toasts").firstChild.remove();
  if (kind !== "error") setTimeout(() => node.remove(), 6000);
}

/* -------------------------------------------------------------------- state */

let S = null;                     // latest state from the server
let spectrum = { segments: [] };
let compareData = null;
let options = { atmospheres: [], nlte_grids: [], nlte_defaults: {}, patterns: [] };
let seenRevision = -1;
let logSeq = 0;
let pollTimer = null;
let lastJobKey = "";
const ui = { prereleases: false, installChoice: null, ownDraft: null, advancedOpen: null, tool: "zoom", drawer: "results", linePage: 0, lineQuery: "", element: "Fe", compare: null, flux: false, line: null, hiddenCurves: new Set(), removeWhat: 0, velocityMode: "whole", view: "spectrum", abundScale: "A", gridChoice: {} };

/* All changes are sent one after another, so that "type a value, then press
   Synthesize" always reaches PySME in that order. */
let queue = Promise.resolve();
function send(path, body = {}) {
  const task = queue.then(async () => {
    try {
      const data = await request(path, body);
      if (data.revision !== undefined) await apply(data);
      return data;
    } catch (error) {
      toast(error.message, "error");
      if (S) renderForms(true, true);   // show what PySME really has
      return null;
    }
  });
  queue = task.then(() => {}, () => {});
  return task;
}

async function refresh() {
  try {
    await apply(await request("state"));
  } catch (error) {
    toast(error.message, "error");
  }
}

const jobRunning = () => S && S.job.state === "running";

async function apply(state) {
  const changed = state.revision !== seenRevision;
  S = state;
  seenRevision = state.revision;
  if (changed) {
    try {
      spectrum = await request("spectrum");
    } catch (error) {
      spectrum = { segments: [] };
      toast(error.message, "error");
    }
    if (ui.compare !== null && !S.runs.some((r) => r.id === ui.compare)) {
      ui.compare = null;
      compareData = null;
    }
    await refreshChosenLine();      // before the plot, which marks it
  }
  renderTop();
  renderJob();
  renderForms(changed);
  renderReadiness();
  if (changed) {
    renderPlot();
    renderPattern();
    renderDrawer();
  }
  $("#solar-reference").value = S.solar_reference;
  renderLineEditor();
  if (S.log_seq > logSeq) await pullLog();
  schedulePoll();
}

function schedulePoll() {
  clearTimeout(pollTimer);
  const updating = S && S.update.task.state === "running";
  if (jobRunning() || updating) pollTimer = setTimeout(refresh, 600);
}

async function pullLog() {
  const data = await request("log?since=" + logSeq);
  logSeq = data.seq;
  if (!data.lines.length) return;
  const log = $("#log");
  const body = $(".drawer-body");
  const atEnd = body.scrollHeight - body.scrollTop - body.clientHeight < 40;
  log.textContent = (log.textContent + data.lines.join("\n") + "\n").slice(-200000);
  if (ui.drawer === "log" && atEnd) body.scrollTop = body.scrollHeight;
}

/* ------------------------------------------------------------------ top bar */

function renderTop() {
  const busy = jobRunning();
  $("#btn-synth").disabled = busy || S.ready.synth.length > 0;
  $("#btn-fit").disabled = busy || S.ready.fit.length > 0;
  $("#btn-synth").hidden = busy;
  $("#btn-fit").hidden = busy;
  $("#btn-stop").hidden = !busy;
  for (const id of ["#btn-new", "#btn-open", "#btn-import", "#btn-save"]) $(id).disabled = busy;
  $("#forms").disabled = busy;
  const name = S.path ? baseName(S.path) : "Untitled";
  document.title = `${name}${S.dirty ? " •" : ""} — PySME`;
  $("#btn-quit").hidden = !S.can_quit;          // the Mac app has its own menu
  const badge = $("#stage-badge");
  badge.hidden = S.release.stage === "stable";
  badge.textContent = S.release.stage;           // "alpha" or "beta"
  /* The dot: something newer, a result not looked at yet, or a restart to come. */
  const { latest, task, restart } = S.update;
  const unseen = (task.state === "done" || task.state === "failed") && !task.seen;
  $("#update-dot").hidden = !(restart || unseen || (latest && (latest.pysme_new || latest.gui_newer)));
}

/* The settings dialog is rebuilt while something installs. Starting the turn
   where a clock says it should be keeps a new spinner in step with the old. */
function spinner() {
  const element = h("span", { class: "spinner" });
  element.style.animationDelay = `-${Date.now() % 800}ms`;       // 800 ms per turn, see app.css
  return element;
}

function renderJob() {
  const job = S.job;
  const node = $("#job");
  node.className = job.state === "running" ? "job running" : "job";
  node.onclick = null;
  // Rebuilding the line would restart the spinner at every progress message.
  const spinning = job.state === "running" && node.firstElementChild?.classList.contains("spinner");
  if (!spinning) node.replaceChildren();
  if (job.state === "running") {
    const title = job.kind === "fit" ? "Fitting" : "Synthesizing";
    const text = `${title} ${Math.floor(job.elapsed)} s · ${job.progress}`;
    if (!spinning) node.append(spinner(), h("span", { class: "job-text" }));
    const words = node.lastElementChild;
    words.textContent = text;
    // Within one run the room for the text only grows: otherwise the spinner in
    // front of it would jump back and forth with every new progress message.
    // A width, not a minimum: in a narrow window it may still shrink to an ellipsis.
    words.style.width = "auto";
    words.dataset.widest = Math.max(Number(words.dataset.widest || 0), words.scrollWidth + 1);
    words.style.width = `${words.dataset.widest}px`;
    node.title = job.progress;
  } else if (job.state === "done") {
    node.classList.add("ok");
    node.textContent = `✓ ${job.kind === "fit" ? "Fit" : "Synthesis"} finished in ${job.elapsed.toFixed(1)} s`;
  } else if (job.state === "failed") {
    node.classList.add("failed");
    node.textContent = `✕ ${job.kind === "fit" ? "Fit" : "Synthesis"} failed (details)`;
    node.onclick = () => showMessage("The run failed", job.error);
  } else if (job.state === "cancelled") {
    node.textContent = "Run stopped. Nothing was changed.";
  } else {
    node.textContent = S.path ? S.path : "";
    node.title = S.path || "";
  }
  const key = `${job.id}:${job.state}`;
  if (key !== lastJobKey) {
    if (job.state === "failed") toast(`${job.error}`, "error", true);
    if (job.state === "done") showDrawer(job.kind === "fit" ? "results" : ui.drawer);
    lastJobKey = key;
  }
}

function renderReadiness() {
  const box = $("#readiness");
  const list = h("ul");
  const fitting = $("#side-tabs .active").dataset.tab === "fit";
  const missing = fitting ? S.ready.fit : S.ready.synth;
  for (const item of missing) list.append(h("li", { class: "todo" }, item));
  if (!missing.length) list.append(h("li", { class: "good" }, fitting ? "Ready to fit" : "Ready to synthesize"));
  for (const item of S.ready.warnings) list.append(h("li", { class: "warn" }, item));
  box.replaceChildren(list);

  const banner = $("#banner");
  const messages = [];
  if (S.synth_stale) messages.push("Settings changed after this synthetic spectrum was computed. Run again to update it.");
  messages.push(...S.notes);
  banner.hidden = !messages.length;
  banner.textContent = messages.join("  ");
}

/* ------------------------------------------------------------------- inputs */

const velocitiesDiffer = () => new Set((S.params.vrad_each || []).map((v) => v.toPrecision(8))).size > 1;

function currentValue(name) {
  if (name.startsWith("atmo.")) return S.atmo[name.slice(5)] ?? "auto";
  if (name === "vrad" && velocitiesDiffer()) return null;
  return S.params[name];
}

let wavelengthKey = "";
let synthRanges = [];         // the ranges as last entered, without an observation
let stepFromServer;           // what the Step box was last filled with
let sendCount = 0;

/* Send what a box holds. Until its last change is answered the box is ahead of
   the stored value, and writing an older answer into it would undo a step of the
   up/down arrows. */
function sendFrom(box, path, body, answered) {
  const mine = String(++sendCount);
  box.dataset.pending = mine;
  send(path, body).then(() => {
    if (box.dataset.pending !== mine) return;
    delete box.dataset.pending;
    answered();
  });
}

function writeParam(input) {
  const value = currentValue(input.dataset.param);
  if (input.type === "checkbox") input.checked = Boolean(value);
  else if (input.tagName === "SELECT") {
    if (value !== null && ![...input.options].some((o) => o.value === String(value))) {
      input.append(h("option", { value: String(value) }, String(value)));
    }
    input.value = String(value);
  } else {
    const short = (v) => (typeof v === "number" ? Number(v.toPrecision(8)) : v ?? "");
    const text = Array.isArray(value) ? value.map(short).join(", ") : String(short(value));
    if (input.value !== text) input.value = text;
  }
}

function renderMuState() {
  $("#mu-state").textContent = S.params.mu_default ? "(default)" : "(your own)";
  $("#mu-default").disabled = S.params.mu_default;
}

/* Every synthesis gives both scales, so this only chooses what is shown. */
const showingFlux = () => ui.flux && spectrum.segments.some((s) => s.flux);

function renderFluxScale() {
  for (const button of $$("#flux-scale button")) {
    const on = (button.dataset.normalised === "false") === ui.flux;
    button.classList.toggle("active", on);
    button.setAttribute("aria-pressed", String(on));
  }
  const hint = !ui.flux
    ? ["Flux divided by the continuum."]
    : ["Flux at the stellar surface, in erg s⁻¹ cm⁻² Å⁻¹. Not scaled by the star's radius or distance."];
  if (ui.flux && !showingFlux()) hint.push("It appears once there is a synthesis.");
  else if (ui.flux && S.observation) hint.push("The observation and residuals are hidden in this view.");
  $("#flux-hint").textContent = hint.join(" ");
}

/* "force" also overwrites a field the user is in the middle of typing. */
function renderForms(rebuild, force = false) {
  if (!rebuild) return;
  for (const input of $$("[data-param]")) {
    if (!force && (input.dataset.pending || (input.dataset.editing && input === document.activeElement))) continue;
    delete input.dataset.editing;
    writeParam(input);
  }
  renderFluxScale();
  renderMuState();
  // One box for all segments; say so when they differ, as after fitting each.
  const velocities = velocitiesDiffer() ? S.params.vrad_each.map((v) => v.toFixed(3)).join(", ") : "";
  $('[data-param="vrad"]').placeholder = velocities ? "per segment" : "";
  $("#vrad-hint").hidden = !velocities;
  $("#vrad-hint").textContent = velocities ? `v_rad of the segments: ${velocities} km/s. A value typed here is used for all.` : "";
  $("#snr-block").hidden = !(S.observation && !S.observation.has_uncs);
  const ip = S.params.iptype;
  $("#ip-hint").textContent = S.params.ipres > 0
    ? `Instrumental profile: ${ip}, R = ${Math.round(S.params.ipres)}.`
    : "R = 0: no instrumental broadening.";
  // Only rebuilt when its content changed, so that unrelated updates never
  // throw away a half-typed range.
  const key = JSON.stringify([S.setup.ranges, S.observation, S.observation && S.segments]);
  if (force || key !== wavelengthKey) {
    wavelengthKey = key;
    renderWavelength();
  }
  writeStep(force);
  renderLinelistCard();
  renderFitChips();
}

function rangeRows(container, ranges, onCommit) {
  const rows = h("div");
  const read = () => $$(".ranges", rows)
    .map((row) => $$("input", row).map((i) => i.value.trim()))
    .filter(([a, b]) => a !== "" && b !== "")
    .map(([a, b]) => [Number(a), Number(b)]);
  const add = (start = "", end = "") => {
    let edited = false;
    const field = (value, placeholder, label) => h("input", {
      type: "number", step: "any", value, placeholder, "aria-label": label,
      oninput: () => { edited = true; },
      onkeydown: (event) => { if (event.key === "Enter") event.target.blur(); },
    });
    const drop = () => { edited = false; row.remove(); onCommit(read()); };
    const row = h("div", { class: "ranges" },
      field(start, "start Å", "Range start"),
      h("span", {}, "–"),
      field(end, "end Å", "Range end"),
      h("button", { type: "button", title: "Remove range", onclick: drop }, "×"));
    // Committing after each box would send half-edited ranges such as 7000-6444.
    row.addEventListener("focusout", (event) => {
      if (!edited || row.contains(event.relatedTarget)) return;
      if ($$("input", row).every((i) => i.value.trim() !== "")) {
        edited = false;
        onCommit(read());
      }
    });
    rows.append(row);
  };
  if (ranges.length) ranges.forEach(([a, b]) => add(Number(a.toFixed(4)), Number(b.toFixed(4))));
  else add();
  container.append(rows, h("button", { type: "button", onclick: () => add() }, "+ range"));
}

/* The Step box stays the same element as long as there is no observation. In
   the Mac app the arrows of a replaced number box do nothing until the mouse has
   moved. */
function synthSetupControls() {
  const step = h("input", {
    type: "number", step: "0.01", min: "0", id: "synth-step", placeholder: "adaptive", "aria-label": "Wavelength step",
    onchange: () => {
      // Without a range there is nothing to store the step with yet.
      if (synthRanges.length) sendFrom(step, "synthsetup", { ranges: synthRanges, step: step.value }, () => writeStep(false));
    },
    onkeydown: (event) => { if (event.key === "Enter") step.blur(); },
  });
  stepFromServer = undefined;
  return [h("div", { id: "synth-ranges" }), h("div", { class: "grid", style: "margin-top:8px" },
    h("label", { title: "Leave empty to let PySME choose an adaptive grid" }, "Step"), step, h("span", {}, "Å"))];
}

/* Written only when PySME has another value than last time, so that a step typed
   before the first range stays. "force" restores what PySME really has. */
function writeStep(force) {
  const box = $("#synth-step");
  if (!box || (!force && box.dataset.pending)) return;
  const value = S.setup.step ?? null;
  if (!force && value === stepFromServer) return;
  if (force && value === null && !synthRanges.length) return;
  stepFromServer = value;
  if (value === null) box.value = "";
  else if (box.value === "" || Number(box.value) !== value) box.value = String(value);
}

function renderWavelength() {
  const box = $("#wavelength");
  const obs = S.observation;
  if (!obs) {
    if (!$("#synth-step")) box.replaceChildren(...synthSetupControls());
    synthRanges = S.setup.ranges;
    const rangesBox = $("#synth-ranges");
    rangesBox.replaceChildren();
    rangeRows(rangesBox, synthRanges, (value) => {
      synthRanges = value;
      send("synthsetup", { ranges: value, step: $("#synth-step").value });
    });
    return;
  }
  box.replaceChildren();
  box.append(h("p", { class: "hint" },
    `Taken from the observation: ${obs.used.toLocaleString()} of ${obs.npix.toLocaleString()} pixels in ${S.segments.length} segment(s).`));
  box.append(h("p", { class: "hint" }, "Windows restrict what is computed and fitted:"));
  rangeRows(box, obs.windows || [], (windows) => send("windows", { windows }));
  box.append(h("div", { class: "row" },
    h("button", { type: "button", onclick: addWindowFromView, title: "Add the wavelength range now visible in the plot" }, "Add visible range"),
    h("button", { type: "button", disabled: !obs.windows, onclick: () => send("windows", { windows: null }) }, "Use all"),
    h("button", {
      type: "button",
      onclick: async () => {
        if (await confirmDialog("Remove the observation?", "The observed spectrum and fit results are removed from this setup. Your file on disk is not touched.", "Remove")) {
          send("observation/remove");
        }
      },
    }, "Remove observation")));
}

function addWindowFromView() {
  const graph = $("#plot");
  if (!graph._fullLayout) return;
  const [a, b] = graph._fullLayout.xaxis.range.map(Number);
  let windows = [...(S.observation.windows || []), [Number(a.toFixed(3)), Number(b.toFixed(3))]];
  windows.sort((x, y) => x[0] - y[0]);
  const merged = [];
  for (const [start, end] of windows) {
    const last = merged[merged.length - 1];
    if (last && start <= last[1]) last[1] = Math.max(last[1], end);
    else merged.push([start, end]);
  }
  send("windows", { windows: merged });
}

function renderLinelistCard() {
  const list = S.linelist;
  const card = $("#linelist-card");
  card.replaceChildren();
  if (!list.nlines) {
    card.append(h("div", {}, "No line list. Request one from VALD for your wavelength range (extract stellar or extract all)."));
  } else {
    card.append(
      h("b", {}, `${list.nlines.toLocaleString()} lines`),
      h("div", {}, `${fixed(list.wmin, 2)} – ${fixed(list.wmax, 2)} Å (${list.medium}), ${list.format} format`),
      h("div", { class: "hint" }, list.file ? baseName(list.file) : "stored in the SME file"));
    card.lastChild.title = list.file || "";
    if (list.edited) {
      card.append(h("div", { class: "edited-note" }, `${list.edited} line${list.edited > 1 ? "s" : ""} edited here (see the Line list tab). The file is unchanged.`));
    }
  }
  card.append(h("button", { type: "button", "data-cmd": "linelist" }, "Load VALD list…"));
}

function toggleFit(name, on) {
  const current = new Set(S.params.fitparameters);
  if (on) current.add(name);
  else current.delete(name);
  send("params", { values: { fitparameters: [...current] } });
}

const LABELS = { teff: "Teff", logg: "log g", monh: "[M/H]", vmic: "vmic", vmac: "vmac", vsini: "v sin i" };

const parameterLabel = (name) => (name.startsWith("abund ") ? `A(${name.slice(6)})` : LABELS[name] || name);

function renderFitChips() {
  const chosen = S.params.fitparameters;
  $("#fit-basic").replaceChildren(...Object.entries(LABELS).map(([name, label]) =>
    h("button", {
      type: "button", class: "chip" + (chosen.includes(name) ? " on" : ""),
      "aria-pressed": String(chosen.includes(name)),
      onclick: () => toggleFit(name, !chosen.includes(name)),
    }, label)));
  // v_rad is chosen like any other parameter. PySME keeps that apart, in
  // vrad_flag, together with whether the segments share one value.
  const free = velocityFree();
  if (free) ui.velocityMode = S.params.vrad_flag;
  $("#fit-basic").append(h("button", {
    type: "button", class: "chip" + (free ? " on" : ""), "aria-pressed": String(free), "data-chip": "vrad",
    title: "Radial velocity of the observed spectrum. A fit finds it; a synthesis uses the value on the Star tab.",
    onclick: () => send("params", { values: { vrad_flag: free ? "fix" : ui.velocityMode } }),
  }, "v_rad"));
  const modes = $("#vrad-mode");
  modes.hidden = !free;
  for (const button of $$("button", modes)) {
    const on = button.dataset.vrad === S.params.vrad_flag;
    button.classList.toggle("active", on);
    button.setAttribute("aria-pressed", String(on));
  }
  $("#fit-abunds").replaceChildren(...chosen.filter((n) => n.startsWith("abund ")).map((name) =>
    h("button", { type: "button", class: "chip on", title: "Remove", onclick: () => toggleFit(name, false) }, `A(${name.slice(6)}) ×`)));
}

/* --------------------------------------------------------------------- plot */



const SLOW_LIMIT = 60000;
let plotKey = "";
let lineTimer = null;
let currentLines = [];
let linesBelongTo = "";       // the plot (see plotKey) those labels were chosen for
let plotShapes = [];
let plotCurves = [];          // what the line above the plot lists

/* PySME's mask is a set of flags, and a pixel can carry several: line (it is
   fitted), continuum and radial velocity. 0 means the pixel is not used. */
const MASK_FLAGS = [[1, "--m-line"], [2, "--m-cont"], [4, "--m-vrad"]];   // top to bottom
const MASK_BAND = 0.0255;        // height of the strip, of the panel

/* The velocity mask matters, and is shown, only while a fit is to find v_rad. */
const velocityFree = () => ["whole", "each"].includes(S?.params.vrad_flag);

/* The flags a strip shows. Where no pixel carries the velocity flag, PySME takes
   the line pixels for the velocity, so those are shown as its mask. */
function shownMask(segment) {
  const free = velocityFree();
  return segment.mask.map((m) => (m & 3) | (free && m & (segment.velocity_follows ? 1 : 4) ? 4 : 0));
}

/* Plotly reads tags such as <a href> in its texts. Text that comes from a file
   is to be shown as it is written. */
const plotText = (text) => String(text).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");

/* The labels as Plotly wants them, in the colours of the theme now shown. */
const lineAnnotations = (lines) => lines.map((line) => ({
  x: line.x, xref: "x", y: 0.035, yref: "y domain", yanchor: "bottom", text: plotText(line.species),
  textangle: -90, showarrow: false, font: { size: 10, color: css("--muted") },
  hovertext: `${plotText(line.species)} ${line.wave.toFixed(3)} Å`,
  captureevents: true,
}));

/* One strip under the data. Where pixels carry several flags, it is divided
   into as many bands, line on top, then continuum, then velocity. Excluded
   pixels (0) have no strip: they show as a gap. */
function maskStrips(segment) {
  const shapes = [];
  const { wave } = segment;
  if (!segment.mask) return shapes;
  const mask = shownMask(segment);
  let start = 0;
  for (let i = 1; i <= mask.length; i++) {
    if (i < mask.length && mask[i] === mask[start]) continue;
    const kinds = MASK_FLAGS.filter(([flag]) => mask[start] & flag);
    const height = MASK_BAND / kinds.length;
    kinds.forEach(([, colour], k) => shapes.push({
      type: "rect", xref: "x", yref: "y domain",
      x0: start === 0 ? wave[0] : (wave[start - 1] + wave[start]) / 2,
      x1: i === mask.length ? wave[i - 1] : (wave[i - 1] + wave[i]) / 2,
      y0: MASK_BAND - (k + 1) * height, y1: MASK_BAND - k * height,
      fillcolor: css(colour), opacity: 0.95, line: { width: 0 },
    }));
    start = i;
  }
  return shapes;
}

function buildPlot() {
  const surface = showingFlux();
  // An observation is on another scale, so the surface flux is shown as the model alone.
  const onScale = (list) => (surface ? list.map((s) => ({ wave: s.wave, synth: s.flux })) : list);
  const segments = onScale(spectrum.segments);
  const earlier = compareData ? onScale(compareData.segments).filter((s) => s.synth) : [];
  const total = segments.reduce((n, s) => n + s.wave.length, 0);
  const type = total > SLOW_LIMIT ? "scattergl" : "scatter";
  const hasObs = segments.some((s) => s.obs);
  const hasSynth = segments.some((s) => s.synth);
  const showResid = hasObs && hasSynth && $("#show-resid").checked;
  const fluxTitle = surface ? "Surface flux (erg s⁻¹ cm⁻² Å⁻¹)" : "Normalised flux";
  const bigNumbers = surface ? { exponentformat: "power", showexponent: "all" } : {};
  const stepped = hasObs ? "hvh" : "linear";
  const traces = [];
  const shapes = [];
  let first = { obs: true, synth: true, comp: true };
  const add = (key, trace) => {
    traces.push({ type, mode: "lines", legendgroup: key, showlegend: first[key] ?? false, ...trace });
    first[key] = false;
  };

  for (const seg of earlier) {
    add("comp", {
      x: seg.wave, y: seg.synth, name: compareData.label,
      line: { color: css("--compare"), width: 1.2 }, meta: "earlier",
    });
  }
  for (const seg of segments) {
    if (seg.obs) {
      add("obs", {
        x: seg.wave, y: seg.obs, name: "observed",
        line: { color: css("--obs"), width: 1, shape: stepped }, meta: "observed",
      });
      shapes.push(...maskStrips(seg));
    }
    if (seg.synth) {
      add("synth", {
        x: seg.wave, y: seg.synth, name: S.synth_stale ? "synthetic (out of date)" : "synthetic",
        line: { color: css("--synth"), width: 1.6, shape: stepped, dash: S.synth_stale ? "dot" : "solid" },
        meta: "synthetic",
      });
    }
    if (showResid && seg.obs && seg.synth) {
      traces.push({
        type, mode: "lines", x: seg.wave, y: seg.obs.map((v, i) => (v === null || seg.synth[i] === null ? null : v - seg.synth[i])),
        yaxis: "y2", showlegend: false, line: { color: css("--resid"), width: 1, shape: "hvh" },
        meta: "O − S",
      });
      if (seg.uncs) {
        for (const sign of [1, -1]) {
          traces.push({
            type, mode: "lines", x: seg.wave, y: seg.uncs.map((u) => (u === null ? null : sign * u)),
            yaxis: "y2", showlegend: false, hoverinfo: "skip", line: { color: css("--resid"), width: 0.7, dash: "dot" },
          });
        }
      }
    }
  }

  // The curves of the line above the plot, which is also its legend. The
  // uncertainty band goes with the residuals.
  plotCurves = ["observed", "synthetic", "earlier", "O − S"]
    .map((meta) => traces.find((t) => t.meta === meta))
    .filter(Boolean)
    .map((t) => ({
      meta: t.meta, colour: t.line.color, dashed: t.line.dash === "dot",
      name: t.meta === "O − S" ? t.meta : t.meta === "synthetic" ? t.name
        : t.meta === "earlier" ? t.name.trim().split(/\s+/).slice(0, 2).join(" ") : t.meta,
      title: t.meta === "earlier" ? t.name : "",
    }));
  for (const trace of traces) trace.visible = !ui.hiddenCurves.has(trace.meta || "O − S");

  const axis = {
    gridcolor: css("--grid"), zerolinecolor: css("--border"), linecolor: css("--border"),
    tickfont: { color: css("--muted"), size: 11 }, title: { font: { color: css("--muted"), size: 12 } },
  };
  // Leave room under the deepest line for the line labels and the mask strip.
  let low = Infinity, high = -Infinity;
  for (const seg of [...segments, ...earlier]) {
    for (const values of [seg.obs, seg.synth]) {
      for (const v of values || []) {
        if (v === null) continue;
        if (v < low) low = v;
        if (v > high) high = v;
      }
    }
  }
  const spread = Math.max(high - low, 0.02 * Math.abs(high) || 0.02);
  const yRange = Number.isFinite(low) ? [low - 0.2 * spread, high + 0.06 * spread] : undefined;

  const layout = {
    uirevision: plotKey,
    paper_bgcolor: css("--surface"), plot_bgcolor: css("--surface"), font: { color: css("--text") },
    margin: { l: 58, r: 14, t: 24, b: 40 },     // t: room for Plotly's buttons
    separators: ".",          // no thousands comma: wavelengths read 6438.5
    showlegend: false,        // see renderLegend; exported figures switch it on
    legend: { orientation: "h", x: 0, xanchor: "left", y: 1.0, yanchor: "bottom", font: { size: 11 } },
    hovermode: false,          // see wireCrosshair; line labels keep their own hover
    hoverlabel: { bgcolor: css("--surface"), bordercolor: css("--border"), font: { color: css("--text") } },
    dragmode: ui.tool === "zoom" ? "zoom" : "select",
    selectdirection: "h",
    newselection: { line: { color: css("--muted") } },
    xaxis: { ...axis, title: { ...axis.title, text: "Wavelength (Å)" }, showspikes: false, anchor: showResid ? "y2" : "y", exponentformat: "none", tickformat: "" , hoverformat: ".4f" },
    // The other scale needs its own height; the wavelength zoom stays.
    yaxis: { ...axis, ...bigNumbers, uirevision: `${plotKey}|${surface}`, title: { ...axis.title, text: fluxTitle }, domain: showResid ? [0.3, 1] : [0, 1], range: yRange },
    shapes: [...shapes, ...lineMarks()],
    // Redraw with the labels already there; without them they would vanish at every
    // redraw, such as after each abundance change, until looked up again.
    annotations: $("#show-lines").checked && linesBelongTo === plotKey ? lineAnnotations(currentLines) : [],
    selections: [],
    selectionrevision: S.revision,
  };
  if (showResid) layout.yaxis2 = { ...axis, ...bigNumbers, domain: [0, 0.23], title: { ...axis.title, text: "O − S" } };
  plotShapes = shapes;      // the mask strips, to which drawLineMark adds the mark
  return { traces, layout };
}

function renderPlot() {
  hideReadout();          // its numbers may belong to the curves being replaced
  const empty = !spectrum.segments.length;
  $("#empty").hidden = !empty;
  const ready = empty && !S.ready.synth.length;
  $("#empty-start").hidden = ready;
  $("#empty-ready").hidden = !ready;
  const graph = $("#plot");
  graph.style.visibility = empty ? "hidden" : "visible";
  renderPlotSelectors();
  $("#readout").hidden = empty;
  if (empty) {
    plotKey = "";
    return;
  }
  // Keep the user's zoom while only the synthetic spectrum changes.
  plotKey = spectrum.segments.map((s) => `${s.wave[0]}-${s.wave[s.wave.length - 1]}`).join("|") + ($("#show-resid").checked ? "r" : "");
  const { traces, layout } = buildPlot();
  renderLegend();
  const isNew = !graph._pysmeReady;
  return Plotly.react(graph, traces, layout, {
    responsive: true, displaylogo: false, scrollZoom: true,
    modeBarButtonsToRemove: ["toImage", "select2d", "lasso2d"],
  }).then(() => {
    if (isNew) {
      graph._pysmeReady = true;
      graph.on("plotly_selected", onSelected);
      graph.on("plotly_clickannotation", (event) => {
        const line = currentLines[event.index];
        if (line) chooseLine(line.id, true);
      });
      graph.on("plotly_relayout", (event) => {
        if (!Object.keys(event).some((k) => k.startsWith("xaxis"))) return;
        scheduleLineLabels();
        renderLineNote();       // whether the chosen line is still in view
      });
    }
    scheduleLineLabels();
    renderLineNote();
  });
}

function scheduleLineLabels() {
  clearTimeout(lineTimer);
  lineTimer = setTimeout(updateLineLabels, 150);
}

async function updateLineLabels() {
  const graph = $("#plot");
  if (!graph._fullLayout || !spectrum.segments.length) return;
  const [a, b] = graph._fullLayout.xaxis.range.map(Number);
  let lines = [];
  if ($("#show-lines").checked && S.linelist.nlines) {
    const width = graph._fullLayout.width || 800;
    const limit = Math.max(6, Math.floor(width / 26));
    // The list has rest wavelengths; the plot may be shifted by a velocity.
    const shift = spectrum.segments.find((s) => s.wave[s.wave.length - 1] >= a && s.wave[0] <= b)?.shift || 1;
    try {
      lines = (await request(`lines?wmin=${a / shift}&wmax=${b / shift}&limit=${limit}`)).lines;
    } catch (error) {
      return;
    }
  }
  // Greedy: strongest first, skip labels that would overlap one already placed.
  const spacing = (b - a) / Math.max(1, (graph._fullLayout.width - 80) / 13);
  const placed = [];
  for (const line of lines) {
    line.x = plotPosition(line.wave);
    if (placed.every((p) => Math.abs(p.x - line.x) > spacing)) placed.push(line);
  }
  const key = (list) => JSON.stringify(list.map((p) => [p.x, p.wave, p.species, p.id]));
  if (key(placed) === key(currentLines) && graph.layout.annotations?.length === placed.length) return;
  currentLines = placed;
  linesBelongTo = plotKey;
  Plotly.relayout(graph, { annotations: lineAnnotations(placed) });
}

async function onSelected(event) {
  if (ui.tool === "zoom" || !event || !event.range || !event.range.x) return;
  const [start, end] = event.range.x;
  const remove = ui.tool === "0";
  await send("mask", { start, end, value: remove ? ui.removeWhat : Number(ui.tool), remove });
}

const REMOVE_LABELS = { 0: "Remove mask", 1: "Remove line mask", 2: "Remove continuum mask", 4: "Remove velocity mask" };

/* The velocity tool and its menu entry come and go with v_rad as a free
   parameter; the remove button names what it takes off. */
function renderMaskTools() {
  const free = velocityFree();
  $("#tool-vrad").hidden = !free;
  $('#menu-remove [data-remove="4"]').hidden = !free;
  if (!free && ui.tool === "4") setTool("zoom");
  if (!free && ui.removeWhat === 4) ui.removeWhat = 0;
  $("#remove-label").textContent = REMOVE_LABELS[ui.removeWhat];
  for (const item of $$("#menu-remove [data-remove]")) {
    item.setAttribute("aria-checked", String(Number(item.dataset.remove) === ui.removeWhat));
  }
}

function renderPlotSelectors() {
  const segmentSelect = $("#segment");
  $("#segment-wrap").hidden = spectrum.segments.length < 2;
  segmentSelect.replaceChildren(h("option", { value: "all" }, "all"), ...spectrum.segments.map((s, i) =>
    h("option", { value: String(i) }, `${i + 1}: ${s.wave[0].toFixed(1)}–${s.wave[s.wave.length - 1].toFixed(1)} Å`)));
  $("#compare-wrap").hidden = !S || S.runs.length < 2;
  $("#compare").replaceChildren(h("option", { value: "" }, "nothing"), ...S.runs.slice().reverse().map((r) =>
    h("option", { value: String(r.id) }, `${r.label}  (${r.time})`)));
  $("#compare").value = ui.compare === null ? "" : String(ui.compare);
  $("#show-resid").disabled = showingFlux();
  const canMask = !showingFlux() && spectrum.segments.some((s) => s.obs);
  for (const button of $$("#tools button")) button.disabled = !canMask;
  if (!canMask && ui.tool !== "zoom") setTool("zoom", false);
  renderMaskTools();
}

function setTool(tool, redraw = true) {
  ui.tool = tool;
  for (const button of $$("#tools [data-tool]")) {
    button.classList.toggle("active", button.dataset.tool === tool);
    button.setAttribute("aria-pressed", String(button.dataset.tool === tool));
  }
  const graph = $("#plot");
  if (redraw && graph._pysmeReady) Plotly.relayout(graph, { dragmode: tool === "zoom" ? "zoom" : "select" });
}

function zoomTo(wmin, wmax) {
  const graph = $("#plot");
  if (!graph._pysmeReady) return;
  Plotly.relayout(graph, { "xaxis.range": [wmin, wmax] });
}

async function setCompare(id) {
  ui.compare = id;
  compareData = null;
  if (id !== null) {
    const data = await request("run?id=" + id);
    compareData = { ...data, label: S.runs.find((r) => r.id === id).label };
  }
  renderPlot();
  if (ui.drawer === "results") renderResults();     // its button says Overlay or Hide
}

/* ------------------------------------------------------------------- drawer */

function showDrawer(tab) {
  ui.drawer = tab;
  for (const button of $$("#drawer-tabs [data-tab]")) button.classList.toggle("active", button.dataset.tab === tab);
  for (const panel of $$(".drawer-body [data-panel]")) panel.hidden = panel.dataset.panel !== tab;
  if (S) renderDrawer();
}

function renderDrawer() {
  if (ui.drawer === "results") renderResults();
  else if (ui.drawer === "lines") renderLines();
  else if (ui.drawer === "abund") renderAbundances();
  else if (ui.drawer === "cite") renderCitations();
  else if (ui.drawer === "log") $(".drawer-body").scrollTop = $(".drawer-body").scrollHeight;
}

/* v_rad in the table of results: one row, or one per segment. */
function velocityRows(velocity, extraCell) {
  if (!velocity) return [];
  const rows = velocity.each && velocity.value.length > 1 ? velocity.value.length : 1;
  const notGiven = () => h("td", { class: "hint", title: "PySME computes no uncertainty for v_rad here" }, "not given");
  return Array.from({ length: rows }, (_, i) => h("tr", {},
    h("td", {}, rows > 1 ? `v_rad (km/s), segment ${i + 1}` : "v_rad (km/s)"),
    h("td", {}, fmt(velocity.initial[i] ?? velocity.initial[0])),
    h("td", {}, h("b", {}, fixed(velocity.value[i], 3))), notGiven(), notGiven(), extraCell ? h("td", {}) : null));
}

/* A fitted abundance relative to the Sun. */
function ratioCell(parameter) {
  const ratio = parameter.ratio;
  if (!ratio) return h("td", { class: "hint" }, parameter.name.startsWith("abund ") ? "–" : "");
  const errors = [[ratio.unc_fit, "ls"], [ratio.unc_sme, "SME"]]
    .filter(([value]) => value !== null).map(([value, kind]) => `± ${fmt(value, 2)} ${kind}`);
  return h("td", { class: "ratio" }, h("div", {}, `${ratio.label} `, h("b", {}, signed(ratio.value, 3))),
    h("div", { class: "hint" }, errors.length ? errors.join(", ") : "σ not given"));
}

function renderResults() {
  const box = $("#results");
  const main = h("div", { class: "results-main" });
  const side = h("div", { class: "results-runs" });
  const fit = S.fit;
  if (fit) {
    if (fit.outdated) main.append(h("p", { class: "hint" }, "A synthesis was run after this fit, so the plot may not show the fitted model."));
    const ratios = fit.parameters.some((p) => p.ratio);
    main.append(h("table", {},
      h("thead", {}, h("tr", {},
        h("th", {}, "Parameter"), h("th", {}, "Start"), h("th", {}, "Best fit"),
        h("th", { title: "From the least-squares covariance matrix" }, "σ (least squares)"),
        h("th", { title: "PySME's estimate from the distribution of residuals; usually larger" }, "σ (SME)"),
        ratios ? h("th", { title: `Against the ${S.solar_reference} Sun` }, "[X/Fe]") : null)),
      h("tbody", {}, fit.parameters.map((p) => h("tr", {},
        h("td", {}, parameterLabel(p.name)), h("td", {}, fmt(p.initial)),
        h("td", {}, h("b", {}, fmt(p.value, 5))), h("td", {}, fmt(p.unc_fit, 2)), h("td", {}, fmt(p.unc_sme, 2)),
        ratios ? ratioCell(p) : null)),
      velocityRows(fit.velocity, ratios))));
    if (ratios) {
      const joint = fit.parameters.some((p) => p.name === "abund Fe");
      main.append(h("p", { class: "hint" },
        `[X/Fe] is against the ${S.solar_reference} Sun. Its errors leave out those of Fe and of the solar values.` +
        (joint ? " Fe was fitted too, so the other elements get no [X/Fe] error here." : "")));
    }
    if (fit.velocity) {
      main.append(h("p", { class: "hint" },
        "PySME finds v_rad on its own, by matching the model to the observation each time it computes one. " +
        "It is not part of the least-squares fit, and PySME gives no uncertainty for it."));
    }
    const extra = [];
    if (fit.chisq !== null && fit.chisq !== undefined) extra.push(`reduced χ² = ${fmt(fit.chisq, 3)}`);
    // Files saved by 0.3.0a1 keep the velocity in this form.
    if (fit.vrad) extra.push(`v_rad = ${fit.vrad.map((v) => fixed(v, 3)).join(", ")} km/s`);
    main.append(h("div", { class: "row" }, h("span", {}, extra.join("    ")), h("span", { class: "spacer" }),
      fit.restore && h("button", { onclick: () => send("revert_fit"), title: "Restore the free parameters to their values before this fit" }, "Restore starting values")));
    for (const p of fit.parameters) {
      if (!p.solver) continue;
      const element = p.name.slice(6);
      const joint = fit.parameters.some((q) => q.name === "monh");
      main.append(h("p", { class: "hint" },
        `PySME solves for ${element} on the abundance-pattern scale, which excludes [M/H]: ` +
        `${fmt(p.solver.value, 5)} ± ${fmt(p.solver.unc_fit, 2)} (least squares), ± ${fmt(p.solver.unc_sme, 2)} (SME). ` +
        (joint ? `[M/H] was free too, so these errors do not carry over to A(${element}).` : `The same errors apply to A(${element}).`)));
    }
    main.append(h("p", { class: "hint" }, "Neither σ includes systematic errors from the model atmosphere, line data or continuum placement."));
  } else {
    main.append(h("p", { class: "hint" }, "No fit yet. Fit results appear here with both PySME uncertainty estimates."));
  }
  if (S.runs.length) {
    side.append(h("h3", {}, "Runs in this session"));
    side.append(h("ul", { class: "run-list" }, S.runs.slice().reverse().map((r) =>
      h("li", {}, h("span", { title: "Teff / log g / [M/H]" }, `${r.label}  ·  ${r.time}`),
        h("button", { onclick: () => setCompare(ui.compare === r.id ? null : r.id) }, ui.compare === r.id ? "Hide" : "Overlay")))));
  }
  box.replaceChildren(h("div", { class: "results-layout" }, main, side));
}

const LINES_PER_PAGE = 100;
let lineRender = 0;

const LINE_TITLES = { species: "Species", wlcent: "λ (Å)", excit: "χ (eV)", gflog: "log gf", gamrad: "Rad.", gamqst: "Stark", gamvw: "van der Waals", depth: "VALD depth" };
const LINE_DIGITS = { wlcent: 4, excit: 3, gflog: 3, gamrad: 2, gamqst: 2, gamvw: 3, depth: 3 };

/* --------------------------------------------------------- the chosen line */

/* One line can be chosen, in the table or by its label: it is marked in the plot
   and its data can be edited. Lines are known by an id that stays with them when
   a new wavelength moves them to another row. */
let lineChoice = 0;

/* With "reveal", the Line list tab is opened at that line. */
async function chooseLine(id, reveal = false) {
  const mine = ++lineChoice;
  let line = null;
  if (id !== null) {
    try {
      line = await request("linelist/line?id=" + id);
    } catch (error) {
      toast(error.message, "error");
      return;
    }
    if (mine !== lineChoice) return;
  }
  ui.line = line;
  ui.lineList = S.linelist.version;
  renderLineEditor(true);
  drawLineMark();
  if (reveal && line) {
    ui.lineQuery = "";
    $("#line-search").value = "";
    ui.linePage = Math.floor(line.row / LINES_PER_PAGE);
    ui.scrollToLine = true;
    const drawer = $("#drawer");
    drawer.classList.remove("collapsed");
    showDrawer("lines");
    makeRoomForTable();
    resizePlot();
  } else {
    markLineRows();
    if (line) makeRoomForTable();
    keepChosenRowInSight(false);   // the editor above the table may have pushed it out
  }
}

/* The editor takes room from the table. If that leaves only a row or two, the
   panel grows; the plot gets less height but shows the same range. */
function makeRoomForTable() {
  const scroller = $(".line-scroll");
  if (ui.drawer !== "lines" || !scroller.offsetParent) return;
  const missing = 130 - scroller.offsetHeight;
  if (missing <= 0) return;
  const drawer = $("#drawer");
  const limit = $("#work").clientHeight - 290;      // leaves the plot readable
  drawer.style.height = Math.min(Math.max(limit, drawer.offsetHeight), drawer.offsetHeight + missing) + "px";
}

async function refreshChosenLine() {
  if (!ui.line) return;
  if (S.linelist.version !== ui.lineList) {
    ui.line = null;                 // another list was loaded
    return;
  }
  try {
    ui.line = await request("linelist/line?id=" + ui.line.id);
  } catch (error) {
    ui.line = null;
  }
}

/* Where a rest wavelength appears in the spectrum shown: PySME moves the model
   by the radial velocity, segment by segment. */
function plotPosition(wave) {
  for (const segment of spectrum.segments) {
    const x = wave * (segment.shift || 1);
    if (x >= segment.wave[0] && x <= segment.wave[segment.wave.length - 1]) return x;
  }
  return wave * (spectrum.segments[0]?.shift || 1);
}

const lineMarks = () => (!ui.line ? [] : [{
  type: "line", xref: "x", yref: "paper", y0: 0, y1: 1,
  x0: plotPosition(ui.line.values.wlcent), x1: plotPosition(ui.line.values.wlcent),
  line: { color: css("--accent"), width: 1.2, dash: "dash" },
}]);

/* Draw or remove the mark; the view stays as it is. */
function drawLineMark() {
  const graph = $("#plot");
  if (graph._pysmeReady && spectrum.segments.length) Plotly.relayout(graph, { shapes: [...plotShapes, ...lineMarks()] });
}

const readValue = (value) => String(Number(value.toPrecision(10)));

function writeLineInput(input) {
  const value = ui.line.values[input.dataset.line];
  const text = value === null || value === undefined ? "" : readValue(value);
  // The arrows count from the "value" attribute. Without this, -1.909 would step
  // to -1.9 instead of -1.809.
  input.setAttribute("value", text);
  if (input.value !== text) input.value = text;
}

/* "all" also overwrites what is being typed, as when another line is chosen.
   The boxes are never replaced, so their arrows keep working. */
function renderLineEditor(all = false) {
  const box = $("#line-editor");
  box.hidden = !ui.line;
  if (!ui.line) {
    markLineRows();
    return;
  }
  const { species, values, original } = ui.line;
  $("#line-name").textContent = `${species} ${values.wlcent.toFixed(4)} Å`;
  for (const input of $$("[data-line]")) {
    const column = input.dataset.line;
    input.parentElement.hidden = !(column in values);
    input.parentElement.classList.toggle("edited", column in original);
    input.disabled = jobRunning();
    if (all) {
      delete input.dataset.pending;
      delete input.dataset.editing;
    } else if (input.dataset.pending || (input.dataset.editing && input === document.activeElement)) continue;
    if (column in values) writeLineInput(input);
  }
  $("#line-reset").disabled = jobRunning() || !Object.keys(original).length;
  $("#line-reset-all").hidden = !S.linelist.edited;
  $("#line-reset-all").disabled = jobRunning();
  $("#line-reset-all").textContent = `Restore all (${S.linelist.edited})`;
  renderLineNote();
}

/* Clicking a line never moves the plot, so say when its mark cannot be seen. */
function renderLineNote() {
  if (!ui.line) return;
  const range = $("#plot")._fullLayout?.xaxis.range.map(Number);
  let outside = false;
  if (range && spectrum.segments.length) {
    const x = plotPosition(ui.line.values.wlcent);
    outside = x < range[0] || x > range[1];
  }
  $("#line-note").textContent = outside ? "This line is outside the wavelengths now shown." : "";
}

function markLineRows() {
  for (const row of $$("#line-table tbody tr")) {
    row.classList.toggle("chosen", Number(row.dataset.id) === ui.line?.id);
  }
}

/* Only the table scrolls; scrollIntoView would move the window's layout too. */
function keepChosenRowInSight(centre) {
  const row = $("#line-table tr.chosen");
  const scroller = $(".line-scroll");
  if (!row) return false;
  const at = row.getBoundingClientRect();
  const frame = scroller.getBoundingClientRect();
  const top = frame.top + ($("#line-table thead")?.offsetHeight || 0);
  if (centre || at.top < top || at.bottom > frame.bottom) {
    scroller.scrollTop += at.top - top - (frame.bottom - top - at.height) / 2;
  }
  return true;
}

async function renderLines() {
  const limit = LINES_PER_PAGE;
  const mine = ++lineRender;
  const offset = ui.linePage * limit;
  const data = await request(`linelist?offset=${offset}&limit=${limit}&q=${encodeURIComponent(ui.lineQuery)}`);
  if (mine !== lineRender) return;        // a newer request is on its way
  const cell = (value, column, was) => h("td", {
    class: was && column in was ? "edited" : null,
    title: was && column in was ? `Loaded with ${readValue(was[column])}` : null,
  }, column in LINE_DIGITS ? fixed(value, LINE_DIGITS[column]) : value);
  /* The rest of what the list holds on a line, with where each value is from. */
  const sources = (id, line) => h("td", { class: "refs", onclick: (event) => event.stopPropagation() },
    h("button", { type: "button", class: "link", "aria-label": `All data and references of ${line}`, onclick: () => showSources(id) }, "ref"));
  const terms = (data.terms || []).some(Boolean);       // the short format has none
  $("#line-table").replaceChildren(
    h("thead", {}, h("tr", {}, data.columns.map((c) => h("th", {}, LINE_TITLES[c] || c)),
      terms ? h("th", {}, "Term") : null, h("th", {}, "Ref"))),
    h("tbody", {}, data.rows.map((row, k) => {
      const id = data.ids[k];
      const value = (column) => row[data.columns.indexOf(column)];
      return h("tr", {
        "data-id": String(id),
        onclick: () => chooseLine(ui.line?.id === id ? null : id),
      }, row.map((v, i) => cell(v, data.columns[i], data.edited[id])),
      terms ? h("td", {}, data.terms[k] || "–") : null,
      sources(id, `${value("species")} ${value("wlcent").toFixed(4)} Å`));
    })));
  markLineRows();
  if (ui.lineScroll !== undefined && ui.drawer === "lines") {
    $(".line-scroll").scrollTop = ui.lineScroll;
    ui.lineScroll = undefined;
  }
  if (ui.scrollToLine && keepChosenRowInSight(true)) ui.scrollToLine = false;
  const pages = Math.max(1, Math.ceil(data.total / limit));
  $("#line-page").textContent = `${data.total.toLocaleString()} lines · page ${ui.linePage + 1}/${pages}`;
  $("#line-prev").disabled = ui.linePage === 0;
  $("#line-next").disabled = ui.linePage + 1 >= pages;
}

function elementPosition(z) {
  if (z === 1) return [1, 1];
  if (z === 2) return [1, 18];
  if (z <= 4) return [2, z - 2];
  if (z <= 10) return [2, z + 8];
  if (z <= 12) return [3, z - 10];
  if (z <= 18) return [3, z];
  if (z <= 36) return [4, z - 18];
  if (z <= 54) return [5, z - 36];
  if (z <= 56) return [6, z - 54];
  if (z >= 57 && z <= 71) return [9, z - 53];
  if (z <= 86) return [6, z - 68];
  if (z <= 88) return [7, z - 86];
  if (z >= 89 && z <= 103) return [10, z - 85];
  return [7, z - 100];
}

function gridsFor(element) {
  const wanted = new RegExp(`(^|_)${element}(\\d{4})?(_|\\.)`);
  const list = options.nlte_grids.filter((g) => wanted.test(g));
  for (const extra of [options.nlte_defaults[element], S.nlte.grids[element]]) {
    if (extra && !list.includes(extra)) list.unshift(extra);
  }
  return list;
}

/* The grid the switch of an element would turn on: the one in use, else the one
   chosen in the editor, else PySME's own. */
function chosenGrid(element) {
  if (S.nlte.elements.includes(element) && S.nlte.grids[element]) return S.nlte.grids[element];
  return ui.gridChoice[element] || S.nlte.grids[element] || options.nlte_defaults[element] || gridsFor(element)[0];
}

function setNlte(element, enabled, grid = chosenGrid(element)) {
  return send("nlte", { element, enabled, grid });
}

function nlteControls(element) {
  const enabled = S.nlte.elements.includes(element);
  const grids = gridsFor(element);
  if (!grids.length) return h("p", { class: "hint" }, `PySME has no NLTE grid for ${element}.`);
  const select = h("select", { "aria-label": `NLTE grid for ${element}`, disabled: jobRunning() },
    grids.map((g) => h("option", { value: g }, g)));
  select.value = chosenGrid(element);
  select.addEventListener("change", () => {
    ui.gridChoice[element] = select.value;
    if (enabled) setNlte(element, true, select.value);
  });
  return h("div", {},
    h("label", { class: "check" },
      h("input", { type: "checkbox", checked: enabled, disabled: jobRunning(), onchange: (e) => setNlte(element, e.target.checked, select.value) }),
      " NLTE"),
    select);
}

/* --------------------------------------------------------------- abundances */

const ratioName = (element) => (element === "Fe" ? "[Fe/H]" : `[${element}/Fe]`);
/* H and He stay A(X) in either scale: [M/H] does not apply to them. */
const asRatio = (element) => ui.abundScale === "ratio" && element !== "H" && element !== "He";

/* With its sign, and no "-0.00". */
function signed(value, digits) {
  const text = value.toFixed(digits);
  if (Number(text) === 0) return (0).toFixed(digits);
  return value > 0 ? `+${text}` : text;
}

function renderScaleButtons() {
  for (const button of $$(".abund-scale [data-scale]")) {
    const on = button.dataset.scale === ui.abundScale;
    button.classList.toggle("active", on);
    button.setAttribute("aria-pressed", String(on));
  }
}

function chooseElement(element) {
  ui.element = element;
  renderAbundances();
  renderPattern();
}

/* The cells are made once and then only updated, so that what the pointer rests
   on stays: the fit dot shows only under the pointer. */
function buildTable(table, elements) {
  // The page already holds the controls, in the table's empty upper part.
  table.append(h("div", { class: "gap", style: "grid-row:8" }), ...elements.map((row) => {
    const [r, c] = elementPosition(row.z);
    const element = row.el;
    // Row 9 holds the lanthanides, which move up into the empty part of row 7.
    return h("div", { class: r === 9 ? "cell f-first" : "cell", "data-el": element, style: `grid-row:${r};grid-column:${c}` },
      h("button", { type: "button", class: "el", onclick: () => chooseElement(element) }, h("b", {}), h("small", {})),
      h("button", {
        type: "button", class: "el-nlte", "aria-label": `NLTE for ${element}`,
        onclick: () => {
          ui.element = element;
          setNlte(element, !S.nlte.elements.includes(element));
        },
      }, h("span", {}, "N")),
      element === "H" ? null : h("button", {
        type: "button", class: "el-fit", "aria-label": `Fit the abundance of ${element}`,
        onclick: () => {
          ui.element = element;
          toggleFit(`abund ${element}`, !S.params.fitparameters.includes(`abund ${element}`));
        },
      }, h("span", {})));
  }));
}

function updateCell(cell, row, fitting) {
  const element = row.el;
  const ratio = asRatio(element);
  const main = cell.querySelector(".el");
  const value = ratio ? row.ratio : row.A;
  main.classList.toggle("changed", Math.abs(row.delta || 0) > 0.0005);
  main.classList.toggle("selected", element === ui.element);
  const symbol = main.querySelector("b");
  // The Fe box holds another quantity in this scale, and says so.
  symbol.textContent = ratio && element === "Fe" ? "Fe/H" : element;     // short, between the switches
  symbol.classList.toggle("long", symbol.textContent.length > 2);
  const asA = ui.abundScale === "ratio" && !ratio;         // H, He: marked A, so one digit fewer
  const text = value === null ? "–" : ratio ? signed(value, 2) : value.toFixed(asA ? 1 : 2);
  main.querySelector("small").textContent = asA && value !== null ? `A ${text}` : text;

  const nlte = cell.querySelector(".el-nlte");
  const on = S.nlte.elements.includes(element);
  nlte.hidden = !on && !gridsFor(element).length;
  nlte.classList.toggle("on", on);
  nlte.setAttribute("aria-pressed", String(on));
  nlte.title = on ? "NLTE on" : "Turn NLTE on";
  nlte.disabled = jobRunning();

  const fit = cell.querySelector(".el-fit");
  if (!fit) return;
  const chosen = fitting.has(`abund ${element}`);
  fit.classList.toggle("on", chosen);
  fit.setAttribute("aria-pressed", String(chosen));
  fit.title = chosen ? "Stop fitting this abundance" : "Fit this abundance";
  fit.disabled = jobRunning();
}

/* The size of the grids is warned about with the other warnings, bottom left. */
function renderNlteSummary() {
  const box = $("#nlte-summary");
  const elements = S.nlte.elements;
  box.hidden = !elements.length;
  // This has little room; the filled N in the table shows which ones anyway.
  box.replaceChildren(
    h("span", { title: elements.join(", ") }, "NLTE: ",
      h("b", {}, elements.length > 8 ? `${elements.length} elements` : elements.join(", "))),
    h("button", { type: "button", disabled: jobRunning(), onclick: () => send("nlte/off") }, "Turn all off"));
}

let abundRender = 0;

async function renderAbundances() {
  const mine = ++abundRender;
  const data = await request("abund");
  if (mine !== abundRender) return;        // a newer one is on its way
  const fitting = new Set(S.params.fitparameters);
  const table = $("#ptable");
  if (!table.querySelector(".cell")) buildTable(table, data.elements);
  for (const row of data.elements) updateCell(table.querySelector(`.cell[data-el="${row.el}"]`), row, fitting);
  renderScaleButtons();
  renderNlteSummary();
  const chosen = data.elements.find((e) => e.el === ui.element) || data.elements[25];
  renderElementEditor(chosen, data, fitting);
}

/* The other way of writing the same abundance, under the box. */
function otherScale(chosen, data, ratio) {
  const element = chosen.el;
  if (element === "H" || element === "He") {
    return ui.abundScale === "ratio" ? "[M/H] does not apply to H and He, so they are given as A." : "";
  }
  if (chosen.solar === null) {
    return `${data.reference} has no solar value for ${element}.` + (ratio ? " Switch to A(X) to change it." : "");
  }
  if (ratio) return chosen.A === null ? "" : `A(${element}) = ${chosen.A.toFixed(3)}`;
  return chosen.ratio === null ? "" : `${ratioName(element)} = ${signed(chosen.ratio, 3)} (${data.reference})`;
}

/* The editor is built once per element and scale and then only updated.
   Replacing the number box after every step broke its arrows in the Mac app:
   WebKit's arrows learn from pointer movement which one the pointer is over, so
   a new box under a resting pointer only selected the number. */
function renderElementEditor(chosen, data, fitting) {
  const editor = $("#element-editor");
  const fixedElement = chosen.el === "H";
  const ratio = asRatio(chosen.el);
  const label = ratio ? ratioName(chosen.el) : `A(${chosen.el})`;
  const key = `${chosen.el} ${label}`;
  if (editor.dataset.element !== key || !editor.querySelector("input[type=number]")) {
    editor.dataset.element = key;
    const input = h("input", { type: "number", step: "0.05", "aria-label": label });
    let sent = 0;
    input.addEventListener("change", () => {
      const mine = ++sent;
      // Until the last change is answered the box is ahead of the stored value;
      // writing an older answer into it would undo a step.
      input.dataset.pending = "1";
      send("abund/set", { element: chosen.el, value: input.value, scale: ratio ? "ratio" : "A" }).then(() => {
        if (mine !== sent) return;
        delete input.dataset.pending;
        renderAbundances();
      });
    });
    const reset = h("button", { onclick: () => send("abund/set", { element: chosen.el, value: null }) }, "Reset");
    const fit = h("input", { type: "checkbox", onchange: (e) => toggleFit(`abund ${chosen.el}`, e.target.checked) });
    const meaning = !ratio ? "log10(N_X / N_H) + 12 as used in the synthesis, including [M/H]"
      : chosen.el === "Fe" ? "A(Fe) minus the Sun's" : `A(${chosen.el}) and A(Fe), each minus the Sun's, subtracted`;
    editor.replaceChildren(
      h("h3", {}, `${chosen.el}  (Z = ${chosen.z})`),
      h("div", { class: "row" }, h("label", { title: meaning }, label), input, reset),
      h("p", { class: "hint other" }),
      h("p", { class: "hint change" }),
      fixedElement ? "" : h("label", { class: "check fit-box" }, fit, " Fit this abundance"),
      h("div", { class: "nlte-box", style: "margin-top:8px" }));
  }
  const input = editor.querySelector("input[type=number]");
  const number = ratio ? chosen.ratio : chosen.A;
  const value = number === null ? "" : String(Number(number.toFixed(4)));
  if (!input.dataset.pending && (value === "" || Number(input.value) !== Number(value) || input.value === "")) input.value = value;
  input.disabled = fixedElement || jobRunning() || (ratio && chosen.solar === null);
  editor.querySelector(".row button").disabled = fixedElement || jobRunning() || !chosen.delta;
  editor.querySelector(".other").textContent = otherScale(chosen, data, ratio);
  editor.querySelector(".change").textContent = `Change relative to ${data.baseline}: ${chosen.delta === null ? "–" : (chosen.delta >= 0 ? "+" : "") + chosen.delta.toFixed(3)} dex (on top of [M/H] = ${fmt(data.monh)}).`;
  const fit = editor.querySelector(".fit-box input");
  if (fit) {
    fit.checked = fitting.has(`abund ${chosen.el}`);
    fit.disabled = jobRunning();
  }
  editor.querySelector(".nlte-box").replaceChildren(...[nlteControls(chosen.el)].flat().filter(Boolean));
}

/* -------------------------------------------------------- abundance pattern */

const SAME = 0.0005;          // dex; closer to solar proportions than this counts as solar
const MAX_NAMES = 20;

/* The abundances of the last fit that still hold, with their errors. */
function fittedElements(rows) {
  const fitted = new Map();
  for (const parameter of S.fit?.parameters || []) {
    if (!parameter.name.startsWith("abund ")) continue;
    const element = parameter.name.slice(6);
    const row = rows.find((r) => r.el === element);
    // Edited since the fit: its errors are no longer those of this value.
    if (!row || row.A === null || Math.abs(row.A - parameter.value) > 1e-6) continue;
    let errors = parameter;
    if (ui.abundScale === "ratio") {
      errors = parameter.ratio;
      if (errors && element !== "Fe" && Math.abs(errors.value - row.ratio) > 1e-6) continue;
      if (element === "Fe") errors = null;      // [Fe/H] is not on this axis
    }
    fitted.set(element, { sme: errors?.unc_sme ?? null, ls: errors?.unc_fit ?? null });
  }
  return fitted;
}

function patternFigure(data) {
  const ratio = ui.abundScale === "ratio";
  const rows = data.elements.filter((r) => (ratio ? r.z > 2 && r.ratio !== null : r.A !== null && r.A > -50));
  const fitted = fittedElements(data.elements);
  const nlte = new Set(S.nlte.elements);
  // Fe itself is the measure of [X/Fe].
  const height = (r) => (!ratio ? r.A : r.el === "Fe" ? 0 : r.ratio);
  const departs = (r) => r.el !== "Fe" && r.ratio !== null && Math.abs(r.ratio) > SAME;
  // An edited Fe moves everything else off solar proportions, so it is marked too.
  const marked = rows.filter((r) => departs(r) || fitted.has(r.el) || nlte.has(r.el) ||
    (r.el === "Fe" && Math.abs(r.delta || 0) > SAME));
  const named = marked.length <= MAX_NAMES ? null
    : new Set(marked.filter((r) => fitted.has(r.el) || nlte.has(r.el) || Math.abs(r.delta || 0) > SAME).map((r) => r.el));
  const name = (r) => (named && !named.has(r.el) ? "" : r.el + (nlte.has(r.el) ? " (N)" : ""));
  const bar = (r) => fitted.get(r.el)?.sme ?? fitted.get(r.el)?.ls ?? null;
  const about = (r) => {
    const lines = [`<b>${r.el}</b>  Z = ${r.z}`];
    if (r.el === "Fe") lines.push(data.feh === null ? "" : `[Fe/H] = ${signed(data.feh, 3)}`);
    else if (r.el !== "H" && r.el !== "He" && r.ratio !== null) lines.push(`[${r.el}/Fe] = ${signed(r.ratio, 3)}`);
    if (r.A !== null) lines.push(`A(${r.el}) = ${r.A.toFixed(3)}`);
    if (r.solar !== null) lines.push(`Sun: ${r.solar.toFixed(2)}`);
    const errors = fitted.get(r.el);
    if (errors) {
      const parts = [[errors.sme, "SME"], [errors.ls, "ls"]].filter(([v]) => v !== null).map(([v, kind]) => `${fmt(v, 2)} (${kind})`);
      lines.push(`fitted, σ ${parts.length ? parts.join(", ") : "not given"}`);
    }
    if (nlte.has(r.el)) lines.push("NLTE");
    return lines.filter(Boolean).join("<br>");
  };
  const points = (list, extra) => ({
    type: "scatter", x: list.map((r) => r.z), y: list.map(height),
    customdata: list.map((r) => [r.el, about(r)]), hovertemplate: "%{customdata[1]}<extra></extra>", ...extra,
  });

  const traces = [];
  if (!ratio) {
    const sun = data.elements.filter((r) => r.z <= 92);
    traces.push({
      type: "scatter", mode: "lines", x: sun.map((r) => r.z), y: sun.map((r) => r.solar),
      connectgaps: false, hoverinfo: "skip", line: { color: css("--compare"), width: 1.2 }, name: "Sun",
    });
  }
  traces.push(points(rows.filter((r) => !marked.includes(r)), { mode: "markers", marker: { color: css("--muted"), size: 5 } }));
  const withBar = marked.filter((r) => bar(r) !== null);
  const without = marked.filter((r) => bar(r) === null);
  for (const list of [without, withBar]) {
    if (!list.length) continue;
    traces.push(points(list, {
      mode: "markers", marker: { color: css("--synth"), size: 8 },
      error_y: list === withBar ? { type: "data", array: list.map(bar), color: css("--synth"), thickness: 1.4, width: 4 } : undefined,
    }));
  }
  const selected = rows.find((r) => r.el === ui.element);
  if (selected) {
    traces.push({
      type: "scatter", mode: "markers", x: [selected.z], y: [height(selected)], hoverinfo: "skip",
      marker: { symbol: "circle-open", size: 16, color: css("--accent"), line: { width: 2 } },
    });
  }

  // The names are annotations: updating a plot, Plotly can leave texts of points out.
  const names = marked.filter(name).map((r) => ({
    x: r.z, y: height(r), text: name(r), showarrow: false, font: { size: 11, color: css("--text") },
    // beside an error bar, not on it
    ...(bar(r) === null ? { yanchor: "bottom", yshift: 5 } : { xanchor: "left", xshift: 6 }),
  }));

  const reach = rows.flatMap((r) => [height(r) - (bar(r) || 0), height(r) + (bar(r) || 0)]);
  // [X/Fe] that is solar everywhere must not be blown up to the noise level.
  const range = ratio ? [Math.min(-0.5, ...reach.map((v) => v - 0.15)), Math.max(0.5, ...reach.map((v) => v + 0.2))] : undefined;
  const axis = {
    gridcolor: css("--grid"), zeroline: false, linecolor: css("--border"),
    tickfont: { color: css("--muted"), size: 11 }, title: { font: { color: css("--muted"), size: 12 } },
  };
  const layout = {
    uirevision: `${ui.abundScale}|${data.reference}`,
    paper_bgcolor: css("--surface"), plot_bgcolor: css("--surface"), font: { color: css("--text") },
    margin: { l: 58, r: 14, t: 30, b: 40 }, separators: ".", showlegend: false, hovermode: "closest",
    hoverlabel: { bgcolor: css("--surface"), bordercolor: css("--border"), font: { color: css("--text") } },
    xaxis: { ...axis, title: { ...axis.title, text: "Atomic number" }, range: [ratio ? 1.5 : -0.5, 93.5], dtick: 10 },
    yaxis: { ...axis, title: { ...axis.title, text: ratio ? "[X/Fe]" : "A(X)" }, range },
    shapes: ratio ? [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 0, y1: 0, line: { color: css("--muted"), width: 1 }, layer: "below" }] : [],
    annotations: [{
      xref: "paper", yref: "paper", x: 0, y: 1, xanchor: "left", yanchor: "bottom", showarrow: false,
      text: `Sun: ${data.reference}${ratio ? "" : " (blue line)"}   [Fe/H] = ${data.feh === null ? "–" : signed(data.feh, 3)}`,
      font: { size: 12, color: css("--muted") },
    }, ...names],
  };
  return { traces, layout, errors: withBar.length };
}

let patternRender = 0;

async function renderPattern() {
  if (ui.view !== "pattern" || !S) return;
  const mine = ++patternRender;
  const data = await request("abund");
  if (mine !== patternRender || ui.view !== "pattern") return;
  const graph = $("#pattern");
  const { traces, layout, errors } = patternFigure(data);
  $("#pattern-note").textContent = "Orange: off solar proportions, fitted or NLTE (N)." +
    (errors ? " Bars: σ of the last fit (SME)." : "");
  renderScaleButtons();
  await Plotly.react(graph, traces, layout, { displayModeBar: false, scrollZoom: true });
  if (graph._pysmeReady) return;
  graph._pysmeReady = true;
  graph.on("plotly_click", (event) => {
    const element = event.points[0]?.customdata?.[0];
    if (!element) return;
    ui.element = element;
    $("#drawer").classList.remove("collapsed");
    showDrawer("abund");
    renderPattern();
    resizePlot();
  });
}

function setView(view) {
  ui.view = view;
  const pattern = view === "pattern";
  if (pattern) setTool("zoom");
  for (const button of $$("#view-tabs [data-view]")) {
    button.classList.toggle("active", button.dataset.view === view);
    button.setAttribute("aria-pressed", String(button.dataset.view === view));
  }
  for (const item of $$(".plot-toolbar .for-pattern")) item.hidden = !pattern;
  $(".plot-toolbar .for-spectrum").hidden = pattern;
  $("#pattern").hidden = !pattern;       // it covers the spectrum, which stays as it was
  hideReadout();
  if (pattern) renderPattern();
  else resizePlot();
}

let citeRender = 0;

/* One entry of the bibliography, in the format chosen above the list. */
function citationNode(entry, format, ...lead) {
  return format === "bibtex"
    ? h("pre", { class: "cite-entry" }, entry.bibtex)
    : h("div", { class: "cite-entry" }, ...lead, h("span", {}, entry.text));
}

/* The whole list, or, coming from a line of the line list, that line alone. */
async function renderCitations() {
  const format = $("#cite-format").value;
  const list = $("#cite-list");
  const mine = ++citeRender;
  if (!list.children.length) list.textContent = "Building the reference list…";
  if (ui.cited && ui.cited.list !== S.linelist.version) ui.cited = null;
  const [data, line] = await Promise.all([
    request("citation?format=" + format),
    ui.cited ? request(`linelist/detail?id=${ui.cited.id}`).catch(() => null) : null,
  ]);
  if (mine !== citeRender) return;
  if (!line) ui.cited = null;
  $("#cite-from").hidden = !line;
  $("#cite-save").hidden = Boolean(line);      // it saves the whole list
  $("#cite-hint").hidden = Boolean(line);
  if (line) renderLineSources(list, data, line, format);
  else {
    ui.citeText = data.text;
    let number = 0;
    list.replaceChildren(...(data.sections.length ? data.sections.map((part) => {
      const section = h("section", { class: "cite-section" }, h("h3", {}, part.title));
      if (!part.entries.length) section.append(h("p", { class: "hint" }, part.empty));
      for (const entry of part.entries) {
        number += 1;
        section.append(citationNode(entry, format, h("span", { class: "cite-number" }, `[${number}]`),
          part.keys ? h("b", { class: "cite-key" }, entry.key) : ""));
      }
      return section;
    }) : [h("pre", {}, data.text)]));
  }
  if (ui.scrollToCited) {
    ui.scrollToCited = false;
    $(".drawer-body").scrollTop = 0;
  }
}

function showSource(key) {
  const list = $("#cite-list");
  let chosen = null;
  for (const node of $$("[data-source]", list)) {
    const on = node.dataset.source === key;
    node.classList.toggle("cited", on);
    if (on) chosen = node;
  }
  if (!chosen) return;
  const body = $(".drawer-body");
  body.scrollTop += chosen.getBoundingClientRect().top - body.getBoundingClientRect().top -
    $("#cite-from").offsetHeight - 24;
}

/* Every quantity of one line beside its source, then those references only.
   VALD names a source for each quantity, so one line often has several. */
function renderLineSources(list, data, line, format) {
  $("#cite-line").textContent = line.line;
  const listed = ["lines", "vald"].flatMap((id) => data.sections.find((part) => part.id === id)?.entries || []);
  const entryOf = (key) => listed.find((e) => e.key.toLowerCase() === key.toLowerCase());

  const sourceCell = (q) => h("td", { class: "line-source" },
    q.sources.map((key) => h("button", { type: "button", class: "link", onclick: () => showSource(key) }, key)),
    // A source stands behind the file's value, not behind one typed in here.
    q.file !== null && q.sources.length ? h("span", { class: "hint" }, "for the file's value") : null,
    q.note ? h("span", { class: "hint" }, q.note) : null,
    !q.sources.length && !q.note ? h("span", { class: "hint" }, "–") : null);
  const table = h("table", { class: "line-sources" },
    h("thead", {}, h("tr", {}, h("th", {}, "Quantity"), h("th", {}, "Value"), h("th", {}, "Source"))),
    h("tbody", {}, line.quantities.map((q) => h("tr", {},
      h("td", {}, q.name),
      h("td", {}, q.value, q.file !== null ? h("div", { class: "edited-note" }, `edited here; the file had ${q.file}`) : null),
      sourceCell(q)))));

  const section = h("section", { class: "cite-section" }, h("h3", {}, "References of this line"));
  if (!line.references.length) section.append(h("p", { class: "hint" }, "The line list names no reference for this line."));
  if (line.references.some((r) => r.uses.every((use) => use === "other data"))) {
    section.append(h("p", { class: "hint" }, "For some of these the file does not say which quantity they gave."));
  }
  for (const reference of line.references) {
    const entry = entryOf(reference.key);
    const key = h("b", { class: "cite-key" }, reference.key);      // as in the full list
    const node = entry ? citationNode(entry, format, key)
      : h("div", { class: "cite-entry" }, key, h("span", {}, "PySME has no bibliography entry for this one."));
    node.dataset.source = reference.key;
    section.append(node);
  }
  list.replaceChildren(table, section);

  // What Copy takes is what is shown.
  const entries = line.references.map((r) => entryOf(r.key)).filter(Boolean);
  ui.citeText = format === "bibtex"
    ? [`% ${line.line}`, ...entries.map((e) => e.bibtex)].join("\n\n") + "\n"
    : [line.line,
      ...line.quantities.map((q) => `${q.name}: ${q.value}${q.file !== null ? ` (edited; file: ${q.file})` : ""}${q.sources.length ? ` [${q.sources.join(", ")}]` : ""}`),
      "",
      ...line.references.map((r) => `${r.key}: ${entryOf(r.key)?.text || "no bibliography entry"}`),
    ].join("\n") + "\n";
}

/* From a line of the line list to its data and references. */
function showSources(id) {
  ui.lineScroll = $(".line-scroll").scrollTop;        // to come back to the same rows
  ui.cited = { id, list: S.linelist.version };
  ui.scrollToCited = true;
  const drawer = $("#drawer");
  drawer.classList.remove("collapsed");
  if (drawer.offsetHeight < 250) drawer.style.height = "250px";
  showDrawer("cite");
  resizePlot();
}

/* ------------------------------------------------------------------ dialogs */

function openDialog(title, body, buttons = [], onClose) {
  const dialog = $("#dialog");
  if (dialog.open) {
    const done = dialog._done;
    dialog._done = null;
    dialog.close();
    if (done) done();
  }
  dialog.replaceChildren(
    h("div", { class: "dialog-head" }, h("h2", {}, title), h("button", { class: "ghost", title: "Close", onclick: () => dialog.close() }, "✕")),
    h("div", { class: "dialog-body" }, body),
    buttons.length ? h("div", { class: "dialog-foot" }, buttons) : "");
  dialog._done = onClose || null;
  // The "close" event arrives late. When one dialog replaces another, the old
  // dialog's event must not answer the new one.
  dialog.onclose = () => {
    if (dialog.open) return;
    const done = dialog._done;
    dialog._done = null;
    if (done) done();
  };
  dialog.showModal();
  return dialog;
}

function showMessage(title, text) {
  openDialog(title, h("pre", {}, text), [h("button", { onclick: () => showReport() }, "Report a problem…"), h("button", { onclick: () => { $("#dialog").close(); showDrawer("log"); } }, "Open log"), h("button", { class: "secondary", onclick: () => $("#dialog").close() }, "Close")]);
}

function confirmDialog(title, text, yes) {
  return new Promise((resolve) => {
    let answer = false;
    openDialog(title, h("p", {}, text), [
      h("button", { onclick: () => $("#dialog").close() }, "Cancel"),
      h("button", { class: "secondary", onclick: () => { answer = true; $("#dialog").close(); } }, yes),
    ], () => resolve(answer));
  });
}

const nativeWaiting = new Map();
let nativeId = 0;
window.__nativeReply = (id, path) => {
  const resolve = nativeWaiting.get(id);
  nativeWaiting.delete(id);
  if (resolve) resolve(path);
};
const toNative = (message) => window.webkit.messageHandlers.native.postMessage(message);

/* Paths come from the computer that runs the program, which may be Windows. */
const isWindowsPath = (path) => /^([A-Za-z]:|\\\\)/.test(path);
const baseName = (path) => path.split(isWindowsPath(path) ? /[\\/]/ : "/").pop();
const isAbsolutePath = (path) => /^([\\/~]|[A-Za-z]:[\\/])/.test(path);
const joinPath = (folder, name) =>
  `${folder.replace(isWindowsPath(folder) ? /[\\/]+$/ : /\/+$/, "")}/${name}`;

/* Native panel inside the Mac app, a small built-in browser otherwise. */
function pickFile({ mode, title, extensions = [], name = "" }) {
  if (NATIVE) {
    return new Promise((resolve) => {
      const id = ++nativeId;
      nativeWaiting.set(id, resolve);
      toNative({ type: mode, id, title, extensions, name });
    });
  }
  return browseDialog({ mode, title, extensions, name });
}

function browseDialog({ mode, title, extensions, name }) {
  return new Promise((resolve) => {
    let result = null;
    let directory = localStorageGet("pysme-dir") || "~";
    const where = h("input", { type: "text", "aria-label": "Folder", style: "width:100%", onchange: () => list(where.value) });
    const files = h("ul", { class: "files" });
    const filename = h("input", { type: "text", value: name, placeholder: mode === "save" ? "file name" : "or type a full path", "aria-label": "File name" });
    const confirm = () => {
      const value = filename.value.trim();
      if (!value) return;
      result = isAbsolutePath(value) ? value : joinPath(directory, value);
      localStorageSet("pysme-dir", directory);
      $("#dialog").close();
    };
    filename.addEventListener("keydown", (e) => e.key === "Enter" && confirm());
    async function list(target) {
      let data;
      try {
        data = await request("fs?dir=" + encodeURIComponent(target));
      } catch (error) {
        toast(error.message, "error");
        return;
      }
      directory = data.dir;
      where.value = data.dir;
      const wanted = (entry) => entry.dir || !extensions.length || extensions.some((x) => entry.name.toLowerCase().endsWith("." + x));
      files.replaceChildren(
        h("li", {}, h("button", { onclick: () => list(data.parent) }, "📁 ..")),
        ...(data.drives || []).map((drive) => h("li", {}, h("button", { onclick: () => list(drive) }, `💽 ${drive}`))),
        ...data.entries.filter(wanted).map((entry) => h("li", {}, h("button", {
          onclick: (event) => {
            if (entry.dir) return list(joinPath(data.dir, entry.name));
            filename.value = entry.name;
            $$("button.selected", files).forEach((b) => b.classList.remove("selected"));
            event.currentTarget.classList.add("selected");
          },
          ondblclick: () => { if (!entry.dir) confirm(); },
        }, `${entry.dir ? "📁" : "📄"} ${entry.name}`))));
    }
    openDialog(title, h("div", {}, where, files), [
      filename,
      h("button", { onclick: () => $("#dialog").close() }, "Cancel"),
      h("button", { class: "secondary", onclick: confirm }, mode === "save" ? "Save" : "Open"),
    ], () => resolve(result));
    list(directory);
  });
}

function localStorageGet(key) {
  try { return localStorage.getItem(key); } catch (error) { return null; }
}
function localStorageSet(key, value) {
  try { localStorage.setItem(key, value); } catch (error) { /* private mode */ }
}

/* ------------------------------------------------------- settings & updates */

let settingsTimer = null;
let settingsScroll = 0;

const shortSmelib = (text) => /ref: (v[\w.]+)/.exec(text || "")?.[1] || text;

async function showSettings(info) {
  info = info || (await request("settings"));
  const latest = info.latest;
  const task = info.task;
  const busy = task.state === "running";
  const body = h("div", {});

  const run = async (path, payload) => {
    try {
      await showSettings(await request(path, payload));
      await refresh();
    } catch (error) {
      toast(error.message, "error");
      await showSettings();
    }
  };

  if (info.notice) body.append(h("div", { class: "card" }, info.notice));
  if (busy) body.append(h("div", { class: "card" }, spinner(), `${task.title}: ${task.step}. You can keep working.`));
  /* What a restart will apply is said in one box with the restart itself. */
  const restartResult = info.restart && task.state === "done" && task.restarts;
  if (task.state === "done" && !restartResult) body.append(h("div", { class: "card" }, task.message));
  if (task.state === "failed") body.append(h("p", { style: "color:var(--err);white-space:pre-wrap" }, `${task.title} failed. Nothing was changed.\n${task.error}`));
  if (info.restart) {
    body.append(h("div", { class: "card" }, restartResult ? h("div", {}, task.message) : "",
      h("b", {}, "Restart to apply the change. "),
      NATIVE ? h("button", { class: "secondary", onclick: restartApp }, "Restart now") : h("span", {}, "Quit PySME (⋯ → Quit PySME) and start it again.")));
  }

  /* the interface */
  const newGui = latest && latest.gui_newer;
  const gui = h("div", { class: "row" },
    h("button", { disabled: busy, onclick: (e) => { e.target.textContent = "Checking…"; run("update/check", {}); } }, "Check for updates"));
  if (info.managed) {
    gui.append(h("button", { class: "primary", disabled: busy || !newGui || Boolean(latest.gui_blocked), onclick: () => run("update/gui", {}) }, "Update interface"));
    if (info.previous_gui) {
      gui.append(h("button", { disabled: busy, onclick: () => run("update/rollback", {}) }, `Go back to ${info.previous_gui}`));
    }
  }
  gui.append(h("button", { onclick: () => showReport() }, "Report a problem…"));
  body.append(
    h("h3", {}, "Interface"),
    h("p", {}, `PySME-GUI ${info.gui}`,
      newGui ? h("b", {}, `  → ${latest.gui} available`)
        : info.gui_selected !== info.gui ? h("b", {}, `  → ${info.gui_selected} after restart`)
          : latest && latest.gui ? "  (up to date)" : ""),
    newGui && latest.gui === info.left_gui ? h("p", { class: "hint" }, `You went back from ${info.left_gui}.`) : "",
    gui,
    channelRow(info, busy, run),
    latest && latest.gui_note ? h("p", { class: "hint" }, latest.gui_note) : "",
    newGui && latest.gui_blocked ? h("p", { style: "color:var(--err)" }, latest.gui_blocked) : "",
    latest && latest.errors.length ? h("p", { class: "hint" }, "Could not check: " + latest.errors.join("; ")) : "",
    latest ? h("p", { class: "hint" }, `Last checked ${latest.checked}. Source: ${info.source}`) : "");

  /* PySME */
  body.append(h("h3", {}, "PySME"));
  if (info.managed) {
    body.append(h("ul", { class: "versions" }, info.environments.map((env) => versionRow(env, info, busy, run))));
    body.append(installRow(info, busy, run));
  } else {
    body.append(h("p", {}, `PySME ${info.pysme}, SMElib ${shortSmelib(info.smelib)}`),
      h("p", { class: "hint" }, info.custom_python
        ? "This session uses your own PySME (see Advanced below), so versions are not managed here."
        : "Versions can be managed when PySME-GUI runs in its own installed environment. This session uses another Python."));
  }

  body.append(
    advancedSettings(info),
    h("h3", {}, "Details"),
    h("div", { class: "kv" },
      h("span", {}, "Running"), h("span", {}, `PySME ${info.pysme}, SMElib ${shortSmelib(info.smelib)}`),
      h("span", {}, "Python"), h("span", {}, `${info.python_version} — ${info.python}`),
      h("span", {}, "App data"), h("span", {}, info.home),
      h("span", {}, "Model data"), h("span", {}, "~/.sme (shared by all PySME versions)")));

  if ((task.state === "done" || task.state === "failed") && !task.seen) {
    request("update/seen", { id: task.id }).then(refresh).catch(() => {});
  }

  const previous = $("#dialog .dialog-body");
  if (previous && $("#dialog .settings-body")) settingsScroll = previous.scrollTop;
  body.className = "settings-body";
  openDialog("Updates & settings", body, [h("button", { onclick: () => $("#dialog").close() }, "Close")],
    () => { clearTimeout(settingsTimer); });
  $("#dialog .dialog-body").scrollTop = settingsScroll;
  clearTimeout(settingsTimer);
  if (busy) {
    settingsTimer = setTimeout(async () => {
      await showSettings();
      await refresh();
    }, 1500);
  }
}

/* Which releases of the interface are offered. Alpha builds are for internal
   testing: the choice appears only for someone who already receives them. */
function channelRow(info, busy, run) {
  const names = { stable: "release only", beta: "release + beta", alpha: "release + beta + alpha (internal testing)" };
  const offered = info.channel === "alpha" ? ["stable", "beta", "alpha"] : ["stable", "beta"];
  const select = h("select", { "aria-label": "Interface updates to receive", disabled: busy, onchange: () => run("update/channel", { channel: select.value }) },
    offered.map((name) => h("option", { value: name }, names[name])));
  select.value = info.channel;
  return h("label", { class: "row" }, "Receive ", select);
}

/* A text to be pasted into a terminal, with a button that copies it. */
const copyable = (text) => h("div", { class: "command" },
  h("code", { class: "selectable" }, text),
  h("button", { class: "link", onclick: () => copyText(text) }, "Copy"));

/* The other direction: the app's own PySME, used outside the app. */
function appPython(info) {
  const selected = info.managed && info.environments.find((e) => e.selected);
  if (!selected) return "";
  return h("div", {},
    h("h3", {}, "Use the app's PySME in your own scripts"),
    h("p", {}, "Run a script with this Python to get the same PySME and SMElib as the interface."),
    copyable(selected.python),
    h("p", { class: "hint" }, "Put the path in quotes in a terminal: it contains spaces. It changes when you switch the PySME version. This Python belongs to the app, so do not install or update packages in it."));
}

/* For the few who need either: folded away, and open when an own PySME is in use. */
function advancedSettings(info) {
  const command = 'python -c "import sys; print(sys.executable)"';
  const python = h("input", { type: "text", value: ui.ownDraft ?? info.custom_python ?? "", placeholder: "empty: the app's own PySME", "aria-label": "Path of your Python", style: "flex:1" });
  python.spellcheck = false;
  python.addEventListener("input", () => { ui.ownDraft = python.value; });

  const save = async (value, acceptUnsupported = false) => {
    let result;
    try {
      result = await request("settings/python", { python: value, accept_unsupported: acceptUnsupported });
    } catch (error) {
      toast(error.message, "error");
      await showSettings();
      return;
    }
    const checked = result.checked;
    if (checked && !checked.saved) {
      const yes = await confirmDialog(`PySME ${checked.pysme} is outside the supported range`,
        `This interface is made for PySME ${checked.range}. It started with yours, but some functions may fail or behave differently. Use it anyway?`, "Use it anyway");
      if (yes) await save(value, true);
      else await showSettings();
      return;
    }
    ui.ownDraft = null;
    toast(checked ? `Checked: the interface starts with PySME ${checked.pysme}. It is used after a restart.` : "The app's own PySME is used after a restart.");
    await showSettings(result);
    await refresh();      // the dot beside the menu follows what was saved
  };

  const button = h("button", {
    onclick: (e) => { e.target.textContent = "Checking…"; e.target.disabled = true; save(python.value); },
  }, "Check and save");
  python.addEventListener("keydown", (e) => { if (e.key === "Enter") button.click(); });

  const section = h("details", { class: "advanced", ontoggle: () => { ui.advancedOpen = section.open; } },
    h("summary", {}, "Advanced"),
    appPython(info),
    h("h3", {}, "Use a PySME you installed yourself"),
    h("p", {}, h("b", {}, "Usually leave this empty."), " The app has its own PySME, managed above. Fill this in only to run the interface with an installation of yours, for example a development build or one you compiled."),
    h("p", { class: "hint" }, "To find the path: in a terminal, activate the environment in which you use PySME, then run"),
    copyable(command),
    h("p", { class: "hint" }, "Paste the whole line it prints: the Python program itself, not the environment folder. That environment needs PySME only; the interface comes with the app."),
    h("div", { class: "row" }, python, button),
    info.custom_python
      ? h("div", {},
        h("p", {}, `Chosen: PySME ${info.custom_pysme || "(version not recorded)"} from `, h("span", { class: "selectable" }, info.custom_python)),
        info.custom_supported === false ? h("p", { style: "color:var(--err)" }, `Outside the supported range (${info.supported}).`) : "",
        h("button", { onclick: () => { ui.ownDraft = null; save(""); } }, "Go back to the app's own PySME"))
      : "");
  section.open = ui.advancedOpen ?? Boolean(info.custom_python);
  return section;
}

function versionRow(env, info, busy, run) {
  const tags = [];
  if (env.running) tags.push(h("span", { class: "tag on" }, "in use"));
  if (env.selected && !env.running) tags.push(h("span", { class: "tag" }, "after restart"));
  if (env.pysme_version === info.recommended) tags.push(h("span", { class: "tag" }, "recommended"));
  if (!env.supported) tags.push(h("span", { class: "tag warn" }, "not supported by this interface"));
  const actions = h("span", { class: "actions" });
  if (!env.selected && env.supported) actions.append(h("button", { disabled: busy, onclick: () => run("pysme/use", { id: env.id }) }, "Use"));
  actions.append(h("button", {
    title: "Save the exact package versions, to recreate this installation on another computer",
    onclick: async () => {
      const path = await pickFile({ mode: "save", title: "Save package list", extensions: ["txt"], name: `pysme-${env.pysme_version}-packages.txt` });
      if (path) {
        try {
          await request("pysme/export", { id: env.id, path });
          toast("Package list saved.");
        } catch (error) {
          toast(error.message, "error");
        }
      }
      await showSettings();
    },
  }, "Package list…"));
  if (!env.selected && !env.running) {
    actions.append(h("button", {
      disabled: busy,
      onclick: async () => {
        const yes = await confirmDialog(`Remove PySME ${env.pysme_version}?`,
          `Scripts that use its Python will stop working. You can install PySME ${env.pysme_version} again later, but the other packages it uses may then be newer. To keep a record of the exact versions, save the package list first. The app cannot install from that list; use it to set up your own Python.`, "Remove");
        if (yes) await run("pysme/remove", { id: env.id });
        else await showSettings();
      },
    }, "Remove"));
  }
  return h("li", {},
    h("span", {}, h("b", {}, env.pysme_version), " ", tags,
      h("small", {}, `SMElib ${shortSmelib(env.smelib_version)} · installed ${env.created.slice(0, 10)}`)),
    actions);
}

function installRow(info, busy, run) {
  const releases = info.latest?.releases;
  if (!releases) {
    return h("p", { class: "hint" }, "Press “Check for updates” to list the versions you can install.");
  }
  const installed = new Set(info.environments.map((e) => e.pysme_version));
  /* The recommended one is always offered, also while it is a pre-release. */
  const choices = releases.filter((r) => !installed.has(r.version) &&
    (ui.prereleases || !r.prerelease || r.version === info.recommended));
  const select = h("select", { "aria-label": "Version to install", disabled: busy || !choices.length, onchange: () => { ui.installChoice = select.value; } },
    choices.map((r) => h("option", { value: r.version }, `${r.version}  (${r.date})${r.version === info.recommended ? " — recommended" : ""}${r.prerelease ? " — pre-release" : ""}`)));
  if (choices.some((r) => r.version === ui.installChoice)) select.value = ui.installChoice;
  const early = h("input", { type: "checkbox", onchange: () => { ui.prereleases = early.checked; showSettings(info); } });
  early.checked = Boolean(ui.prereleases);
  const newer = info.latest.pysme_new;
  return h("div", {},
    h("div", { class: "row" },
      choices.length ? select : h("span", { class: "hint" }, "No other version to install."),
      h("button", { disabled: busy || !choices.length, onclick: () => run("pysme/install", { version: select.value }) }, "Install"),
      h("label", { class: "check" }, early, " pre-releases")),
    newer ? h("p", {}, h("b", {}, `PySME ${newer} is new.`), " Installing it keeps the versions you have.") : "",
    h("p", { class: "hint" }, `This interface works with PySME ${info.minimum} or newer. Installing does not switch to the new version.`));
}

async function restartApp() {
  if (S.dirty && !(await confirmDialog("Restart with unsaved changes?", "Changes that were not saved will be lost.", "Restart"))) return;
  toNative({ type: "restart" });
}

/* What the label beside the name means. */
function showStage() {
  const { stage, version } = S.release;
  const what = stage === "alpha"
    ? `an alpha version, for internal testing: things may change or not work.`
    : `a beta version, still being tested.`;
  const dialog = openDialog(`${stage[0].toUpperCase()}${stage.slice(1)} version`,
    [h("p", {}, `This interface (${version}) is ${what}`),
      h("p", {}, "Check results against your own PySME scripts before you rely on them, and report what goes wrong.")],
    [h("button", { type: "button", onclick: () => showReport() }, "Report a problem…"),
      h("button", { type: "button", class: "primary", onclick: () => dialog.close() }, "Close")]);
}

/* ---------------------------------------------------------- problem reports */

/* Three steps: describe, check what will be shared, then save and mail it. */
function showReport(draft = { description: "", settings: false }) {
  clearTimeout(settingsTimer);
  const description = h("textarea", { rows: 6, "aria-label": "Description", placeholder: "What were you doing? What did you expect, and what happened instead? (any language)" });
  description.value = draft.description;
  const include = h("input", { type: "checkbox" });
  include.checked = draft.settings;
  const next = h("button", {
    class: "secondary",
    onclick: async () => {
      next.disabled = true;
      draft = { description: description.value, settings: include.checked };
      try {
        const { text } = await request("report/preview", { ...draft, client: { agent: navigator.userAgent, errors: recentErrors } });
        reviewReport(text, draft);
      } catch (error) {
        toast(error.message, "error");
        next.disabled = false;
      }
    },
  }, "Create report");
  openDialog("Report a problem", h("div", { class: "report" },
    h("p", {}, "This makes one text file with the program versions, your system and the recent log, which you can attach to a GitHub issue or pass on yourself. Nothing is sent automatically."),
    description,
    h("label", { class: "check" }, include, " Also include my current settings (stellar parameters, wavelength ranges, fit values)"),
    h("p", { class: "hint" }, "Spectra and line lists are never included. You can read and edit the report in the next step.")),
    [h("button", { onclick: () => $("#dialog").close() }, "Cancel"), next]);
}

function reviewReport(text, draft) {
  const preview = h("textarea", { class: "report-text", rows: 18, "aria-label": "Report contents" });
  preview.spellcheck = false;
  preview.value = text;
  openDialog("Check the report", h("div", { class: "report" },
    h("p", {}, "This is everything that will be in the file. Delete anything you do not want to share, such as file or star names."),
    preview),
    [h("button", { onclick: () => showReport(draft) }, "Back"), h("button", {
      class: "secondary",
      onclick: async () => {
        try {
          sendReport(await request("report/save", { text: preview.value }));
        } catch (error) {
          toast(error.message, "error");
        }
      },
    }, "Save report")]);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Copied.");
  } catch (error) {
    toast("Copying is not allowed here. Select the text and copy it.", "error");
  }
}

/* A report goes into a GitHub issue, or is passed on by hand. The program holds
   no e-mail address and sends nothing itself. */
function sendReport(saved) {
  const openIssue = () => {
    const link = h("a", { href: saved.issue_url, rel: "noopener" });
    // The Mac app hands web links to the system browser; elsewhere use a new tab.
    if (!NATIVE) link.target = "_blank";
    document.body.append(link);
    link.click();
    link.remove();
    request("report/reveal", {}).catch(() => {});
  };
  const row = (label, value) => [h("span", {}, label), h("span", {}, h("span", { class: "selectable" }, value), " ", h("button", { class: "link", onclick: () => copyText(value) }, "Copy"))];
  openDialog("Send the report", h("div", { class: "report" },
    h("p", {}, h("b", {}, "The report is saved. It has not been sent yet."), " Choose either way:"),
    h("div", { class: "kv" }, row("File", saved.path)),
    h("div", { class: "send-ways" },
      h("div", { class: "send-way" },
        h("h3", {}, "GitHub issue"),
        h("p", {}, "Needs a GitHub account and access to github.com. A new issue opens in your browser: drag the file into it."),
        h("p", { class: "hint" }, "Issues are public: anyone can read the attached file."),
        h("button", { class: "secondary", onclick: openIssue }, "Open GitHub issue")),
      h("div", { class: "send-way" },
        h("h3", {}, "Without GitHub"),
        h("p", {}, "Give the file to the person who gave you the program, in whatever way you usually reach them."),
        h("button", { class: "secondary", onclick: () => request("report/reveal", {}).catch((e) => toast(e.message, "error")) }, "Show file")))),
    [h("button", { onclick: () => $("#dialog").close() }, "Done")]);
}

/* ----------------------------------------------------------------- commands */

async function mayDiscard() {
  if (!S || !S.dirty) return true;
  return confirmDialog("Discard unsaved changes?", "The current setup has changes that are not saved.", "Discard");
}

/* Finish the field being edited so its value is sent before the next action. */
function commitEdits() {
  if (document.activeElement && document.activeElement.matches("input, select, textarea")) document.activeElement.blur();
}

const commands = {
  async new() { if (await mayDiscard()) { resetView(); send("new"); } },
  async open(path) {
    if (jobRunning()) return;
    path = path || (await pickFile({ mode: "open", title: "Open", extensions: ["sme", "inp", "sav", "out", "lin", "txt", "csv", "dat", "asc", "spec"] }));
    if (!path) return;
    const replaces = /\.(sme|inp|sav|out)$/i.test(path);
    if (replaces && !(await mayDiscard())) return;
    if (replaces) resetView();
    send("open", { path });
  },
  async import() {
    const path = await pickFile({ mode: "open", title: "Import observed spectrum", extensions: ["txt", "csv", "dat", "asc", "spec", "tsv"] });
    if (path) send("open", { path });
  },
  async linelist() {
    const path = await pickFile({ mode: "open", title: "Load VALD line list", extensions: ["lin", "txt", "dat"] });
    if (path) { ui.linePage = 0; send("linelist/load", { path }); }
  },
  async save() {
    commitEdits();
    await queue;
    if (!S.path) return commands.saveAs();
    if (await send("save", { path: S.path })) toast("Saved.");
  },
  async saveAs() {
    commitEdits();
    const path = await pickFile({ mode: "save", title: "Save as", extensions: ["sme"], name: S.path ? baseName(S.path) : "spectrum.sme" });
    if (path && (await send("save", { path }))) toast("Saved.");
  },
  async exportCsv() {
    const path = await pickFile({ mode: "save", title: "Export spectrum", extensions: ["csv"], name: "spectrum.csv" });
    if (path && (await send("export/csv", { path }))) toast("CSV written.");
  },
  async exportPng() {
    const pattern = ui.view === "pattern";
    const graph = $(pattern ? "#pattern" : "#plot");
    if (!pattern && !spectrum.segments.length) return toast("There is no plot yet.", "error");
    // Figures for papers and slides are exported in light colours.
    const theme = currentTheme();
    const recolour = darkNow();
    let url;
    try {
      if (recolour) {
        setThemeAttribute("light");
        if (pattern) await renderPattern();
        else {
          await renderPlot();
          await updateLineLabels();
        }
      }
      // On screen the legend is the line above the plot; a figure needs its own.
      const figure = {
        data: graph.data,
        layout: pattern ? graph.layout : { ...graph.layout, showlegend: true, margin: { ...graph.layout.margin, t: 30 } },
      };
      url = await Plotly.toImage(figure, { format: "png", width: graph.clientWidth, height: graph.clientHeight, scale: 2 });
    } finally {
      if (recolour) {
        setThemeAttribute(theme);
        renderPlot();
        renderPattern();
      }
    }
    const file = pattern ? "abundances.png" : "spectrum.png";
    if (NATIVE) toNative({ type: "savePng", data: url.split(",")[1], name: file });
    else h("a", { href: url, download: file }).click();
  },
  async example() { if (await mayDiscard()) { resetView(); send("example"); } },
  synth() { commitEdits(); send("run", { kind: "synth" }); },
  fit() { commitEdits(); send("run", { kind: "fit" }); },
  stop() { send("cancel"); },
  async quit() {
    commitEdits();
    await queue;
    if (S.dirty && !(await confirmDialog("Quit without saving?", "The current setup has changes that are not saved.", "Quit"))) return;
    if (!(await send("quit"))?.quitting) return;
    ui.quit = true;
    clearTimeout(pollTimer);
    document.title = "PySME has quit";
    document.body.replaceChildren(h("div", { class: "quit-page" },
      h("h2", {}, "PySME has quit"), h("p", {}, "You can close this tab.")));
  },
  settings() { showSettings().catch((e) => toast(e.message, "error")); },
  report() { showReport(); },
  themeAuto() { applyTheme("auto"); },
  themeLight() { applyTheme("light"); },
  themeDark() { applyTheme("dark"); },
};

function resetView() {
  ui.compare = null;
  compareData = null;
  ui.linePage = 0;
  ui.gridChoice = {};
  const graph = $("#plot");
  if (graph._pysmeReady) {
    Plotly.purge(graph);
    graph._pysmeReady = false;
    currentLines = [];
  }
}

/* Entry points used by the Mac app's menu bar. */
window.guiCommand = (name) => {
  if (!S || !commands[name]) return;
  if (jobRunning() && name !== "stop" && name !== "settings") return;
  commands[name]();
};
window.openFromFinder = (path) => commands.open(path);
window.hasUnsavedChanges = () => Boolean(S && S.dirty);

/* ------------------------------------------------------------------- wiring */

/* The choice is stored by the server: the Mac app serves the page from a new
   port on every start, so the browser's own storage would be forgotten. */
const THEME_LABELS = { auto: "◐ Auto", light: "☀ Light", dark: "☾ Dark" };
const currentTheme = () => document.documentElement.dataset.theme || "auto";

function setThemeAttribute(theme) {
  if (theme === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
}

function applyTheme(theme, remember = true) {
  setThemeAttribute(theme);
  $("#btn-theme").textContent = THEME_LABELS[theme];
  for (const choice of $$("[data-theme-choice]")) {
    choice.setAttribute("role", "menuitemradio");
    choice.setAttribute("aria-checked", String(choice.dataset.themeChoice === theme));
  }
  // The window frame, file panels and alerts of the Mac app follow along.
  if (NATIVE) toNative({ type: "theme", value: theme });
  if (remember) request("settings/theme", { theme }).catch((error) => toast(error.message, "error"));
  if (S) {
    renderPlot();
    renderPattern();
  }
}

const darkNow = () => currentTheme() === "dark" || (currentTheme() === "auto" && matchMedia("(prefers-color-scheme: dark)").matches);

function wire() {
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-cmd]");
    if (button && !button.disabled) {
      commitEdits();
      commands[button.dataset.cmd]();
    }
    for (const [button, items] of [["#btn-more", "#menu-more"], ["#btn-theme", "#menu-theme"], ["#btn-remove-what", "#menu-remove"]]) {
      const menu = $(items);
      menu.hidden = event.target.closest(button) ? !menu.hidden : true;
    }
  });

  for (const input of $$("[data-param]")) {
    input.addEventListener("input", () => { input.dataset.editing = "1"; });
    input.addEventListener("change", () => {
      delete input.dataset.editing;
      const name = input.dataset.param;
      const value = input.type === "checkbox" ? input.checked : input.value;
      if (value === "") {
        renderForms(true);
        return;
      }
      sendFrom(input, "params", { values: { [name]: value } }, () => writeParam(input));
    });
    if (input.type === "number") input.addEventListener("keydown", (e) => e.key === "Enter" && input.blur());
    if (input.tagName === "TEXTAREA") {
      input.addEventListener("keydown", (e) => {
        if (e.key !== "Enter") return;
        e.preventDefault();            // Enter finishes the entry; it adds no line
        input.blur();
      });
    }
  }
  for (const button of $$("[data-help]")) {
    button.addEventListener("click", () => {
      const box = $("#" + button.dataset.help);
      box.hidden = !box.hidden;
      button.setAttribute("aria-expanded", String(!box.hidden));
    });
  }
  $("#mu-default").addEventListener("click", () => send("params", { values: { nmu: S.params.nmu } }));
  // Scrolling the sidebar must never change a number by accident.
  document.addEventListener("wheel", () => {
    if (document.activeElement && document.activeElement.type === "number") document.activeElement.blur();
  }, { passive: true });

  for (const button of $$("#flux-scale button")) {
    button.addEventListener("click", () => {
      ui.flux = button.dataset.normalised === "false";
      renderFluxScale();
      if (S) renderPlot();
    });
  }
  for (const button of $$("#side-tabs button")) {
    button.addEventListener("click", () => {
      for (const other of $$("#side-tabs button")) other.classList.toggle("active", other === button);
      for (const panel of $$(".side-body [data-panel]")) panel.hidden = panel.dataset.panel !== button.dataset.tab;
      if (S) renderReadiness();
    });
  }
  $("#stage-badge").addEventListener("click", showStage);
  $("#cite-all").addEventListener("click", () => {
    ui.cited = null;
    ui.scrollToCited = true;
    renderCitations();
  });
  $("#cite-back").addEventListener("click", () => {
    ui.cited = null;
    showDrawer("lines");
  });
  for (const button of $$("#drawer-tabs [data-tab]")) {
    button.addEventListener("click", () => {
      if (button.dataset.tab === "cite") ui.cited = null;     // opened by hand
      $("#drawer").classList.remove("collapsed");
      showDrawer(button.dataset.tab);
      resizePlot();
    });
  }
  $("#drawer-toggle").addEventListener("click", () => {
    $("#drawer").classList.toggle("collapsed");
    resizePlot();
  });
  document.addEventListener("click", (event) => {
    const link = event.target.closest("[data-goto]");
    if (link) {
      event.preventDefault();
      $("#drawer").classList.remove("collapsed");
      showDrawer(link.dataset.goto);
      resizePlot();
    }
  });

  const splitter = $("#splitter");
  splitter.addEventListener("pointerdown", (start) => {
    const drawer = $("#drawer");
    const height = drawer.offsetHeight;
    splitter.setPointerCapture(start.pointerId);
    drawer.classList.remove("collapsed");
    const move = (event) => {
      const limit = $("#work").clientHeight - 230;
      drawer.style.height = Math.max(36, Math.min(limit, height + start.clientY - event.clientY)) + "px";
      resizePlot();
    };
    splitter.addEventListener("pointermove", move);
    splitter.addEventListener("pointerup", () => splitter.removeEventListener("pointermove", move), { once: true });
  });

  for (const button of $$("#view-tabs [data-view]")) {
    button.addEventListener("click", () => setView(button.dataset.view));
  }
  for (const button of $$(".abund-scale [data-scale]")) {
    button.addEventListener("click", () => {
      ui.abundScale = button.dataset.scale;
      renderScaleButtons();
      if (!S) return;
      if (ui.drawer === "abund") renderAbundances();
      renderPattern();
    });
  }
  renderScaleButtons();
  $("#solar-reference").addEventListener("change", (event) => send("abund/reference", { name: event.target.value }));

  // With no mask tool on, dragging zooms. A second click switches a tool off.
  for (const button of $$("#tools [data-tool]")) {
    button.addEventListener("click", () => setTool(ui.tool === button.dataset.tool ? "zoom" : button.dataset.tool));
  }
  for (const item of $$("#menu-remove [data-remove]")) {
    item.addEventListener("click", () => {
      ui.removeWhat = Number(item.dataset.remove);
      renderMaskTools();
      setTool("0");                  // choosing what to remove starts removing
    });
  }
  for (const button of $$("#vrad-mode [data-vrad]")) {
    button.addEventListener("click", () => {
      ui.velocityMode = button.dataset.vrad;
      send("params", { values: { vrad_flag: ui.velocityMode } });
    });
  }
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && ui.tool !== "zoom" && !$("#dialog").open) setTool("zoom");
  });
  $("#show-resid").addEventListener("change", renderPlot);
  $("#show-lines").addEventListener("change", updateLineLabels);
  $("#segment").addEventListener("change", (event) => {
    if (event.target.value === "all") Plotly.relayout($("#plot"), { "xaxis.autorange": true, "yaxis.autorange": true });
    else {
      const seg = spectrum.segments[Number(event.target.value)];
      zoomTo(seg.wave[0], seg.wave[seg.wave.length - 1]);
    }
  });
  $("#compare").addEventListener("change", (event) => setCompare(event.target.value === "" ? null : Number(event.target.value)));

  $("#btn-apply-pattern").addEventListener("click", async () => {
    const name = $("#abund-pattern").value;
    if (await confirmDialog(`Apply ${name}?`, "This replaces every element abundance, including your edits. [M/H] is kept.", "Apply")) {
      send("abund/pattern", { name });
    }
  });
  $("#btn-add-abund").addEventListener("click", () => toggleFit(`abund ${$("#fit-element").value}`, true));

  let searchTimer = null;
  $("#line-search").addEventListener("input", (event) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      ui.lineQuery = event.target.value;
      ui.linePage = 0;
      renderLines();
    }, 250);
  });
  $("#line-prev").addEventListener("click", () => { ui.linePage -= 1; renderLines(); });
  $("#line-next").addEventListener("click", () => { ui.linePage += 1; renderLines(); });
  for (const input of $$("[data-line]")) {
    input.addEventListener("input", () => { input.dataset.editing = "1"; });
    input.addEventListener("change", () => {
      delete input.dataset.editing;
      if (!ui.line) return;
      if (input.value === "") {
        writeLineInput(input);
        return;
      }
      sendFrom(input, "linelist/edit", { id: ui.line.id, column: input.dataset.line, value: input.value },
        () => ui.line && writeLineInput(input));
    });
    input.addEventListener("keydown", (e) => e.key === "Enter" && input.blur());
  }
  $("#line-reset").addEventListener("click", async () => {
    await send("linelist/reset", { id: ui.line.id });
    renderLineEditor(true);
  });
  $("#line-reset-all").addEventListener("click", async () => {
    if (await confirmDialog("Restore all edited lines?", "Every line gets the values it was loaded with.", "Restore")) {
      await send("linelist/reset", {});
      renderLineEditor(true);
    }
  });
  $("#line-close").addEventListener("click", () => chooseLine(null));

  $("#cite-format").addEventListener("change", renderCitations);
  $("#cite-copy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(ui.citeText || "");
      toast("Copied.");
    } catch (error) {
      toast("Copying is not allowed here. Select the text and copy it.", "error");
    }
  });
  $("#cite-save").addEventListener("click", async () => {
    const format = $("#cite-format").value;
    const path = await pickFile({ mode: "save", title: "Save references", extensions: [format === "bibtex" ? "bib" : "txt"], name: format === "bibtex" ? "pysme.bib" : "pysme-references.txt" });
    if (path && (await send("export/citation", { path, format }))) toast("References written.");
  });

  for (const choice of $$("[data-theme-choice]")) {
    choice.addEventListener("click", () => applyTheme(choice.dataset.themeChoice));
  }
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (!S) return;
    renderPlot();
    renderPattern();
  });

  if (!NATIVE) {
    document.addEventListener("keydown", (event) => {
      if (!(event.metaKey || event.ctrlKey)) return;
      const key = event.key.toLowerCase();
      const name = key === "s" ? (event.shiftKey ? "saveAs" : "save") : key === "o" ? "open" : key === "enter" ? (event.shiftKey ? "fit" : "synth") : key === "." ? "stop" : null;
      if (name) {
        event.preventDefault();
        window.guiCommand(name);
      }
    });
    window.addEventListener("beforeunload", (event) => {
      if (!ui.quit && window.hasUnsavedChanges()) event.preventDefault();
    });
  }
  new ResizeObserver(resizePlot).observe($(".plot-area"));
  wireCrosshair();
}

/* At most one redraw per frame, following the size while it changes.
   (Plotly.Plots.resize waits until the size stops changing, so the plot only
   caught up after the splitter had been released.) */
let resizeFrame = null;
function resizePlot() {
  if (resizeFrame !== null) return;
  resizeFrame = requestAnimationFrame(async () => {
    const graph = $("#plot");
    const fits = () => !graph._pysmeReady || !graph.clientWidth ||
      (graph._fullLayout.width === graph.clientWidth && graph._fullLayout.height === graph.clientHeight);
    let asked = null;
    const pattern = $("#pattern");
    if (!pattern.hidden && pattern._pysmeReady && (pattern._fullLayout.width !== pattern.clientWidth || pattern._fullLayout.height !== pattern.clientHeight)) {
      Plotly.relayout(pattern, { width: pattern.clientWidth, height: pattern.clientHeight });
    }
    try {
      if (!fits()) {
        asked = `${graph.clientWidth}x${graph.clientHeight}`;
        await Plotly.relayout(graph, { width: graph.clientWidth, height: graph.clientHeight });
        scheduleLineLabels();
      }
    } finally {
      resizeFrame = null;
      // The box may have changed again during the redraw. Comparing with what was
      // asked for, not with what Plotly drew, rules out an endless redraw.
      if (asked && asked !== `${graph.clientWidth}x${graph.clientHeight}`) resizePlot();
    }
  });
}

/* Plotly redraws its hover at most every 50 ms, so its line and value box trail
   the mouse. Both are plain elements here, moved with every pointer event. */

/* The value of every shown curve at the data point nearest to a wavelength. */
function valuesAt(graph, wave) {
  const rows = [];
  for (const trace of graph.data || []) {
    // "meta" marks the curves to report; the error band has none.
    if (!trace.meta || trace.visible === false || trace.visible === "legendonly") continue;
    const x = trace.x;
    let low = 0, high = x.length - 1;
    if (high < 0 || wave < x[low] || wave > x[high]) continue;     // another segment
    while (high - low > 1) {
      const middle = (low + high) >> 1;
      if (x[middle] < wave) low = middle;
      else high = middle;
    }
    const i = wave - x[low] <= x[high] - wave ? low : high;
    if (trace.y[i] === null || trace.y[i] === undefined) continue;
    rows.push({ name: trace.meta, colour: trace.line.color, wave: x[i], value: trace.y[i] });
  }
  return rows;
}

/* One line above the plot is both its legend and, under the pointer, the values.
   Its places do not move as the values come and go. A click on a name hides or
   shows that curve. */
function renderLegend() {
  $("#readout").replaceChildren(
    h("div", { class: "wave" }, h("span", { class: "name" }, "λ"), h("span", { class: "value" })),
    ...plotCurves.map((curve) => {
      const dash = h("i", {});
      dash.style.background = curve.dashed
        ? `repeating-linear-gradient(90deg, ${curve.colour} 0 3px, transparent 3px 6px)` : curve.colour;
      const off = ui.hiddenCurves.has(curve.meta);
      return h("button", {
        type: "button", "data-curve": curve.meta, class: off ? "off" : null, "aria-pressed": String(!off),
        title: `${curve.title ? curve.title + ". " : ""}Click to ${off ? "show" : "hide"} this curve`,
        onclick: () => {
          if (off) ui.hiddenCurves.delete(curve.meta);
          else ui.hiddenCurves.add(curve.meta);
          renderPlot();
        },
      }, dash, h("span", { class: "name" }, curve.name), h("span", { class: "value" }));
    }));
}

function hideReadout() {
  $("#crosshair").hidden = true;
  for (const value of $$("#readout .value")) value.textContent = "";
}

/* Unnormalised fluxes are in the millions. */
const readoutValue = (value) => (Math.abs(value) >= 1e4 ? value.toExponential(4) : value.toFixed(4));

function showReadout(rows) {
  const wave = $("#readout .wave .value");
  if (!wave) return;
  wave.textContent = rows.length ? `${rows[0].wave.toFixed(4)} Å` : "";
  for (const item of $$("#readout [data-curve]")) {
    const row = rows.find((r) => r.name === item.dataset.curve);
    item.lastChild.textContent = row ? readoutValue(row.value) : "";
  }
}

function wireCrosshair() {
  const area = $(".plot-area");
  const line = $("#crosshair");
  area.addEventListener("pointermove", (event) => {
    const graph = $("#plot");
    const size = graph._fullLayout && graph._fullLayout._size;
    if (ui.view !== "spectrum" || !size || graph.style.visibility === "hidden" || event.buttons) return hideReadout();
    const frame = graph.getBoundingClientRect();
    const x = event.clientX - frame.left;
    const y = event.clientY - frame.top;
    if (x < size.l || x > size.l + size.w || y < size.t || y > size.t + size.h) return hideReadout();
    line.style.top = `${graph.offsetTop + size.t}px`;
    line.style.height = `${size.h}px`;
    line.style.transform = `translateX(${graph.offsetLeft + x}px)`;
    line.hidden = false;

    const [first, last] = graph._fullLayout.xaxis.range.map(Number);
    const rows = valuesAt(graph, first + ((x - size.l) / size.w) * (last - first));
    showReadout(rows);
  });
  area.addEventListener("pointerleave", hideReadout);
  area.addEventListener("pointerdown", hideReadout);   // zoom and selection draw their own box
  area.addEventListener("wheel", hideReadout, { passive: true });
}

async function start() {
  // The server already wrote the stored choice into the page.
  applyTheme(currentTheme(), false);
  wire();
  await refresh();
  try {
    options = await request("options");
  } catch (error) {
    toast(error.message, "error");
    return;
  }
  for (const error of options.errors || []) toast(error, "error");
  $("#atmo-source").replaceChildren(...options.atmospheres.map((a) => h("option", { value: a }, a)));
  $("#abund-pattern").replaceChildren(...options.patterns.map((p) => h("option", { value: p }, p)));
  $("#solar-reference").replaceChildren(...options.patterns.map((p) => h("option", { value: p }, p)));
  $("#solar-reference").value = S.solar_reference;
  if (options.patterns.includes("asplund2021")) $("#abund-pattern").value = "asplund2021";
  const { elements } = await request("abund");
  $("#fit-element").replaceChildren(...elements.filter((e) => e.z > 2).map((e) => h("option", { value: e.el }, e.el)));
  $("#fit-element").value = "Mg";
  renderForms(true);
  if (!S) return;               // the message has been shown
  renderDrawer();        // the periodic table needs the list of NLTE grids
  const { notice } = await request("settings");
  if (notice) toast(notice, "error");
  // Quietly look for a newer version; only sets the dot on the settings button.
  request("update/check", {}).then(refresh).catch(() => {});
}

start();
