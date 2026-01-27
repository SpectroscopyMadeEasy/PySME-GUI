

const { shell } = require('electron')
var LinelistTable: any = null

addEventListener("pysme_load", (event: any) => {
    let sme: SmeFile = event.detail.structure
    let updated: string[] = event.detail.updated
    if (!updated.length || updated.includes("linelist")) {
        show_linelist(sme)
        return
    }
    // After synthesize, header/body can misalign; re-adjust if table exists
    if (updated.includes("spectrum") && LinelistTable) {
        adjust_linelist_table(LinelistTable)
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
            let store: any = (window as any).spectrum_store
            if (store && Array.isArray(store.spectra)) {
                store.spectra.forEach((entry: any) => {
                    if (!entry || !entry.sme) return
                    entry.sme.linelist = { header: sme.linelist.header, data: sme.linelist.data }
                    if (!entry.sme.system_info) {
                        entry.sme.system_info = { header: {} }
                    }
                    if (!entry.sme.system_info.header) {
                        entry.sme.system_info.header = {}
                    }
                    entry.sme.system_info.header["linelist_file"] = fname
                })
            }
            cast_load_event(sme, "linelist")
        } catch (err) {
            console.error(err)
        }
    }
})

var ButtonLinelistRequestVald = document.getElementById("btn-linelist-request-vald") as HTMLButtonElement
ButtonLinelistRequestVald.addEventListener("click", (event) => {
    shell.openExternal("https://vald.astro.uu.se/")
})

function show_linelist(sme: SmeFile) {
    let rows: any[] = sme.linelist.data || []
    let columns: any[] = []
    let referenceIndex = -1
    let wlcentIndex = -1

    let keySet = new Set<string>()
    rows.forEach((row) => {
        if (!row) return
        Object.keys(row).forEach((k) => keySet.add(k))
    })
    let keys = Array.from(keySet)
    columns = keys.map((key) => {
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
        if (key === "wlcent") {
            wlcentIndex = columns.length
            column.type = "num"
        }
        return column
    })

    if (columns.length === 0) {
        return
    }

    let tableEl = $('#table-linelist')
    if (LinelistTable) {
        LinelistTable.destroy(false)
        LinelistTable = null
    }
    tableEl.empty()
    let thead = $('<thead><tr></tr></thead>')
    keys.forEach((key) => {
        thead.find('tr').append(`<th>${key}</th>`)
    })
    tableEl.append(thead)
    tableEl.append('<tbody></tbody>')

    let columnDefs: any[] = []
    if (referenceIndex >= 0) {
        columnDefs.push({
            targets: referenceIndex,
            width: "260px",
            className: "linelist-reference"
        })
    }
    if (wlcentIndex >= 0) {
        columnDefs.push({
            targets: wlcentIndex,
            type: "num"
        })
    }

    let table = tableEl.DataTable({
        destroy: true,
        responsive: false,
        scrollX: false,
        scrollCollapse: false,
        autoWidth: true,
        pageLength: 10,
        data: rows,
        columns: columns,
        order: wlcentIndex >= 0 ? [[wlcentIndex, "asc"]] : [],
        columnDefs: columnDefs
    });
    LinelistTable = table
    move_linelist_controls(table)
    // Ensure header/body alignment after render and on layout changes
    setTimeout(() => { adjust_linelist_table(table); }, 0);
    setTimeout(() => { adjust_linelist_table(table); }, 200);
    setTimeout(() => { adjust_linelist_table(table); }, 500);
    $(window).off("resize.linelist").on("resize.linelist", () => {
        if (LinelistTable) adjust_linelist_table(LinelistTable);
    });
}

function move_linelist_controls(table: any) {
    let wrapper = $(table.table().container())
    if (wrapper.length === 0) return
    let top = $('#linelist-controls-top')
    let bottom = $('#linelist-controls-bottom')
    if (top.length === 0 || bottom.length === 0) return
    top.empty()
    bottom.empty()
    top.append(wrapper.find('.dataTables_length'))
    top.append(wrapper.find('.dataTables_filter'))
    bottom.append(wrapper.find('.dataTables_info'))
    bottom.append(wrapper.find('.dataTables_paginate'))
}

function adjust_linelist_table(table: any) {
    let container = $(table.table().container());
    container.find("div.dataTables_scrollHeadInner, div.dataTables_scrollHeadInner > table").css("width", "");
    container.find("div.dataTables_scrollBody > table").css("width", "");
    table.columns.adjust().draw(false);
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
