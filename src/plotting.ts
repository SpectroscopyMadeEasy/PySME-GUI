import { Main } from "electron";

// Define the colors and linestyles to use in the plot
var fmt = {
    Obs: { color: "#1f77b4", linestyle: "solid" },
    Syn: { color: "#ff7f0e", linestyle: "solid", marker: "" },
    LineMask: { facecolor: "#bcbd22", alpha: 0.3 },
    ContMask: { facecolor: "#d62728", alpha: 0.3 },
    fontsize: 16,
}

function hex_to_rgba(hex: string, alpha: number) {
    let value = hex.replace("#", "")
    if (value.length === 3) {
        value = value.split("").map((c) => c + c).join("")
    }
    let r = parseInt(value.slice(0, 2), 16)
    let g = parseInt(value.slice(2, 4), 16)
    let b = parseInt(value.slice(4, 6), 16)
    return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

type mask_modes = "bad" | "line" | "cont";
var mask_mode: mask_modes = "cont"
var initial_setup = true
var graphDiv: any = document.getElementById('div-graph') as HTMLDivElement;
var current_plot_sme: SmeFile | null = null;
var data: any = []
var layout: any = {}
var data_index: { [id: string]: { [id: number]: number } } = {
    linemask: {},
    contmask: {},
    spec: {},
    synth: {}
}
// Not showing annotations is a lot faster
var show_annotations = true;

addEventListener("pysme_load", (event: any) => {
    let sme: SmeFile = event.detail.structure
    let updated: string[] = event.detail.updated
    if (!updated.length || updated.includes("spectrum") || updated.includes("linelist")) {
        plot_sme(sme)
    }
})

function reset_plot() {
    initial_setup = true
    data = []
    layout = {}
    data_index = {
        linemask: {},
        contmask: {},
        spec: {},
        synth: {}
    }
}

function get_mask_value() {
    if (mask_mode == "bad") return 0
    if (mask_mode == "line") return 1
    if (mask_mode == "cont") return 2
}

function get_segment() {
    var seg: number = layout.sliders[0].active
    return seg
}

function get_data_index(seg: number, type: "linemask" | "contmask" | "spec" | "synth") {
    return data_index[type][seg]
}

function set_mask(sme: SmeFile, range: [number, number]) {
    var value = get_mask_value()
    var seg = get_segment()

    var wave = sme.wave[seg]
    var wmin = range[0]
    var wmax = range[1]

    if (typeof (sme.mask[seg][0]) === "bigint") {
        let mask = sme.mask[seg] as BigInt64Array
        let big_value = BigInt(value)
        sme.mask[seg] = mask.map((v, i) => {
            if ((wave[i] >= wmin) && (wave[i] <= wmax)) {
                return big_value
            } else {
                return v
            }
        })
    } else {
        let mask2 = sme.mask[seg] as Int8Array
        sme.mask[seg] = mask2.map((v, i) => {
            if ((wave[i] >= wmin) && (wave[i] <= wmax)) {
                return value
            } else {
                return v
            }
        })
    }

    var idx = get_data_index(seg, "linemask")
    var [x, y] = create_mask_points(
        sme.wave[seg], sme.spec[seg], sme.mask[seg], 1
    )
    data[idx].x = x
    data[idx].y = y

    var idx = get_data_index(seg, "contmask")
    var [x, y] = create_mask_points(
        sme.wave[seg], sme.spec[seg], sme.mask[seg], 2
    )

    data[idx].x = x
    data[idx].y = y

    layout.datarevision = layout.datarevision + 1
    Plotly.react(graphDiv, data, layout)
}

function create_mask_points(x: FloatArray, y: FloatArray, mask: IntArray, value: number) {
    // Creates the points that define the outer edge of the mask region
    y = y.map((v: number, i: number) => {
        if (mask[i] == value) {
            return v
        } else {
            return 0
        }
    })

    x = x.map((v: number, i: number, a: FloatArray) => {
        if ((i == 0) || (i == a.length - 1)) {
            return v
        } else {
            if ((mask[i] != value) && (mask[i + 1] == value)) {
                return a[i + 1]
            } else {
                if ((mask[i] != value) && (mask[i - 1] == value)) {
                    return a[i - 1]
                } else {
                    return v
                }
            }
        }
    })

    return [x, y]
}

function plot_sme(sme: any) {
    let store: SpectrumStoreState | null = (window as any).spectrum_store || null
    let active_entry: SpectrumEntry | null = null
    if (store && store.spectra.length > 0) {
        active_entry = store.spectra.find((entry) => entry.id === store.active_id) || store.spectra[0]
    }
    let base_sme = (active_entry && active_entry.sme) ? active_entry.sme : sme
    if (!base_sme || !base_sme.wave || base_sme.wave.length === 0) {
        reset_plot()
        return
    }
    current_plot_sme = base_sme
    reset_plot()
    var nSegment = base_sme.wave.length

    // Create Plot
    var annotations: any = {};
    var visible: any = [];
    var line_mask_idx: { [id: number]: any } = {};
    var cont_mask_idx: { [id: number]: any } = {};
    var steps = []
    var counter = 0;


    for (let seg = 0; seg < nSegment; seg++) {
        var k = visible.length
        line_mask_idx[seg] = k
        cont_mask_idx[seg] = k + 1
        // The order of the plots is chosen by the z order, from low to high
        // Masks should be below the spectra (so they don't hide half of the line)
        // Synthetic on top of observation, because synthetic varies less than observation
        // Annoying I know, but plotly doesn't seem to have good controls for the z order
        // Or Legend order for that matter

        if (base_sme.mask) {
            // Line mask
            var [x, y] = create_mask_points(
                base_sme.wave[seg], base_sme.spec[seg], base_sme.mask[seg], 1
            )

            data.push({
                type: "scattergl",
                x: x,
                y: y,
                fillcolor: hex_to_rgba(fmt["LineMask"]["facecolor"], fmt["LineMask"]["alpha"]),
                fill: "tozeroy",
                mode: "none",
                name: "Line Mask",
                hoverinfo: "none",
                legendgroup: 2,
                visible: 0 == seg,
            })
            visible.push(seg)
            data_index["linemask"][seg] = counter
            counter += 1

            // Cont mask
            var [x, y] = create_mask_points(
                base_sme.wave[seg], base_sme.spec[seg], base_sme.mask[seg], 2
            )

            data.push(
                {
                    type: "scattergl",
                    x: x,
                    y: y,
                    fillcolor: hex_to_rgba(fmt["ContMask"]["facecolor"], fmt["ContMask"]["alpha"]),
                    fill: "tozeroy",
                    mode: "none",
                    name: "Continuum Mask",
                    hoverinfo: "none",
                    legendgroup: 2,
                    visible: 0 == seg,
                }
            )
            visible.push(seg)
            data_index["contmask"][seg] = counter
            counter += 1
        }

        let spectra: SpectrumEntry[] = store ? store.spectra : [{
            id: "single",
            name: "Synthetic",
            kind: "synth",
            source: "file",
            created_at: 0,
            updated_at: 0,
            color: fmt["Syn"]["color"],
            locked: false,
            sme: base_sme,
        }]
        for (let idx = 0; idx < spectra.length; idx++) {
            const entry = spectra[idx]
            if (!entry.sme || !entry.sme.wave || !entry.sme.wave[seg]) continue
            let entry_sme = entry.sme
            let is_active = !store || entry.id === (store as SpectrumStoreState).active_id
            let opacity = is_active ? 1 : 0.3
            let line_color = entry.color || (entry.kind === "obs" ? fmt["Obs"]["color"] : fmt["Syn"]["color"])
            if (entry.kind === "obs" && entry_sme.spec) {
                data.push({
                    type: "scattergl",
                    x: entry_sme.wave[seg],
                    y: entry_sme.spec[seg],
                    line: { "color": line_color },
                    mode: "lines+markers",
                    marker: { size: 3 },
                    name: entry.name,
                    legendgroup: idx + 1,
                    opacity: opacity,
                    visible: 0 == seg,
                })
                visible.push(seg)
                if (is_active) {
                    data_index["spec"][seg] = counter
                }
                counter += 1
            }
            if (entry.kind === "synth" && entry_sme.synth) {
                let data_synth: FloatArray;
                if (entry_sme.telluric) {
                    data_synth = new Float64Array(entry_sme.synth[seg].length)
                    for (let i = 0; i < entry_sme.synth[seg].length; i++) {
                        data_synth[i] = entry_sme.synth[seg][i] * entry_sme.telluric[seg][i];
                    }
                } else {
                    data_synth = entry_sme.synth[seg]
                }
                data.push({
                    type: "scattergl",
                    x: entry_sme.wave[seg],
                    y: data_synth,
                    name: entry.name,
                    line: { "color": line_color },
                    mode: "lines+markers",
                    marker: { size: 3 },
                    legendgroup: idx + 1,
                    opacity: opacity,
                    visible: 0 == seg,
                })
                visible.push(seg)
                if (is_active) {
                    data_index["synth"][seg] = counter
                }
                counter += 1
            }
        }
        if (show_annotations && base_sme.linelist.data) {
            var lines = base_sme.linelist.data
            var wmin: number = base_sme.wave[seg][0]
            var wmax: number = base_sme.wave[seg][base_sme.wave[seg].length - 1]
            var vrad = 0
            if (base_sme.header.vrad) vrad = base_sme.header.vrad[seg]
            wmin *= 1 - vrad / 3e5
            wmax *= 1 - vrad / 3e5

            var seg_annotations: any = []

            let lines_wlcent: number[] = []
            let lines_labels: string[] = []
            for (let key = 0; key < lines.length; key++) {
                const element: number = lines[key]["wlcent"];
                if ((element > wmin) && (element < wmax)) {
                    lines_wlcent.push(element)
                    lines_labels.push(lines[key].species)
                }
            }

            // # Filter out closely packaged lines of the same species
            // # Threshold for the distance between lines
            let wlcent: number[] = []
            let labels: string[] = []
            let threshold = (wmax - wmin) / 100
            let species: string[] = lines_labels.filter((v: string, i: number, a: string[]) => a.indexOf(v) === i)

            for (let key = 0; key < species.length; key++) {
                const sp = species[key]
                let sp_lines: number[] = lines_wlcent.filter((v: number, i: number, a: number[]) => lines_labels[i] == sp)
                let sp_mask: boolean[] = []
                for (let k2 = 1; k2 < sp_lines.length; k2++) {
                    sp_mask.push(sp_lines[k2] - sp_lines[k2 - 1] < threshold)
                }
                sp_mask.push(false)
                if (sp_mask.some((v) => v)) {
                    let j = 0
                    for (j = 0; (j < sp_lines.length) && !(sp_mask[j]); j++) { }

                    for (let i = j; i < sp_lines.length; i++) {
                        j = i
                        let sp_wmid = 0;
                        for (j = i; (j < sp_lines.length) && (sp_mask[j]); j++) {
                            sp_wmid += sp_lines[j]
                        }
                        sp_wmid /= Math.max((j - i), 1)
                        if (sp_wmid != 0){
                            wlcent.push(sp_wmid)
                            labels.push(sp + " +" + (j - i).toString())
                        }
                        i = j
                    }
                } else {
                    for (let k2 = 0; k2 < sp_lines.length; k2++) {
                        wlcent.push(sp_lines[k2])
                        labels.push(sp)
                    }
                }
            }

            // console.log(labels)
            // console.log(wlcent)

            for (let key = 0; key < wlcent.length; key++) {
                var x_loc = wlcent[key] * (1 + vrad / 3e5)
                var y_loc = 1
                var ay = 1.3;

                // Find the clostest data point
                var i = 0;
                var idx = -1;
                var closest = base_sme.wave[seg].reduce((acc: number, value: number) => {
                    i += 1;
                    if (Math.abs(value - x_loc) < acc) {
                        idx = i;
                        return Math.abs(value - x_loc)
                    } else {
                        return acc
                    }
                }, wmax)

                if (idx != -1) {
                    x_loc = base_sme.wave[seg][idx]

                    if (base_sme.synth) {
                        y_loc = base_sme.synth[seg][idx]
                        ay = base_sme.synth[seg].reduce((p:number, c:number) => {
                            if (p > c) {
                                return p;
                            }
                            return c;
                        })
                        ay = ay * 1.1;
                    } else {
                        if (base_sme.spec) {
                            y_loc = base_sme.spec[seg][idx]
                            ay = base_sme.spec[seg].reduce((p:number, c:number) => {
                                if (p > c) {
                                    return p;
                                }
                                return c;
                            })
                            ay = ay * 1.1;
                        }
                    }
                }

                seg_annotations.push({
                    x: x_loc,
                    y: y_loc,
                    xref: "x",
                    yref: "y",
                    text: labels[key],
                    hovertext: wlcent[key],
                    textangle: 90,
                    opacity: 1,
                    ax: 0,
                    ay: ay,
                    ayref: "y",
                    showarrow: true,
                    arrowhead: 7,
                    xanchor: "left",
                    font: { size: fmt["fontsize"] }
                })
            }
            annotations[seg] = seg_annotations
        }


    }
    for (let seg = 0; seg < nSegment; seg++) {
        // Slider Steps
        var visibility = Array.apply(null, Array(visible.length)).map(function (x: any, i: number) { return visible[i] == seg })
        var wmin: number = base_sme.wave[seg][0]
        var wmax: number = base_sme.wave[seg][base_sme.wave[seg].length - 1]
        var step = {
            label: `Segment ${seg}`,
            method: "update",
            args: [
                { visible: visibility },
                {
                    annotations: annotations[seg],
                    xaxis: { range: [wmin, wmax], title: "Wavelength [Å]" },
                    yaxis: { autorange: true, title: "Intensity" },
                },
            ],
        }
        steps.push(step)
    }

    var slider = {
        pad: {
            t: 30
        },
        active: 0,
        steps: steps,
    }

    layout = {
        xaxis: { title: "Wavelength [Å]" },
        yaxis: { title: "Intensity" },
        showlegend: true,
        legend: { traceorder: "reversed" },
        font: { family: "Open Sans, sans-serif", size: fmt["fontsize"] },
        selectdirection: "h",
        sliders: [slider],
        annotations: annotations[0]
    }

    var buttons = [["toImage", "zoom2d", "pan2d", "select2d", "zoomIn2d", "zoomOut2d", "autoScale2d", "resetScale2d"]];
    Plotly.react(graphDiv, data, layout, { responsive: true, displayModeBar: true, modeBarButtons: buttons });

    if (initial_setup) {
        graphDiv.on('plotly_selected', (event: any) => {
            if (event) {
                var range: [number, number] = event.range.x
                if (current_plot_sme) {
                    set_mask(current_plot_sme, range)
                }
            }
        })
        initial_setup = false
    }
}

var ButtonMaskLine = document.getElementById("btn-mask-line")
ButtonMaskLine.addEventListener('click', () => {
    mask_mode = "line"
})


var ButtonMaskCont = document.getElementById("btn-mask-cont")
ButtonMaskCont.addEventListener('click', () => {
    mask_mode = "cont"
})


var ButtonMaskBad = document.getElementById("btn-mask-bad")
ButtonMaskBad.addEventListener('click', () => {
    mask_mode = "bad"
})
