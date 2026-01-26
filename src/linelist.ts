

addEventListener("pysme_load", (event: any) => {
    let sme: SmeFile = event.detail.structure
    let updated: string[] = event.detail.updated
    if (!updated.length || updated.includes("linelist")) {
        show_linelist(sme)
    }
})


var ButtonLinelistLoad = document.getElementById("btn-linelist-load") as HTMLButtonElement
ButtonLinelistLoad.addEventListener("click", async (event) => {
    console.log("load Linelist")
    var out = await dialog.showOpenDialog({ properties: ["openFile"] })
    if (!out.canceled) {
        var fname = out.filePaths[0];
        try {
            sme = await load_new_linelist(sme, fname)
            if (!sme.system_info) {
                sme.system_info = { header: {} }
            }
            if (!sme.system_info.header) {
                sme.system_info.header = {}
            }
            sme.system_info.header["linelist_file"] = fname
            cast_load_event(sme, "linelist")
        } catch (err) {
            console.error(err)
        }
    }
})

var ButtonLinelistRequestVald = document.getElementById("btn-linelist-request-vald") as HTMLButtonElement
ButtonLinelistRequestVald.addEventListener("click", (event) => {
    //TODO
})

function show_linelist(sme: SmeFile) {
    $(document).ready(function () {
        let rows: any[] = sme.linelist.data || []
        let columns: any[] = []
        let referenceIndex = -1
        if (rows.length > 0) {
            columns = Object.keys(rows[0]).map((key) => {
                let column: any = { title: key, data: key }
                if (key === "reference") {
                    referenceIndex = columns.length
                    column.className = "linelist-reference"
                    column.render = function (data: any, type: string) {
                        if (data === null || data === undefined) return ""
                        let text = String(data).replace(/\s+/g, " ").trim()
                        if (type === "display") {
                            return `<span class="linelist-truncate" title="${text.replace(/\"/g, '&quot;')}">${text}</span>`
                        }
                        return text
                    }
                }
                if (key === "term_lower" || key === "term_upper" || key === "term_higher") {
                    column.render = function (data: any, type: string) {
                        if (data === null || data === undefined) return ""
                        let text = String(data).replace(/\s+/g, " ").trim()
                        if (type === "display") {
                            return `<span class="linelist-truncate" title="${text.replace(/\"/g, '&quot;')}">${text}</span>`
                        }
                        return text
                    }
                }
                return column
            })
        }
        $('#table-linelist').DataTable({
            destroy: true,
            responsive: false,
            scrollX: true,
            scrollCollapse: true,
            autoWidth: false,
            data: rows,
            columns: columns,
            columnDefs: referenceIndex >= 0 ? [{
                targets: referenceIndex,
                width: "260px",
                className: "linelist-reference"
            }] : []
        });
    });
}

async function load_new_linelist(sme: SmeFile, linelist_file: string) {
    var tmpout = tmp.fileSync({ postfix: ".json" });
    var success = await call_python("load_new_linelist.py", [linelist_file, tmpout.name, "--format=VALD"])
    var data = fs.readFileSync(tmpout.name, { encoding: "utf-8" })
    var json = JSON.parse(data)
    sme.linelist.data = json.data
    sme.linelist.header = json.info
    return sme
}
