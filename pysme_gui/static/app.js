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

const TOKEN = new URLSearchParams(location.search).get("token") || "";
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
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

function toast(message, kind = "info") {
  const close = h("button", { title: "Dismiss", onclick: () => node.remove() }, "✕");
  const node = h("div", { class: "toast " + kind }, h("div", {}, message), close);
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
const ui = { tool: "zoom", drawer: "results", linePage: 0, lineQuery: "", element: "Fe", compare: null };

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
  }
  renderTop();
  renderJob();
  renderForms(changed);
  renderReadiness();
  if (changed) {
    renderPlot();
    renderDrawer();
  }
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
  const name = S.path ? S.path.split("/").pop() : "Untitled";
  document.title = `${name}${S.dirty ? " •" : ""} — PySME`;
  const latest = S.update.latest;
  $("#update-dot").hidden = !(latest && (latest.pysme_newer || latest.gui_newer));
}

function renderJob() {
  const job = S.job;
  const node = $("#job");
  node.className = "job";
  node.onclick = null;
  node.replaceChildren();
  if (job.state === "running") {
    const title = job.kind === "fit" ? "Fitting" : "Synthesizing";
    node.append(h("span", { class: "spinner" }), `${title} ${Math.floor(job.elapsed)} s · ${job.progress}`);
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
    if (job.state === "failed") toast(`${job.error}`, "error");
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

function currentValue(name) {
  if (name.startsWith("atmo.")) return S.atmo[name.slice(5)] ?? "auto";
  return S.params[name];
}

let wavelengthKey = "";

/* "force" also overwrites a field the user is in the middle of typing. */
function renderForms(rebuild, force = false) {
  if (!rebuild) return;
  for (const input of $$("[data-param]")) {
    if (!force && input.dataset.editing && input === document.activeElement) continue;
    delete input.dataset.editing;
    const value = currentValue(input.dataset.param);
    if (input.type === "checkbox") input.checked = Boolean(value);
    else if (input.tagName === "SELECT") {
      if (value !== null && ![...input.options].some((o) => o.value === String(value))) {
        input.append(h("option", { value: String(value) }, String(value)));
      }
      input.value = String(value);
    } else input.value = typeof value === "number" ? Number(value.toPrecision(8)) : value ?? "";
  }
  $("#vrad-input").disabled = S.params.vrad_flag === "none";
  $("#snr-block").hidden = !(S.observation && !S.observation.has_uncs);
  const ip = S.params.iptype;
  $("#ip-hint").textContent = S.params.ipres > 0
    ? `Instrumental profile: ${ip}, R = ${Math.round(S.params.ipres)}.`
    : "R = 0: no instrumental broadening.";
  // Only rebuilt when its content changed, so that unrelated updates never
  // throw away a half-typed range.
  const key = JSON.stringify([S.setup, S.observation, S.observation && S.segments]);
  if (force || key !== wavelengthKey) {
    wavelengthKey = key;
    renderWavelength();
  }
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

function renderWavelength() {
  const box = $("#wavelength");
  box.replaceChildren();
  const obs = S.observation;
  if (!obs) {
    let step = S.setup.step;
    let ranges = S.setup.ranges;
    const commit = () => send("synthsetup", { ranges, step });
    rangeRows(box, ranges, (value) => { ranges = value; commit(); });
    box.append(h("div", { class: "grid", style: "margin-top:8px" },
      h("label", { title: "Leave empty to let PySME choose an adaptive grid" }, "Step"),
      h("input", {
        type: "number", step: "0.01", min: "0", value: step ?? "", placeholder: "adaptive", "aria-label": "Wavelength step",
        onchange: (event) => {
          step = event.target.value;
          if (ranges.length) commit();
        },
      }),
      h("span", {}, "Å")));
    return;
  }
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
      h("div", { class: "hint" }, list.file ? list.file.split("/").pop() : "stored in the SME file"));
    card.lastChild.title = list.file || "";
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
  $("#fit-abunds").replaceChildren(...chosen.filter((n) => n.startsWith("abund ")).map((name) =>
    h("button", { type: "button", class: "chip on", title: "Remove", onclick: () => toggleFit(name, false) }, `A(${name.slice(6)}) ×`)));
}

/* --------------------------------------------------------------------- plot */



const SLOW_LIMIT = 60000;
let plotKey = "";
let lineTimer = null;
let currentLines = [];

function maskStrips(segment) {
  // Contiguous runs of equal mask value, drawn as a thin strip under the data.
  const colours = { 0: css("--m-bad"), 1: css("--m-line"), 2: css("--m-cont") };
  const shapes = [];
  const { wave, mask } = segment;
  if (!mask) return shapes;
  let start = 0;
  for (let i = 1; i <= mask.length; i++) {
    if (i === mask.length || mask[i] !== mask[start]) {
      const value = mask[start];
      const key = value === 0 ? 0 : value & 2 ? 2 : 1;
      const left = start === 0 ? wave[0] : (wave[start - 1] + wave[start]) / 2;
      const right = i === mask.length ? wave[i - 1] : (wave[i - 1] + wave[i]) / 2;
      shapes.push({
        type: "rect", xref: "x", yref: "y domain", x0: left, x1: right, y0: 0, y1: 0.025,
        fillcolor: colours[key], opacity: key === 1 ? 0.55 : 0.95, line: { width: 0 },
      });
      start = i;
    }
  }
  return shapes;
}

function buildPlot() {
  const segments = spectrum.segments;
  const total = segments.reduce((n, s) => n + s.wave.length, 0);
  const type = total > SLOW_LIMIT ? "scattergl" : "scatter";
  const hasObs = segments.some((s) => s.obs);
  const hasSynth = segments.some((s) => s.synth);
  const showResid = hasObs && hasSynth && $("#show-resid").checked;
  const stepped = hasObs ? "hvh" : "linear";
  const traces = [];
  const shapes = [];
  let first = { obs: true, synth: true, comp: true };
  const add = (key, trace) => {
    traces.push({ type, mode: "lines", legendgroup: key, showlegend: first[key] ?? false, ...trace });
    first[key] = false;
  };

  if (compareData) {
    for (const seg of compareData.segments) {
      add("comp", {
        x: seg.wave, y: seg.synth, name: compareData.label,
        line: { color: css("--compare"), width: 1.2 }, hovertemplate: "%{y:.4f}<extra>earlier</extra>",
      });
    }
  }
  for (const seg of segments) {
    if (seg.obs) {
      add("obs", {
        x: seg.wave, y: seg.obs, name: "observed",
        line: { color: css("--obs"), width: 1, shape: stepped }, hovertemplate: "%{y:.4f}<extra>observed</extra>",
      });
      shapes.push(...maskStrips(seg));
    }
    if (seg.synth) {
      add("synth", {
        x: seg.wave, y: seg.synth, name: S.synth_stale ? "synthetic (out of date)" : "synthetic",
        line: { color: css("--synth"), width: 1.6, shape: stepped, dash: S.synth_stale ? "dot" : "solid" },
        hovertemplate: "%{y:.4f}<extra>synthetic</extra>",
      });
    }
    if (showResid && seg.obs && seg.synth) {
      traces.push({
        type, mode: "lines", x: seg.wave, y: seg.obs.map((v, i) => (v === null || seg.synth[i] === null ? null : v - seg.synth[i])),
        yaxis: "y2", showlegend: false, line: { color: css("--resid"), width: 1, shape: "hvh" },
        hovertemplate: "%{y:.4f}<extra>obs − synth</extra>",
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

  const axis = {
    gridcolor: css("--grid"), zerolinecolor: css("--border"), linecolor: css("--border"),
    tickfont: { color: css("--muted"), size: 11 }, title: { font: { color: css("--muted"), size: 12 } },
  };
  // Leave room under the deepest line for the line labels and the mask strip.
  let low = Infinity, high = -Infinity;
  for (const seg of [...segments, ...(compareData ? compareData.segments : [])]) {
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
    margin: { l: 58, r: 14, t: 28, b: 40 },
    showlegend: true,
    legend: { orientation: "h", x: 0, xanchor: "left", y: 1.0, yanchor: "bottom", font: { size: 11 } },
    hovermode: "x unified",
    hoverlabel: { bgcolor: css("--surface"), bordercolor: css("--border"), font: { color: css("--text") } },
    dragmode: ui.tool === "zoom" ? "zoom" : "select",
    selectdirection: "h",
    newselection: { line: { color: css("--muted") } },
    xaxis: { ...axis, title: { ...axis.title, text: "Wavelength (Å)" }, anchor: showResid ? "y2" : "y", exponentformat: "none", tickformat: "" , hoverformat: ".4f" },
    yaxis: { ...axis, title: { ...axis.title, text: S.params.normalize_by_continuum || hasObs ? "Normalised flux" : "Flux" }, domain: showResid ? [0.3, 1] : [0, 1], range: yRange },
    shapes,
    selections: [],
    selectionrevision: S.revision,
  };
  if (showResid) layout.yaxis2 = { ...axis, domain: [0, 0.23], title: { ...axis.title, text: "O − S" } };
  return { traces, layout };
}

function renderPlot() {
  const empty = !spectrum.segments.length;
  $("#empty").hidden = !empty;
  const ready = empty && !S.ready.synth.length;
  $("#empty-start").hidden = ready;
  $("#empty-ready").hidden = !ready;
  const graph = $("#plot");
  graph.style.visibility = empty ? "hidden" : "visible";
  renderPlotSelectors();
  if (empty) {
    plotKey = "";
    return;
  }
  // Keep the user's zoom while only the synthetic spectrum changes.
  plotKey = spectrum.segments.map((s) => `${s.wave[0]}-${s.wave[s.wave.length - 1]}`).join("|") + ($("#show-resid").checked ? "r" : "");
  const { traces, layout } = buildPlot();
  const isNew = !graph._pysmeReady;
  Plotly.react(graph, traces, layout, {
    responsive: true, displaylogo: false, scrollZoom: true,
    modeBarButtonsToRemove: ["toImage", "select2d", "lasso2d"],
  }).then(() => {
    if (isNew) {
      graph._pysmeReady = true;
      graph.on("plotly_selected", onSelected);
      graph.on("plotly_relayout", (event) => {
        if (Object.keys(event).some((k) => k.startsWith("xaxis"))) scheduleLineLabels();
      });
    }
    scheduleLineLabels();
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
    try {
      lines = (await request(`lines?wmin=${a}&wmax=${b}&limit=${limit}`)).lines;
    } catch (error) {
      return;
    }
  }
  // Greedy: strongest first, skip labels that would overlap one already placed.
  const spacing = (b - a) / Math.max(1, (graph._fullLayout.width - 80) / 13);
  const placed = [];
  for (const line of lines) {
    if (placed.every((p) => Math.abs(p.wave - line.wave) > spacing)) placed.push(line);
  }
  const key = JSON.stringify(placed.map((p) => p.wave));
  if (key === JSON.stringify(currentLines.map((p) => p.wave)) && graph.layout.annotations?.length === placed.length) return;
  currentLines = placed;
  Plotly.relayout(graph, {
    annotations: placed.map((line) => ({
      x: line.wave, xref: "x", y: 0.035, yref: "y domain", yanchor: "bottom", text: line.species,
      textangle: -90, showarrow: false, font: { size: 10, color: css("--muted") },
      hovertext: `${line.species} ${line.wave.toFixed(3)} Å`,
    })),
  });
}

async function onSelected(event) {
  if (ui.tool === "zoom" || !event || !event.range || !event.range.x) return;
  const [start, end] = event.range.x;
  await send("mask", { start, end, value: Number(ui.tool) });
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
  const canMask = spectrum.segments.some((s) => s.obs);
  for (const button of $$("#tools button")) {
    if (button.dataset.tool !== "zoom") button.disabled = !canMask;
  }
  if (!canMask && ui.tool !== "zoom") setTool("zoom", false);
}

function setTool(tool, redraw = true) {
  ui.tool = tool;
  for (const button of $$("#tools button")) button.classList.toggle("active", button.dataset.tool === tool);
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
  else if (ui.drawer === "nlte") renderNlte();
  else if (ui.drawer === "cite") renderCitations();
  else if (ui.drawer === "log") $(".drawer-body").scrollTop = $(".drawer-body").scrollHeight;
}

function renderResults() {
  const box = $("#results");
  const main = h("div", { class: "results-main" });
  const side = h("div", { class: "results-runs" });
  const fit = S.fit;
  if (fit) {
    if (fit.outdated) main.append(h("p", { class: "hint" }, "A synthesis was run after this fit, so the plot may not show the fitted model."));
    main.append(h("table", {},
      h("thead", {}, h("tr", {},
        h("th", {}, "Parameter"), h("th", {}, "Start"), h("th", {}, "Best fit"),
        h("th", { title: "From the least-squares covariance matrix" }, "σ (least squares)"),
        h("th", { title: "PySME's estimate from the distribution of residuals; usually larger" }, "σ (SME)"))),
      h("tbody", {}, fit.parameters.map((p) => h("tr", {},
        h("td", {}, parameterLabel(p.name)), h("td", {}, fmt(p.initial)),
        h("td", {}, h("b", {}, fmt(p.value, 5))), h("td", {}, fmt(p.unc_fit, 2)), h("td", {}, fmt(p.unc_sme, 2)))))));
    const extra = [`reduced χ² = ${fmt(fit.chisq, 3)}`];
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

async function renderLines() {
  const limit = 100;
  const data = await request(`linelist?offset=${ui.linePage * limit}&limit=${limit}&q=${encodeURIComponent(ui.lineQuery)}`);
  const titles = { species: "Species", wlcent: "λ (Å)", excit: "χ (eV)", gflog: "log gf", gamrad: "Rad.", gamqst: "Stark", gamvw: "van der Waals", depth: "VALD depth" };
  const digits = { wlcent: 4, excit: 3, gflog: 3, gamrad: 2, gamqst: 2, gamvw: 3, depth: 3 };
  $("#line-table").replaceChildren(
    h("thead", {}, h("tr", {}, data.columns.map((c) => h("th", {}, titles[c] || c)))),
    h("tbody", {}, data.rows.map((row) => h("tr", {
      title: "Zoom the plot to this line",
      onclick: () => {
        const wave = row[data.columns.indexOf("wlcent")];
        zoomTo(wave - 1, wave + 1);
      },
    }, row.map((value, i) => h("td", {}, data.columns[i] in digits ? fixed(value, digits[data.columns[i]]) : value))))));
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

function nlteControls(element) {
  const enabled = S.nlte.elements.includes(element);
  const grids = gridsFor(element);
  if (!grids.length) return h("p", { class: "hint" }, `PySME has no NLTE grid for ${element}.`);
  const select = h("select", { "aria-label": `NLTE grid for ${element}`, disabled: jobRunning() },
    grids.map((g) => h("option", { value: g }, g)));
  select.value = S.nlte.grids[element] || options.nlte_defaults[element] || grids[0];
  select.addEventListener("change", () => {
    if (enabled) send("nlte", { element, enabled: true, grid: select.value });
  });
  return h("div", {},
    h("label", { class: "check" },
      h("input", { type: "checkbox", checked: enabled, disabled: jobRunning(), onchange: (e) => send("nlte", { element, enabled: e.target.checked, grid: select.value }) }),
      " NLTE"),
    select);
}

async function renderAbundances() {
  const data = await request("abund");
  const fitting = new Set(S.params.fitparameters);
  const table = $("#ptable");
  table.replaceChildren(h("div", { class: "gap", style: "grid-row:8" }));
  for (const row of data.elements) {
    const [r, c] = elementPosition(row.z);
    const classes = ["el"];
    if (Math.abs(row.delta || 0) > 0.0005) classes.push("changed");
    if (row.el === ui.element) classes.push("selected");
    if (fitting.has(`abund ${row.el}`)) classes.push("fit");
    if (S.nlte.elements.includes(row.el)) classes.push("nlte");
    table.append(h("button", {
      class: classes.join(" "), style: `grid-row:${r};grid-column:${c}`, title: `${row.el}: A = ${fixed(row.A, 3)}`,
      onclick: () => { ui.element = row.el; renderAbundances(); },
    }, h("b", {}, row.el), h("small", {}, row.A === null ? "–" : row.A.toFixed(2))));
  }
  const chosen = data.elements.find((e) => e.el === ui.element) || data.elements[25];
  const editor = $("#element-editor");
  const fixedElement = chosen.el === "H";
  editor.replaceChildren(
    h("h3", {}, `${chosen.el}  (Z = ${chosen.z})`),
    h("div", { class: "row" },
      h("label", { title: "log10(N_X / N_H) + 12 as used in the synthesis, including [M/H]" }, `A(${chosen.el})`),
      h("input", {
        type: "number", step: "0.05", value: chosen.A === null ? "" : Number(chosen.A.toFixed(4)), disabled: fixedElement || jobRunning(), "aria-label": `A(${chosen.el})`,
        onchange: (e) => send("abund/set", { element: chosen.el, value: e.target.value }),
      }),
      h("button", { disabled: fixedElement || jobRunning() || !chosen.delta, onclick: () => send("abund/set", { element: chosen.el, value: null }) }, "Reset")),
    h("p", { class: "hint" }, `Change relative to ${data.baseline}: ${chosen.delta === null ? "–" : (chosen.delta >= 0 ? "+" : "") + chosen.delta.toFixed(3)} dex (on top of [M/H] = ${fmt(data.monh)}).`),
    !fixedElement && h("label", { class: "check" },
      h("input", { type: "checkbox", checked: fitting.has(`abund ${chosen.el}`), disabled: jobRunning(), onchange: (e) => toggleFit(`abund ${chosen.el}`, e.target.checked) }),
      " Fit this abundance"),
    h("div", { style: "margin-top:8px" }, nlteControls(chosen.el)));
}

function renderNlte() {
  const box = $("#nlte-panel");
  const elements = S.nlte.elements;
  const candidates = Object.keys(options.nlte_defaults).filter((e) => !elements.includes(e));
  const add = h("select", { "aria-label": "Element to treat in NLTE", disabled: jobRunning() },
    h("option", { value: "" }, "Add element…"), candidates.map((e) => h("option", { value: e }, e)));
  add.addEventListener("change", () => add.value && send("nlte", { element: add.value, enabled: true }));
  box.replaceChildren(
    h("p", { class: "hint" }, "Elements listed here are synthesized with departure coefficients. Grids are downloaded to ~/.sme the first time; some are several GB."),
    elements.length
      ? h("table", { style: "max-width:560px" }, h("tbody", {}, elements.map((e) => h("tr", {}, h("td", {}, h("b", {}, e)), h("td", {}, nlteControls(e))))))
      : h("p", {}, "Everything is computed in LTE."),
    h("div", { class: "row" }, add));
}

async function renderCitations() {
  const format = $("#cite-format").value;
  $("#cite-text").textContent = "Building the reference list…";
  const data = await request("citation?format=" + format);
  $("#cite-text").textContent = data.text;
}

/* ------------------------------------------------------------------ dialogs */

function openDialog(title, body, buttons = [], onClose) {
  const dialog = $("#dialog");
  if (dialog.open) dialog.close();
  dialog.replaceChildren(
    h("div", { class: "dialog-head" }, h("h2", {}, title), h("button", { class: "ghost", title: "Close", onclick: () => dialog.close() }, "✕")),
    h("div", { class: "dialog-body" }, body),
    buttons.length ? h("div", { class: "dialog-foot" }, buttons) : "");
  dialog.onclose = onClose || null;
  dialog.showModal();
  return dialog;
}

function showMessage(title, text) {
  openDialog(title, h("pre", {}, text), [h("button", { onclick: () => { $("#dialog").close(); showDrawer("log"); } }, "Open log"), h("button", { class: "secondary", onclick: () => $("#dialog").close() }, "Close")]);
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
      result = value.startsWith("/") || value.startsWith("~") ? value : `${directory.replace(/\/$/, "")}/${value}`;
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
        ...data.entries.filter(wanted).map((entry) => h("li", {}, h("button", {
          onclick: (event) => {
            if (entry.dir) return list(`${data.dir.replace(/\/$/, "")}/${entry.name}`);
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

async function showSettings(info) {
  info = info || (await request("settings"));
  const latest = info.latest;
  const task = info.task;
  const versionLine = (label, current, newest, isNewer) => [
    h("span", {}, label),
    h("span", {}, current, latest && newest ? (isNewer ? h("b", {}, `  → ${newest} available`) : "  (up to date)") : ""),
  ];
  const body = h("div", {},
    h("h3", {}, "Versions"),
    h("div", { class: "kv" },
      versionLine("PySME", info.pysme, latest?.pysme, latest?.pysme_newer),
      versionLine("PySME-GUI", info.gui, latest?.gui, latest?.gui_newer),
      h("span", {}, "SMElib"), h("span", {}, info.smelib),
      h("span", {}, "Python"), h("span", {}, `${info.python_version} — ${info.python}`),
      h("span", {}, "Update source"), h("span", {}, `pypi.org/project/pysme-astro; ${info.source}`),
      h("span", {}, "App data"), h("span", {}, info.home),
      h("span", {}, "Model data"), h("span", {}, "~/.sme (shared with other PySME installations)")),
    latest && latest.errors.length ? h("p", { class: "hint" }, "Could not check: " + latest.errors.join("; ")) : "",
    latest ? h("p", { class: "hint" }, `Last checked ${latest.checked}.`) : "");

  const buttons = h("div", { class: "row" });
  const run = async (path, payload) => {
    try {
      await showSettings(await request(path, payload));
      await refresh();
    } catch (error) {
      toast(error.message, "error");
    }
  };
  const busy = task.state === "running";
  buttons.append(h("button", { disabled: busy, onclick: (e) => { e.target.textContent = "Checking…"; run("update/check", {}); } }, "Check for updates"));
  if (info.managed) {
    const available = latest && (latest.pysme_newer || latest.gui_newer);
    buttons.append(h("button", { class: "primary", disabled: busy || !available, onclick: () => run("update/apply", { pysme: true, gui: true }) }, "Update now"));
    if (info.previous) {
      buttons.append(h("button", {
        disabled: busy,
        title: `PySME ${info.previous.pysme_version}, GUI ${info.previous.gui_version}, installed ${info.previous.created}`,
        onclick: () => run("update/rollback", {}),
      }, "Go back to previous version"));
    }
  }
  body.append(buttons);
  if (!info.managed) body.append(h("p", { class: "hint" }, "One-click update is available when PySME-GUI runs in its own installed environment. This session uses another Python."));
  if (busy) body.append(h("p", {}, h("span", { class: "spinner" }), `${task.step}. A new environment is being built; the current one stays usable.`));
  if (task.state === "failed") body.append(h("p", { style: "color:var(--err);white-space:pre-wrap" }, `Update failed, nothing was changed.\n${task.error}`));
  if (task.state === "restart") {
    body.append(h("div", { class: "card" }, h("b", {}, "Ready. Restart to use the new version."),
      NATIVE ? h("button", { class: "secondary", onclick: restartApp }, "Restart now") : h("span", {}, "Quit this process and start PySME-GUI again.")));
  }

  const python = h("input", { type: "text", value: info.custom_python || "", placeholder: "empty = the app's own environment", "aria-label": "Custom Python", style: "flex:1" });
  body.append(
    h("h3", {}, "Use your own PySME"),
    h("p", { class: "hint" }, "Point to a Python that already has PySME, for example a development build. Applies after a restart."),
    h("div", { class: "row" }, python,
      h("button", {
        onclick: async (e) => {
          e.target.textContent = "Checking…";
          try {
            const result = await request("settings/python", { python: python.value.trim() });
            toast(result.custom_version ? `Saved. PySME ${result.custom_version} will be used after a restart.` : "The app's own environment will be used after a restart.");
            showSettings(result);
          } catch (error) {
            toast(error.message, "error");
            e.target.textContent = "Save";
          }
        },
      }, "Save")));

  openDialog("Updates & settings", body, [h("button", { onclick: () => $("#dialog").close() }, "Close")], () => clearTimeout(settingsTimer));
  clearTimeout(settingsTimer);
  if (busy) {
    settingsTimer = setTimeout(async () => {
      if ($("#dialog").open) await showSettings();
      await refresh();
    }, 1500);
  }
}

async function restartApp() {
  if (S.dirty && !(await confirmDialog("Restart with unsaved changes?", "Changes that were not saved will be lost.", "Restart"))) return;
  toNative({ type: "restart" });
}

/* ----------------------------------------------------------------- commands */

async function mayDiscard() {
  if (!S || !S.dirty) return true;
  return confirmDialog("Discard unsaved changes?", "The current setup has changes that are not saved.", "Discard");
}

/* Finish the field being edited so its value is sent before the next action. */
function commitEdits() {
  if (document.activeElement && document.activeElement.matches("input, select")) document.activeElement.blur();
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
    const path = await pickFile({ mode: "save", title: "Save as", extensions: ["sme"], name: S.path ? S.path.split("/").pop() : "spectrum.sme" });
    if (path && (await send("save", { path }))) toast("Saved.");
  },
  async exportCsv() {
    const path = await pickFile({ mode: "save", title: "Export spectrum", extensions: ["csv"], name: "spectrum.csv" });
    if (path && (await send("export/csv", { path }))) toast("CSV written.");
  },
  async exportPng() {
    const graph = $("#plot");
    if (!spectrum.segments.length) return toast("There is no plot yet.", "error");
    const url = await Plotly.toImage(graph, { format: "png", width: graph.clientWidth, height: graph.clientHeight, scale: 2 });
    if (NATIVE) toNative({ type: "savePng", data: url.split(",")[1], name: "spectrum.png" });
    else h("a", { href: url, download: "spectrum.png" }).click();
  },
  async example() { if (await mayDiscard()) { resetView(); send("example"); } },
  synth() { commitEdits(); send("run", { kind: "synth" }); },
  fit() { commitEdits(); send("run", { kind: "fit" }); },
  stop() { send("cancel"); },
  settings() { showSettings().catch((e) => toast(e.message, "error")); },
};

function resetView() {
  ui.compare = null;
  compareData = null;
  ui.linePage = 0;
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

const THEMES = ["auto", "light", "dark"];
function applyTheme(theme) {
  if (theme === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  $("#btn-theme").textContent = { auto: "Auto", light: "Light", dark: "Dark" }[theme];
  localStorageSet("pysme-theme", theme);
  if (S) renderPlot();
}

function wire() {
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-cmd]");
    if (button && !button.disabled) {
      commitEdits();
      commands[button.dataset.cmd]();
    }
    const menu = $("#menu-more");
    menu.hidden = event.target.closest("#btn-more") ? !menu.hidden : true;
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
      send("params", { values: { [name]: value } });
    });
    if (input.type === "number") input.addEventListener("keydown", (e) => e.key === "Enter" && input.blur());
  }
  // Scrolling the sidebar must never change a number by accident.
  document.addEventListener("wheel", () => {
    if (document.activeElement && document.activeElement.type === "number") document.activeElement.blur();
  }, { passive: true });

  for (const button of $$("#side-tabs button")) {
    button.addEventListener("click", () => {
      for (const other of $$("#side-tabs button")) other.classList.toggle("active", other === button);
      for (const panel of $$(".side-body [data-panel]")) panel.hidden = panel.dataset.panel !== button.dataset.tab;
      if (S) renderReadiness();
    });
  }
  for (const button of $$("#drawer-tabs [data-tab]")) {
    button.addEventListener("click", () => {
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

  for (const button of $$("#tools button")) button.addEventListener("click", () => setTool(button.dataset.tool));
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

  $("#cite-format").addEventListener("change", renderCitations);
  $("#cite-copy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("#cite-text").textContent);
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

  $("#btn-theme").addEventListener("click", () => {
    const current = localStorageGet("pysme-theme") || "auto";
    applyTheme(THEMES[(THEMES.indexOf(current) + 1) % THEMES.length]);
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => S && renderPlot());

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
      if (window.hasUnsavedChanges()) event.preventDefault();
    });
  }
  new ResizeObserver(resizePlot).observe($(".plot-area"));
}

let resizeFrame = null;
function resizePlot() {
  cancelAnimationFrame(resizeFrame);
  resizeFrame = requestAnimationFrame(() => {
    const graph = $("#plot");
    if (graph._pysmeReady && graph.clientWidth) {
      Plotly.Plots.resize(graph);
      scheduleLineLabels();
    }
  });
}

async function start() {
  applyTheme(localStorageGet("pysme-theme") || "auto");
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
  if (options.patterns.includes("asplund2021")) $("#abund-pattern").value = "asplund2021";
  const { elements } = await request("abund");
  $("#fit-element").replaceChildren(...elements.filter((e) => e.z > 2).map((e) => h("option", { value: e.el }, e.el)));
  $("#fit-element").value = "Mg";
  renderForms(true);
  // Quietly look for a newer version; only sets the dot on the settings button.
  request("update/check", {}).then(refresh).catch(() => {});
}

start();
