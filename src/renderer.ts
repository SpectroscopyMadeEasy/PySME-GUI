// This file is required by the index.html file and will
// be executed in the renderer process for that window.
// All of the Node.js APIs are available in this process.

const $ = require("jquery");
var dt = require('datatables.net')();
require('datatables.net-bs4')();
require('datatables.net-responsive-bs4')();
require("popper.js")
require("bootstrap");

const { dialog } = require('electron').remote
var tmp = require('tmp');
const chokidar = require('chokidar');
const { Table } = require("apache-arrow");
const homedir = require('os').homedir();
const { join, extname, basename } = require('path');
const fs = require('fs');
const untildify = require('untildify');
var Plotly = require("plotly.js")
const { spawn } = require('child_process');
const Cite = require('citation-js');
var Base64 = require('base64-arraybuffer');


// This section is from the template: https://startbootstrap.com/themes/sb-admin-2/
// Toggle the side navigation
$("#sidebarToggle, #sidebarToggleTop").on('click', function (e: any) {
    $("body").toggleClass("sidebar-toggled");
    $(".sidebar").toggleClass("toggled");
    if ($(".sidebar").hasClass("toggled")) {
        $('.sidebar .collapse').collapse('hide');
    };
});


// Close any open menu accordions when window is resized below 768px
$(window).resize(function () {
    if ($(window).width() < 768) {
        $('.sidebar .collapse').collapse('hide');
    };
});

// Prevent the content wrapper from scrolling when the fixed side navigation hovered over
$('body.fixed-nav .sidebar').on('mousewheel DOMMouseScroll wheel', function (e: any) {
    if ($(window).width() > 768) {
        var e0 = e.originalEvent,
            delta = e0.wheelDelta || -e0.detail;
        this.scrollTop += (delta < 0 ? 1 : -1) * 30;
        e.preventDefault();
    }
});

// Scroll to top button appear
$(document).on('scroll', function () {
    var scrollDistance = $(this).scrollTop();
    if (scrollDistance > 100) {
        $('.scroll-to-top').fadeIn();
    } else {
        $('.scroll-to-top').fadeOut();
    }
});

// Smooth scrolling using jQuery easing
$(document).on('click', 'a.scroll-to-top', function (e: any) {
    var $anchor = $(this);
    $('html, body').stop().animate({
        scrollTop: ($($anchor.attr('href')).offset().top)
    }, 1000, 'easeInOutExpo');
    e.preventDefault();
});
$("#sidebarToggle").click()


tmp.setGracefulCleanup();

class SmeError {
    constructor(public message: string) {
    }
}
class EndianError extends SmeError { }
class IdlError extends SmeError { }

function default_mu_values(): number[] {
    return [
        0.96362411,
        0.88640526,
        0.80178373,
        0.70710678,
        0.5976143,
        0.46291005,
        0.26726124,
    ]
}

function create_empty_sme_structure() {
    let struct: SmeFile = {
        header: {
            vrad: new Float64Array([0]),
            cscale: [new Float64Array([1])],
            wran: [],
            citation_info: "",
            teff: 5000,
            logg: 4,
            vmic: 1,
            vmac: 1,
            vsini: 1,
            id: "DEFAULT",
            object: "",
            version: "PySME-GUI",
            vrad_flag: "whole",
            cscale_flag: "constant",
            cscale_type: "mask",
            normalize_by_continuum: true,
            specific_intensities_only: false,
            gam6: 1,
            h2broad: true,
            accwi: 0.1,
            accrt: 0.1,
            mu: default_mu_values(),
            fitparameters: [],
            ipres: 0,
        },
        abund: {
            header: {
                format: "H=12",
                monh: 0,
                citation_info: "",
            },
            data: new Float64Array(99),
        },
        wave: null,
        spec: null,
        uncs: null,
        mask: null,
        synth: null,
        cont: null,
        telluric: null,
        linelist: {
            header: {
                format: "short",
                medium: "vac",
                citation_info: "",
            },
            data: []
        },
        atmo: {
            header: {
                teff: 5000,
                logg: 4,
                vturb: 0,
                lonh: 0,
                source: "marcs2012.sav",
                method: "grid",
                geom: "PP",
                radius: new Float64Array(),
                height: new Float64Array(),
                opflag: [],
                wlstd: 5000,
                depth: "RHOX",
                interp: "TAU",
                citation_info: "",
                abund_format: "H=12",
                monh: 0
            },
            tau: new Float64Array(),
            rhox: new Float64Array(),
            temp: new Float64Array(),
            rho: new Float64Array(),
            xna: new Float64Array(),
            xne: new Float64Array(),
            abund: new Float64Array(99),
        },
        nlte: {
            header: {
                citation_info: "",
                elements: [],
                grids: {},
                subgrid_size: [2, 2, 2, 2],
                flags: new Int8Array()
            }
        },
        system_info: {
            header: {}
        }
    }
    return struct
}

var sme = create_empty_sme_structure();
var sme_loaded_from_file = false;

var spectrum_palette = [
    "#ff7f0e",
    "#2ca02c",
    "#1f77b4",
    "#d62728",
    "#9467bd",
    "#8c564b",
    "#e377c2",
    "#7f7f7f",
    "#bcbd22",
    "#17becf",
];

function next_spectrum_color(store: SpectrumStoreState) {
    return spectrum_palette[store.spectra.length % spectrum_palette.length]
}

function make_spectrum_entry(store: SpectrumStoreState, kind: SpectrumKind, name: string, sme_data: SmeFile | null, source: SpectrumSource) {
    let now = Date.now()
    return {
        id: "spec_" + now.toString(36) + "_" + Math.random().toString(36).slice(2, 8),
        name: name,
        kind: kind,
        source: source,
        created_at: now,
        updated_at: now,
        color: next_spectrum_color(store),
        locked: kind === "obs",
        sme: sme_data,
    }
}

function add_synth_spectrum(store: SpectrumStoreState, name?: string, sme_data?: SmeFile | null) {
    store.counters.synth += 1
    let entry = make_spectrum_entry(store, "synth", name || ("Synth Spectrum " + store.counters.synth), sme_data || null, "params")
    store.spectra.push(entry)
    return entry
}

function add_observed_spectrum(store: SpectrumStoreState, name?: string, sme_data?: SmeFile | null) {
    store.counters.obs += 1
    let entry = make_spectrum_entry(store, "obs", name || ("Observed Spectrum " + store.counters.obs), sme_data || null, "file")
    store.spectra.push(entry)
    return entry
}

function add_fit_result_spectrum(store: SpectrumStoreState, name?: string, sme_data?: SmeFile | null) {
    store.counters.fit += 1
    let entry = make_spectrum_entry(store, "synth", name || ("Fit Result " + store.counters.fit), sme_data || null, "file")
    store.spectra.push(entry)
    return entry
}

function set_active_spectrum(store: SpectrumStoreState, spectrum_id: string | null) {
    store.active_id = spectrum_id
}

function set_fit_target(store: SpectrumStoreState, spectrum_id: string | null) {
    store.fit_target_id = spectrum_id
}

function get_active_spectrum(store: SpectrumStoreState) {
    return store.spectra.find((entry) => entry.id === store.active_id) || null
}

function create_spectrum_store(initial_sme: SmeFile) {
    let store: SpectrumStoreState = {
        mode: "synthesize",
        spectra: [],
        active_id: null,
        fit_target_id: null,
        counters: { synth: 0, obs: 0, fit: 0 },
    }
    return store
}

var spectrum_store = create_spectrum_store(sme);
(window as any).spectrum_store = spectrum_store;

function ensure_entry_sme(entry: SpectrumEntry) {
    if (!entry.sme) {
        entry.sme = create_empty_sme_structure()
    }
}

function set_mode(mode: SpectrumMode) {
    spectrum_store.mode = mode
    let btnSynth = document.getElementById("btn-mode-synth") as HTMLButtonElement
    let btnFit = document.getElementById("btn-mode-fit") as HTMLButtonElement
    let fitTarget = document.getElementById("spectrum-fit-target") as HTMLDivElement
    if (btnSynth && btnFit) {
        if (mode === "synthesize") {
            btnSynth.classList.add("active")
            btnFit.classList.remove("active")
        } else {
            btnFit.classList.add("active")
            btnSynth.classList.remove("active")
        }
    }
    if (fitTarget) {
        fitTarget.style.display = mode === "fit" ? "flex" : "none"
    }
}

function render_fit_target_options() {
    let select = document.getElementById("select-fit-target") as HTMLSelectElement
    if (!select) return
    select.innerHTML = ""
    spectrum_store.spectra.forEach((entry) => {
        let opt = document.createElement("option")
        opt.value = entry.id
        opt.textContent = entry.name
        select.appendChild(opt)
    })
    if (!spectrum_store.fit_target_id) {
        spectrum_store.fit_target_id = spectrum_store.active_id
    }
    if (spectrum_store.fit_target_id) {
        select.value = spectrum_store.fit_target_id
    }
}

function clone_float_array(value: any) {
    if (!value) return value
    if (value instanceof Float64Array) return new Float64Array(value)
    if (value instanceof Float32Array) return new Float32Array(value)
    return value
}

function summarize_mask(mask: any): { segments: number; counts: { [id: number]: number } } | null {
    if (!mask) return null
    let segments = 0
    let counts: { [id: number]: number } = {}
    const add_counts = (arr: any) => {
        if (!arr || typeof arr.length !== "number") return
        for (let i = 0; i < arr.length; i++) {
            let v = Number(arr[i])
            if (!Number.isFinite(v)) continue
            counts[v] = (counts[v] || 0) + 1
        }
    }
    if (ArrayBuffer.isView(mask)) {
        segments = 1
        add_counts(mask)
    } else if (Array.isArray(mask)) {
        segments = mask.length
        mask.forEach((seg) => add_counts(seg))
    } else if (typeof mask === "object") {
        let keys = Object.keys(mask)
            .filter((k) => k !== "header" && k !== "length")
            .map((k) => Number(k))
            .filter((k) => Number.isFinite(k))
        segments = keys.length
        keys.forEach((k) => add_counts(mask[k]))
    }
    return { segments, counts }
}

function sync_fit_params_from_active(source: SmeFile, target: SmeFile) {
    target.header.teff = source.header.teff
    target.header.logg = source.header.logg
    target.header.vmic = source.header.vmic
    target.header.vmac = source.header.vmac
    target.header.vsini = source.header.vsini
    target.header.gam6 = source.header.gam6
    target.header.h2broad = source.header.h2broad
    target.header.accrt = source.header.accrt
    target.header.accwi = source.header.accwi
    target.header.cscale_flag = source.header.cscale_flag
    target.header.cscale_type = source.header.cscale_type
    target.header.vrad_flag = source.header.vrad_flag
    target.header.normalize_by_continuum = source.header.normalize_by_continuum
    target.header.specific_intensities_only = source.header.specific_intensities_only
    target.header.mu = clone_float_array(source.header.mu)
    if (source.header.wran && source.header.wran.length > 0) {
        target.header.wran = source.header.wran
    }
    target.header.fitparameters = source.header.fitparameters

    if (target.abund && source.abund) {
        target.abund.header.monh = source.abund.header.monh
        target.abund.header.format = source.abund.header.format
        target.abund.data = clone_float_array(source.abund.data)
    }
    if (target.atmo && source.atmo && source.atmo.header) {
        target.atmo.header.source = source.atmo.header.source
        target.atmo.header.method = source.atmo.header.method
        target.atmo.header.geom = source.atmo.header.geom
        target.atmo.header.depth = source.atmo.header.depth
        target.atmo.header.interp = source.atmo.header.interp
        target.atmo.header.wlstd = source.atmo.header.wlstd
    }
}

function activate_spectrum_entry(entry: SpectrumEntry) {
    ensure_entry_sme(entry)
    set_active_spectrum(spectrum_store, entry.id)
    sme = entry.sme as SmeFile
    sme_loaded_from_file = entry.source === "file"
    cast_load_event(sme, "spectrum", "parameters", "linelist", "atmosphere", "abundance", "citation")
}

function delete_spectrum_entry(entry: SpectrumEntry) {
    if (entry.locked) return
    let idx = spectrum_store.spectra.indexOf(entry)
    if (idx >= 0) {
        spectrum_store.spectra.splice(idx, 1)
    }
    if (spectrum_store.active_id === entry.id) {
        let next = spectrum_store.spectra[idx] || spectrum_store.spectra[idx - 1] || null
        if (next) {
            activate_spectrum_entry(next)
        } else {
            spectrum_store.active_id = null
            spectrum_store.fit_target_id = null
            sme = create_empty_sme_structure()
            sme_loaded_from_file = false
            cast_load_event(sme, "spectrum", "parameters", "linelist", "atmosphere", "abundance", "citation")
        }
    }
    render_spectrum_list()
    if (spectrum_store.active_id && sme) {
        cast_load_event(sme, "spectrum")
    }
}

function render_spectrum_list() {
    let container = document.getElementById("spectrum-list") as HTMLDivElement
    if (!container) return
    container.innerHTML = ""
    spectrum_store.spectra.forEach((entry) => {
        let row = document.createElement("div")
        row.className = "spectrum-list-row" + (entry.id === spectrum_store.active_id ? " active" : " inactive")

        let radio = document.createElement("input")
        radio.type = "radio"
        radio.name = "spectrum-active"
        radio.checked = entry.id === spectrum_store.active_id
        radio.addEventListener("change", () => {
            activate_spectrum_entry(entry)
            render_spectrum_list()
        })
        row.appendChild(radio)

        let swatch = document.createElement("span")
        swatch.className = "spectrum-color"
        swatch.style.backgroundColor = entry.color
        row.appendChild(swatch)

        let nameInput = document.createElement("input")
        nameInput.type = "text"
        nameInput.className = "form-control form-control-sm spectrum-name"
        nameInput.value = entry.name
        nameInput.addEventListener("change", () => {
            entry.name = nameInput.value.trim() || entry.name
            if (sme) {
                cast_load_event(sme, "spectrum")
            }
            render_fit_target_options()
        })
        row.appendChild(nameInput)

        let badge = document.createElement("span")
        badge.className = "spectrum-kind"
        badge.textContent = entry.kind === "obs" ? "Observed" : "Synth"
        row.appendChild(badge)

        let del = document.createElement("button")
        del.className = "btn btn-sm btn-outline-secondary spectrum-delete"
        del.textContent = "Delete"
        del.disabled = entry.locked
        del.addEventListener("click", () => {
            delete_spectrum_entry(entry)
        })
        row.appendChild(del)

        container.appendChild(row)
    })
    render_fit_target_options()
    let graphDiv = document.getElementById("div-graph") as any
    if (graphDiv && graphDiv.data) {
        setTimeout(() => {
            try {
                Plotly.Plots.resize(graphDiv)
            } catch (err) {
                console.error(err)
            }
        }, 0)
    }
}

render_spectrum_list()
set_mode("synthesize")

var ButtonModeSynth = document.getElementById("btn-mode-synth")
if (ButtonModeSynth) {
    ButtonModeSynth.addEventListener("click", () => {
        set_mode("synthesize")
    })
}
var ButtonModeFit = document.getElementById("btn-mode-fit")
if (ButtonModeFit) {
    ButtonModeFit.addEventListener("click", () => {
        set_mode("fit")
    })
}
var SelectFitTarget = document.getElementById("select-fit-target") as HTMLSelectElement
if (SelectFitTarget) {
    SelectFitTarget.addEventListener("change", () => {
        set_fit_target(spectrum_store, SelectFitTarget.value)
    })
}

function parse_spectrum_file(contents: string) {
    let rows: number[][] = []
    let lines = contents.split(/\r?\n/)
    for (let i = 0; i < lines.length; i++) {
        let line = lines[i].trim()
        if (!line) continue
        if (line.startsWith("#") || line.startsWith("//")) continue
        let parts = line.split(/[,\s;]+/).filter((p) => p.length > 0)
        if (parts.length < 2) continue
        let nums = parts.map((p) => Number(p)).filter((n) => Number.isFinite(n))
        if (nums.length < 2) continue
        rows.push(nums)
    }
    if (rows.length === 0) {
        return { segments: {}, has_uncs: false }
    }

    let max_cols = Math.max(...rows.map((r) => r.length))
    let use_segment = false
    let use_uncs = false

    if (max_cols >= 4) {
        use_segment = true
        use_uncs = true
    } else if (max_cols === 3) {
        let first_col = rows.map((r) => r[0])
        let all_int = first_col.every((v) => Number.isFinite(v) && Math.abs(v - Math.round(v)) < 1e-6)
        let unique = Array.from(new Set(first_col.map((v) => Math.round(v))))
        let max_val = Math.max(...first_col.map((v) => Math.round(v)))
        if (all_int && unique.length <= 50 && max_val <= 50) {
            use_segment = true
            use_uncs = false
        } else {
            use_segment = false
            use_uncs = true
        }
    }

    let segments: { [id: number]: { wave: number[]; spec: number[]; uncs?: number[] } } = {}
    for (let i = 0; i < rows.length; i++) {
        let row = rows[i]
        let seg = 0
        let w: number
        let s: number
        let u: number | null = null

        if (use_segment) {
            if (row.length < 3) continue
            seg = Math.trunc(row[0])
            w = row[1]
            s = row[2]
            if (use_uncs && row.length >= 4) {
                u = row[3]
            }
        } else {
            w = row[0]
            s = row[1]
            if (use_uncs && row.length >= 3) {
                u = row[2]
            }
        }

        if (!segments[seg]) {
            segments[seg] = { wave: [], spec: [], uncs: use_uncs ? [] : undefined }
        }
        segments[seg].wave.push(w)
        segments[seg].spec.push(s)
        if (use_uncs) {
            (segments[seg].uncs as number[]).push(u ?? NaN)
        }
    }
    return { segments, has_uncs: use_uncs }
}

function build_observed_sme_from_segments(segments: { [id: number]: { wave: number[]; spec: number[]; uncs?: number[] } }, has_uncs: boolean) {
    let new_sme = create_empty_sme_structure()
    let keys = Object.keys(segments)
        .map((k) => Number(k))
        .filter((k) => Number.isFinite(k))
        .sort((a, b) => a - b)

    let wave_vec: Float64Array[] = []
    let spec_vec: Float64Array[] = []
    let uncs_vec: Float64Array[] = []
    let wran: Array<FloatArray | number[]> = []

    for (let i = 0; i < keys.length; i++) {
        let seg = keys[i]
        let wave = segments[seg].wave
        let spec = segments[seg].spec
        let uncs = segments[seg].uncs
        if (wave.length === 0) continue
        wave_vec.push(new Float64Array(wave))
        spec_vec.push(new Float64Array(spec))
        if (has_uncs && uncs) {
            uncs_vec.push(new Float64Array(uncs))
        }
        let wmin = wave[0]
        let wmax = wave[0]
        for (let j = 1; j < wave.length; j++) {
            let value = wave[j]
            if (value < wmin) wmin = value
            if (value > wmax) wmax = value
        }
        wran.push([wmin, wmax])
    }
    new_sme.wave = wave_vec
    new_sme.spec = spec_vec
    if (has_uncs) {
        new_sme.uncs = uncs_vec
    }
    new_sme.header.wran = wran
    new_sme.header.vrad = new Float64Array(wave_vec.length)
    new_sme.header.cscale = wave_vec.map(() => new Float64Array([1]))
    return new_sme
}

function get_vector_segments(vec: any): FloatArray[] {
    if (!vec) return []
    if (ArrayBuffer.isView(vec)) return [vec as FloatArray]
    if (Array.isArray(vec)) return vec
    if (typeof vec.length === "number") {
        let segments: FloatArray[] = []
        for (let i = 0; i < vec.length; i++) {
            if (vec[i]) segments.push(vec[i])
        }
        return segments
    }
    let keys = Object.keys(vec)
        .filter((k) => k !== "header" && k !== "length")
        .map((k) => Number(k))
        .filter((k) => Number.isFinite(k))
        .sort((a, b) => a - b)
    return keys.map((k) => vec[k]) as FloatArray[]
}

function spectrum_to_csv(entry: SpectrumEntry, include_segment: boolean) {
    if (!entry.sme) return ""
    let wave_segments = get_vector_segments(entry.sme.wave)
    let synth_segments = get_vector_segments(entry.sme.synth)
    let lines: string[] = []
    if (include_segment) {
        lines.push("segment,wave,synth")
    } else {
        lines.push("wave,synth")
    }
    for (let seg = 0; seg < wave_segments.length; seg++) {
        let wave = wave_segments[seg]
        let synth = synth_segments[seg]
        if (!synth) continue
        if (!include_segment && seg > 0) {
            lines.push("")
        }
        for (let i = 0; i < wave.length; i++) {
            if (include_segment) {
                lines.push([seg, wave[i], synth[i]].join(","))
            } else {
                lines.push([wave[i], synth[i]].join(","))
            }
        }
    }
    return lines.join("\n")
}



var config: { [id: string]: any } = {};
fs.readFile(join(homedir, ".sme", "config.json"), "utf-8", (err: any, data: string) => {
    config = JSON.parse(data)
    dispatchEvent(new CustomEvent("config_loaded", { detail: config }))
});


async function synthesize_spectrum(sme: SmeFile) {
    var script_file = join(__dirname, "scripts/synthesize_spectrum.py")
    var tmpin = tmp.fileSync({ postfix: ".sme" });
    var tmpout = tmp.fileSync({ postfix: ".sme" });
    var tmplog = tmp.fileSync({ postfix: ".log", keep: true });

    // Watch the logfile
    console.log("Watching file: " + tmplog.name)
    const watcher = chokidar.watch(tmplog.name, { usePolling: true })
    watcher.on('change', update_log)

    var promise = new Promise<SmeFile>((resolve, reject) => {
        save_file(tmpin.name, sme).then(() => {
            call_python("synthesize_spectrum.py", [tmpin.name, tmpout.name, "--log_file=" + tmplog.name]).then(() => {
                load_file(tmpout.name).then((sme) => { resolve(sme) })
            })
        });
    })

    return promise
}

function apply_wavelength_step_from_ui(sme: SmeFile) {
    let wstart_field = document.getElementById("par-wstart") as HTMLInputElement
    let wend_field = document.getElementById("par-wend") as HTMLInputElement
    let wstep_field = document.getElementById("par-wstep") as HTMLInputElement

    let wstart = Number(wstart_field.value)
    let wend = Number(wend_field.value)
    let wstep_raw = wstep_field.value
    let wstep = Number(wstep_raw)

    if (!Number.isFinite(wstart) || !Number.isFinite(wend) || wstart >= wend) {
        return
    }

    sme.header.wran = [[wstart, wend]]

    if (wstep_raw.trim() === "" || !Number.isFinite(wstep) || wstep <= 0) {
        sme.wave = null
        return
    }

    let count = Math.floor((wend - wstart) / wstep) + 1
    let wave = new Float64Array(count)
    for (let i = 0; i < count; i++) {
        wave[i] = wstart + i * wstep
    }
    sme.wave = [wave]
}

function validate_required_params_for_synthesize(): boolean {
    let missing: string[] = []
    let teff = (document.getElementById("par-teff") as HTMLInputElement).value
    let logg = (document.getElementById("par-logg") as HTMLInputElement).value
    let monh = (document.getElementById("par-monh") as HTMLInputElement).value
    let wstart = (document.getElementById("par-wstart") as HTMLInputElement).value
    let wend = (document.getElementById("par-wend") as HTMLInputElement).value

    if (!teff.trim()) missing.push("Teff")
    if (!logg.trim()) missing.push("log(g)")
    if (!monh.trim()) missing.push("[M/H]")
    if (!wstart.trim()) missing.push("Wavelength start")
    if (!wend.trim()) missing.push("Wavelength end")

    if (missing.length > 0) {
        show_error("Missing required parameters: " + missing.join(", "))
        return false
    }
    return true
}

async function synthesize_from_params() {
    let tmpin = tmp.fileSync({ postfix: ".json" });
    let tmpout = tmp.fileSync({ postfix: ".sme" });
    let tmplog = tmp.fileSync({ postfix: ".log", keep: true });

    console.log("Watching file: " + tmplog.name)
    const watcher = chokidar.watch(tmplog.name, { usePolling: true })
    watcher.on('change', update_log)

    let wstart_field = document.getElementById("par-wstart") as HTMLInputElement
    let wend_field = document.getElementById("par-wend") as HTMLInputElement
    let wstep_field = document.getElementById("par-wstep") as HTMLInputElement

    let wstart = Number(wstart_field.value)
    let wend = Number(wend_field.value)
    let wstep = wstep_field.value

    if (!Number.isFinite(wstart) || !Number.isFinite(wend)) {
        show_error("Please set wavelength start and end before synthesizing.")
        throw new Error("Missing wavelength range")
    }

    let linelist_file = sme.system_info && sme.system_info.header ? sme.system_info.header["linelist_file"] : ""
    if (!linelist_file) {
        show_error("Please load a linelist file before synthesizing.")
        throw new Error("Missing linelist file")
    }

    let atmo_source = sme.atmo && sme.atmo.header ? sme.atmo.header.source : ""
    if (!atmo_source) {
        show_error("Please select an atmosphere model before synthesizing.")
        throw new Error("Missing atmosphere source")
    }

    let params = {
        teff: sme.header.teff,
        logg: sme.header.logg,
        monh: sme.abund.header.monh,
        wstart: wstart,
        wend: wend,
        wstep: wstep,
        linelist_file: linelist_file,
        atmo_source: atmo_source,
        atmo_method: sme.atmo.header.method,
        atmo_geom: sme.atmo.header.geom,
        atmo_depth: sme.atmo.header.depth,
        atmo_interp: sme.atmo.header.interp,
    }

    fs.writeFileSync(tmpin.name, JSON.stringify(params))
    await call_python("synthesize_from_params.py", [tmpin.name, tmpout.name, "--log_file=" + tmplog.name])
    let sme_out = await load_file(tmpout.name)
    return sme_out
}


async function fit_spectrum(sme: SmeFile) {
    var script_file = join(__dirname, "scripts/fit_spectrum.py")
    var tmpin = tmp.fileSync({ postfix: ".sme" });
    var tmpout = tmp.fileSync({ postfix: ".sme" });
    var tmplog = tmp.fileSync({ postfix: ".log", keep: true });

    console.log("Watching file: " + tmplog.name)
    const watcher = chokidar.watch(tmplog.name, { usePolling: true })
    watcher.on('change', update_log);

    var promise = new Promise<SmeFile>((resolve, reject) => {
        save_file(tmpin.name, sme).then(() => {
            call_python("fit_spectrum.py", [tmpin.name, tmpout.name, ...sme.header.fitparameters, "--log_file=" + tmplog.name]).then(() => {
                load_file(tmpout.name).then((sme) => { resolve(sme); })
            })
        });
    })

    return promise
}

async function get_pysme_version() {
    let tmpout = tmp.fileSync({ postfix: ".txt" });
    let success = await call_python("get_pysme_version.py", [tmpout.name])
    let data = fs.readFileSync(tmpout.name, { encoding: "utf-8" })
    return data
}

function cast_load_event(sme: SmeFile, ...updated: string[]) {
    dispatchEvent(new CustomEvent("pysme_load", { detail: { structure: sme, updated: updated } }))
}

var ButtonLoad = document.getElementById("btn-load")
ButtonLoad.addEventListener('click', async (event) => {
    var out = await dialog.showOpenDialog({
        properties: ["openFile"],
    })
    if (!out.canceled) {
        var fname = out.filePaths[0];
        console.log("Opening new file: " + fname)
        let ext = extname(fname).toLowerCase()
        if (ext === ".sme") {
            sme = await load_file(fname)
            let obs_entry = add_observed_spectrum(spectrum_store, undefined, sme)
            activate_spectrum_entry(obs_entry)
            sme_loaded_from_file = true
            render_spectrum_list()
        } else {
            let contents = fs.readFileSync(fname, { encoding: "utf-8" })
            let parsed = parse_spectrum_file(contents)
            let seg_keys = Object.keys(parsed.segments)
            if (seg_keys.length === 0) {
                show_error("No valid numeric spectrum data found in file.")
                return
            }
            let obs_sme = build_observed_sme_from_segments(parsed.segments, parsed.has_uncs)
            let name = "Observed " + basename(fname)
            let obs_entry = add_observed_spectrum(spectrum_store, name, obs_sme)
            activate_spectrum_entry(obs_entry)
            render_spectrum_list()
        }
    } else {
        console.log("User did not select a file")
    }
})

var ButtonNewSpectrum = document.getElementById("btn-new-spectrum")
ButtonNewSpectrum.addEventListener('click', () => {
    let new_sme = create_empty_sme_structure()
    let entry = add_synth_spectrum(spectrum_store, undefined, new_sme)
    activate_spectrum_entry(entry)
    render_spectrum_list()
})

var ButtonSave = document.getElementById("btn-save")
ButtonSave.addEventListener('click', async (event) => {
    var out = await dialog.showSaveDialog({
        properties: ["showOverwriteConfirmation"],
        filters: [
            { name: "SME files", extensions: ["sme"] },
            { name: "Spectrum CSV", extensions: ["csv"] },
        ]
    })
    if (!out.canceled) {
        var fname = out.filePath
        console.log("Saving to file: " + fname)
        if (!fname) return
        let ext = extname(fname).toLowerCase()
        if (ext === ".csv") {
            let active_entry = get_active_spectrum(spectrum_store)
            if (!active_entry || !active_entry.sme || !active_entry.sme.synth) {
                show_error("No synthetic spectrum available to save.")
                return
            }
            let choice = await dialog.showMessageBox({
                type: "question",
                buttons: ["Simple (wave,synth)", "With segment", "Cancel"],
                defaultId: 0,
                cancelId: 2,
                message: "Choose CSV output format"
            })
            if (choice.response === 2) {
                return
            }
            let include_segment = choice.response === 1
            let csv = spectrum_to_csv(active_entry, include_segment)
            fs.writeFileSync(fname, csv)
        } else {
            save_file(fname, sme)
        }
    } else {
        console.log("User did not select a file")
    }
})

var ButtonSynthesize = document.getElementById("btn-synthesize")
ButtonSynthesize.addEventListener('click', async (event) => {
    console.log("Start synthesizing")
    clear_alerts()
    show_synthesis_status("Synthesizing...", true)
    try {
        if (!validate_required_params_for_synthesize()) {
            hide_synthesis_status()
            return
        }
        let active_entry = get_active_spectrum(spectrum_store)
        if (!active_entry) {
            active_entry = add_synth_spectrum(spectrum_store)
            activate_spectrum_entry(active_entry)
        }
        if (active_entry && active_entry.kind === "obs") {
            show_error("Cannot synthesize an observed spectrum.")
            hide_synthesis_status()
            return
        }
        let updated_sections: string[] = []
        if (sme_loaded_from_file) {
            apply_wavelength_step_from_ui(sme)
            sme = await synthesize_spectrum(sme)
            updated_sections = ["spectrum", "citation", "abundance"]
        } else {
            sme = await synthesize_from_params()
            sme_loaded_from_file = true
            updated_sections = ["spectrum", "citation", "parameters", "linelist", "atmosphere", "abundance"]
        }
        if (active_entry) {
            active_entry.sme = sme
            active_entry.updated_at = Date.now()
            active_entry.source = "file"
        }
        cast_load_event(sme, ...updated_sections)
        render_spectrum_list()
        show_synthesis_status("Synthesis Completed", true)
        setTimeout(() => { hide_synthesis_status() }, 2000)
    } catch (err) {
        console.error(err)
        show_synthesis_status("Synthesis Failed", true)
        setTimeout(() => { hide_synthesis_status() }, 2000)
    }
})

var ButtonFit = document.getElementById("btn-fit")
ButtonFit.addEventListener('click', async (event) => {
    console.log("Start fitting")
    clear_alerts()
    show_synthesis_status("Fitting...", true)
    try {
        if (spectrum_store.mode !== "fit") {
            show_error("Switch to Fit mode before running a fit.")
            hide_synthesis_status()
            return
        }
        let target_id = spectrum_store.fit_target_id || spectrum_store.active_id
        if (!target_id) {
            show_error("Please choose a fit target.")
            hide_synthesis_status()
            return
        }
        let target_entry = spectrum_store.spectra.find((entry) => entry.id === target_id)
        if (!target_entry || !target_entry.sme) {
            show_error("Fit target has no spectrum data.")
            hide_synthesis_status()
            return
        }
        if (sme && target_entry.sme !== sme) {
            sync_fit_params_from_active(sme, target_entry.sme)
        }
        if (!target_entry.sme.spec || (Array.isArray(target_entry.sme.spec) && target_entry.sme.spec.length === 0)) {
            if (target_entry.sme.synth && (Array.isArray(target_entry.sme.synth) && target_entry.sme.synth.length > 0)) {
                target_entry.sme.spec = target_entry.sme.synth
            } else {
                show_error("Fit target has no observation spectrum.")
                hide_synthesis_status()
                return
            }
        }
        let linelist_data = target_entry.sme.linelist ? target_entry.sme.linelist.data : null
        if (!Array.isArray(linelist_data) || linelist_data.length === 0) {
            show_error("Please load a linelist before fitting.")
            hide_synthesis_status()
            return
        }
        let wlcent_values = linelist_data
            .map((row: any) => Number(row && row.wlcent))
            .filter((v: number) => Number.isFinite(v))
        if (wlcent_values.length === 0) {
            show_error("Linelist has no wlcent values. Please reload a valid linelist.")
            hide_synthesis_status()
            return
        }
        console.log("Fit debug - wran:", target_entry.sme.header.wran, "linelist wlcent min/max:", Math.min(...wlcent_values), Math.max(...wlcent_values))
        let wran_min = Infinity
        let wran_max = -Infinity
        target_entry.sme.header.wran.forEach((range: any) => {
            let start = Number(range[0])
            let end = Number(range[1])
            if (!Number.isFinite(start) || !Number.isFinite(end)) return
            if (start < wran_min) wran_min = start
            if (end > wran_max) wran_max = end
        })
        let has_lines_in_range = wlcent_values.some((wl) => wl >= wran_min && wl <= wran_max)
        if (!has_lines_in_range) {
            show_error("Linelist has no lines within the wavelength range. Please load a matching linelist.")
            hide_synthesis_status()
            return
        }
        if (!target_entry.sme.wave || (Array.isArray(target_entry.sme.wave) && target_entry.sme.wave.length === 0)) {
            show_error("Fit target has no wavelength grid.")
            hide_synthesis_status()
            return
        }
        if (!target_entry.sme.header.wran || target_entry.sme.header.wran.length === 0) {
            show_error("Fit target has no wavelength range.")
            hide_synthesis_status()
            return
        }
        function normalize_fitparameter(name: string) {
            let value = name.trim().toLowerCase().replace(/\s+/g, "")
            if (value === "log(g)") return "logg"
            if (value === "[m/h]" || value === "m/h" || value === "mh") return "monh"
            if (value === "gamma6") return "gam6"
            return value
        }
        let fitparams_raw = (target_entry.sme.header.fitparameters || [])
            .map((p) => normalize_fitparameter(String(p)))
            .filter((p) => p.length > 0)
        if (fitparams_raw.length === 0) {
            show_error("Please set fit parameters (e.g., teff, logg, monh) before fitting.")
            hide_synthesis_status()
            return
        }
        let invalid = fitparams_raw.find((p) => Number.isFinite(Number(p)))
        if (invalid) {
            show_error("Fit parameters must be names (e.g., teff, logg, monh), not numbers.")
            hide_synthesis_status()
            return
        }
        target_entry.sme.header.fitparameters = fitparams_raw
        let fit_result = await fit_spectrum(target_entry.sme)
        console.log("Fit mask summary - input:", summarize_mask(target_entry.sme.mask), "output:", summarize_mask(fit_result.mask))
        console.log("Fit mask input raw:", target_entry.sme.mask)
        console.log("Fit mask output raw:", fit_result.mask)
        let fit_entry = add_fit_result_spectrum(spectrum_store, undefined, fit_result)
        activate_spectrum_entry(fit_entry)
        render_spectrum_list()
        show_synthesis_status("Fit Finished", true)
        setTimeout(() => { hide_synthesis_status() }, 2000)
    } catch (err) {
        console.error(err)
        show_synthesis_status("Fit Failed", true)
        setTimeout(() => { hide_synthesis_status() }, 2000)
    }
})

function show_synthesis_status(message: string, allow_close: boolean) {
    let box = document.getElementById("div-synthesis-status") as HTMLDivElement
    let msg = document.getElementById("synthesis-status-message") as HTMLDivElement
    let btn = document.getElementById("btn-synthesis-status-close") as HTMLButtonElement
    msg.textContent = message
    box.style.display = "block"
    btn.style.display = allow_close ? "block" : "none"
    if (message === "Synthesis Completed") {
        box.classList.add("synthesis-status-success")
    } else {
        box.classList.remove("synthesis-status-success")
    }
}

function hide_synthesis_status() {
    let box = document.getElementById("div-synthesis-status") as HTMLDivElement
    box.style.display = "none"
}

var BtnSynthesisStatusClose = document.getElementById("btn-synthesis-status-close") as HTMLButtonElement
BtnSynthesisStatusClose.addEventListener("click", () => {
    hide_synthesis_status()
})

