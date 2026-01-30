
addEventListener("pysme_load", (event: any) => {
    let sme: SmeFile = event.detail.structure
    let updated: string[] = event.detail.updated
    if (!updated.length || updated.includes("abundance")) {
        update_abundance(sme)
    }
})

async function apply_abundance_model(model: string) {
    console.log("Resetting Abundance to " + model)
    let tmpout = tmp.fileSync({ postfix: ".dat" });
    let success = await call_python("get_abundance_values.py", [tmpout.name, model])
    var content: Buffer = fs.readFileSync(tmpout.name, { encoding: null })
    var abund = new Float64Array(content.buffer, content.byteOffset, content.length / Float64Array.BYTES_PER_ELEMENT)
    sme.abund.data = abund
    sme.abund.header["format"] = "H=12"
    sme.abund.header["monh"] = 0
    cast_load_event(sme, "abundance")
}

const SelectAbundSolar = document.getElementById("select-abund-solar") as HTMLSelectElement | null
if (SelectAbundSolar) {
    SelectAbundSolar.addEventListener("change", async () => {
        let model = SelectAbundSolar.value
        if (!model) return
        await apply_abundance_model(model)
    })
    // Default to Asplund2009 on startup if no abundance values are set yet.
    const has_values = () => {
        if (!sme || !sme.abund || !sme.abund.data) return false
        for (let i = 0; i < sme.abund.data.length; i++) {
            let v = Number(sme.abund.data[i])
            if (Number.isFinite(v) && v !== 0) return true
        }
        return false
    }
    if (!SelectAbundSolar.value) {
        SelectAbundSolar.value = "asplund2009"
    }
    if (!has_values()) {
        apply_abundance_model("asplund2009")
    }
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
    field.addEventListener("change", (event: any) => {
        let i = elements_dict[element]
        let raw = String(event.target.value).trim()
        if (raw === "") {
            sme.abund.data[i] = NaN
        } else {
            let value = Number(raw)
            sme.abund.data[i] = value
        }
    })
}
