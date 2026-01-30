
const path = require("path")

addEventListener("pysme_load", (event: any) => {
    let sme: SmeFile = event.detail.structure
    let updated: string[] = event.detail.updated
    if (!updated.length || updated.includes("abundance")) {
        update_abundance(sme)
    }
})

let abundance_baseline: Float64Array | null = null
let abundance_baseline_model: string | null = null

async function fetch_abundance_values(model: string): Promise<Float64Array | null> {
    let tmpout = tmp.fileSync({ postfix: ".dat" })
    let success = await call_python("get_abundance_values.py", [tmpout.name, model])
    if (!success) return null
    var content: Buffer = fs.readFileSync(tmpout.name, { encoding: null })
    return new Float64Array(content.buffer, content.byteOffset, content.length / Float64Array.BYTES_PER_ELEMENT)
}

function update_abundance_highlights() {
    for (let i = 0; i < elements.length; i++) {
        const element = elements[i]
        const cell = document.getElementById(`par-abund-${element}`)
        if (!cell) continue
        cell.classList.remove("abundance-modified")
        if (!abundance_baseline || !abundance_baseline_model || abundance_baseline_model === "empty") continue
        const baseline = Number(abundance_baseline[i])
        const current = Number(sme.abund.data[i])
        const baseline_ok = Number.isFinite(baseline)
        const current_ok = Number.isFinite(current)
        if (!baseline_ok && current_ok) {
            cell.classList.add("abundance-modified")
            continue
        }
        if (!baseline_ok || !current_ok) continue
        if (Math.abs(current - baseline) > 1e-6) {
            cell.classList.add("abundance-modified")
        }
    }
}

async function apply_abundance_model(model: string, values?: Float64Array | null) {
    console.log("Resetting Abundance to " + model)
    let abund = values || await fetch_abundance_values(model)
    if (!abund) return
    sme.abund.data = abund
    sme.abund.header["format"] = "H=12"
    sme.abund.header["monh"] = 0
    cast_load_event(sme, "abundance")
}

const SelectAbundSolar = document.getElementById("select-abund-solar") as HTMLSelectElement | null
const RefreshAbundSolar = document.getElementById("btn-abund-refresh")
const RestoreAbundSolar = document.getElementById("btn-abund-restore")

function get_abundance_cache_path(): string | null {
    try {
        const electron = require("electron")
        const app = electron?.remote?.app
        if (app && typeof app.getPath === "function") {
            return path.join(app.getPath("userData"), "abundance_models.json")
        }
    } catch (err) {
        console.warn("Could not resolve userData cache path for abundance models", err)
    }
    return null
}

function format_abundance_label(model: string): string {
    if (!model) return model
    return model.charAt(0).toUpperCase() + model.slice(1)
}

function populate_abundance_select(models: string[], preferValue?: string) {
    if (!SelectAbundSolar) return
    const previous = preferValue || SelectAbundSolar.value
    SelectAbundSolar.innerHTML = ""
    for (const model of models) {
        const option = document.createElement("option")
        option.value = model
        option.textContent = format_abundance_label(model)
        SelectAbundSolar.appendChild(option)
    }
    if (previous && models.includes(previous)) {
        SelectAbundSolar.value = previous
    } else if (models.includes("asplund2009")) {
        SelectAbundSolar.value = "asplund2009"
    } else if (models.length) {
        SelectAbundSolar.value = models[0]
    }
}

async function fetch_abundance_models(): Promise<string[] | null> {
    let tmpout = tmp.fileSync({ postfix: ".json" })
    let success = await call_python("list_abundance_models.py", [tmpout.name])
    if (!success) return null
    try {
        const raw = fs.readFileSync(tmpout.name, { encoding: "utf8" })
        const parsed = JSON.parse(raw)
        if (Array.isArray(parsed?.models)) {
            return parsed.models.map((m: string) => m.toLowerCase())
        }
        if (Array.isArray(parsed)) {
            return parsed.map((m: string) => String(m).toLowerCase())
        }
    } catch (err) {
        console.warn("Failed to parse abundance models list", err)
    }
    return null
}

function read_abundance_cache(): string[] | null {
    const cache_path = get_abundance_cache_path()
    if (!cache_path || !fs.existsSync(cache_path)) return null
    try {
        const raw = fs.readFileSync(cache_path, { encoding: "utf8" })
        const parsed = JSON.parse(raw)
        if (Array.isArray(parsed?.models)) {
            return parsed.models.map((m: string) => m.toLowerCase())
        }
    } catch (err) {
        console.warn("Failed to read abundance model cache", err)
    }
    return null
}

function write_abundance_cache(models: string[]) {
    const cache_path = get_abundance_cache_path()
    if (!cache_path) return
    try {
        fs.writeFileSync(cache_path, JSON.stringify({ models, updatedAt: new Date().toISOString() }, null, 2))
    } catch (err) {
        console.warn("Failed to write abundance model cache", err)
    }
}

async function ensure_abundance_baseline(model: string): Promise<Float64Array | null> {
    if (!model || model.toLowerCase() === "empty") {
        abundance_baseline = null
        abundance_baseline_model = model
        return null
    }
    if (abundance_baseline && abundance_baseline_model === model) {
        return abundance_baseline
    }
    const values = await fetch_abundance_values(model)
    abundance_baseline = values ? new Float64Array(values) : null
    abundance_baseline_model = model
    return values
}

async function load_abundance_models(force_refresh = false): Promise<string[]> {
    let models: string[] | null = null
    if (!force_refresh) {
        models = read_abundance_cache()
    }
    if (!models || !models.length) {
        models = await fetch_abundance_models()
    }
    if (!models || !models.length) {
        models = ["asplund2009", "grevesse2007", "lodders2003", "empty"]
    }
    models = models.map((m) => m.toLowerCase()).filter((m) => m !== "solar")
    write_abundance_cache(models)
    populate_abundance_select(models)
    return models
}
if (SelectAbundSolar) {
    SelectAbundSolar.addEventListener("change", async () => {
        let model = SelectAbundSolar.value
        if (!model) return
        const values = await ensure_abundance_baseline(model)
        await apply_abundance_model(model, values)
        update_abundance_highlights()
    })
    if (RefreshAbundSolar) {
        RefreshAbundSolar.addEventListener("click", async () => {
            await load_abundance_models(true)
        })
    }
    if (RestoreAbundSolar) {
        RestoreAbundSolar.addEventListener("click", async () => {
            const model = SelectAbundSolar.value
            if (!model) return
            const values = await ensure_abundance_baseline(model)
            await apply_abundance_model(model, values)
            update_abundance_highlights()
        })
    }
    // Default to Asplund2009 on startup if no abundance values are set yet.
    const has_values = () => {
        if (!sme || !sme.abund || !sme.abund.data) return false
        for (let i = 0; i < sme.abund.data.length; i++) {
            let v = Number(sme.abund.data[i])
            if (Number.isFinite(v) && v !== 0) return true
        }
        return false
    }
    load_abundance_models(false).then(async () => {
        if (!SelectAbundSolar.value) {
            SelectAbundSolar.value = "asplund2009"
        }
        const model = SelectAbundSolar.value || "asplund2009"
        const values = await ensure_abundance_baseline(model)
        if (!has_values()) {
            await apply_abundance_model(model, values)
        }
        update_abundance_highlights()
    })
}

function update_abundance(sme: SmeFile) {
    for (let i = 0; i < elements.length; i++) {
        const element = elements[i];
        let field = document.getElementById(`par-abund-input-${element}`) as HTMLInputElement
        let value = sme.abund.data[i]
        if (value === undefined || value === null || isNaN(value)) {
            field.value = ""
        } else {
            field.value = String(value)
        }
    }
    update_abundance_highlights()
}

// All the elements that can be used in SME (the first 100)
var elements = [
    "H", "He",
    "Li", "Be", "B", "C", "N", "O", "F", "Ne",
    "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar",
    "K", "Ca", "Sc", "Ti", "V", "Cr", "Mn", "Fe",
    "Co", "Ni", "Cu", "Zn", "Ga", "Ge", "As", "Se",
    "Br", "Kr", "Rb", "Sr", "Y", "Zr", "Nb", "Mo",
    "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn",
    "Sb", "Te", "I", "Xe", "Cs", "Ba", "La", "Ce",
    "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb", "Dy",
    "Ho", "Er", "Tm", "Yb", "Lu", "Hf", "Ta", "W",
    "Re", "Os", "Ir", "Pt", "Au", "Hg", "Tl", "Pb",
    "Bi", "Po", "At", "Rn", "Fr", "Ra", "Ac", "Th",
    "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf",
    "Es",]

function clear_abundance_inputs_if_nan() {
    for (let i = 0; i < elements.length; i++) {
        const element = elements[i]
        let field = document.getElementById(`par-abund-input-${element}`) as HTMLInputElement
        if (field && field.value.trim().toLowerCase() === "nan") {
            field.value = ""
        }
    }
}

// index of each element in the pattern data array
var elements_dict: { [id: string]: number } = {}
for (let i = 0; i < elements.length; i++) {
    const element = elements[i];
    elements_dict[element] = i
}

clear_abundance_inputs_if_nan()

for (let i = 0; i < elements.length; i++) {
    const element = elements[i];
    let field = document.getElementById(`par-abund-input-${element}`)
    field.addEventListener("change", async (event: any) => {
        let i = elements_dict[element]
        let raw = String(event.target.value).trim()
        if (raw === "") {
            sme.abund.data[i] = NaN
        } else {
            let value = Number(raw)
            sme.abund.data[i] = value
        }
        const model = SelectAbundSolar?.value || ""
        if (!abundance_baseline || abundance_baseline_model !== model) {
            await ensure_abundance_baseline(model)
        }
        update_abundance_highlights()
    })
}
