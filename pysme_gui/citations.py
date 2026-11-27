"""The references of a setup, sorted by what they are for.

PySME keeps BibTeX in five places (its own, the atmosphere's, the abundance
pattern's, the line list's and the NLTE grids') and ``sme.citation()`` joins them
into one alphabetical list. Here they stay apart, and every entry keeps its key,
which is how a line of a VALD list names its references.
"""

import re

import pybtex.database
from pybtex.style.formatting.plain import Style
from pysme.linelist.vald import ValdFile

# Journal macros of the ADS that BibTeX styles leave as they are.
JOURNALS = {
    "aap": "A&A", "aaps": "A&AS", "aj": "AJ", "apj": "ApJ", "apjl": "ApJL",
    "apjs": "ApJS", "araa": "ARA&A", "jcp": "J. Chem. Phys.", "jqsrt": "JQSRT",
    "mnras": "MNRAS", "nat": "Nature", "pasp": "PASP", "physrep": "Phys. Rep.",
    "physscr": "Phys. Scr.", "pra": "Phys. Rev. A", "ssr": "Space Sci. Rev.",
}
ENTRY_KEY = re.compile(r"@\w+\s*\{\s*([^,\s]+)\s*,")
# A reference string of a VALD line holds entries "number [tag:]key", after a
# description. A key may hold hyphens (BA-J), and one entry may list several,
# each with or without its own tag ("wl:LBT,iso:K08").
_KEY = r"[\w+.-]+"
_VALUE = rf"(?:\w+:)?{_KEY}(?:,(?:\w+:)?{_KEY})*"
ENTRY = re.compile(rf"\s*\d+\s+({_VALUE})(?=[\s'])")
# Where the entries begin: at the first one tagged "wl:" or "iso:", or at the
# first tag "gf:" with the untagged wavelength entry (in the short format also
# the excitation one) in front. An untagged key must hold a letter.
_UNTAGGED = r"\d+\s+(?=[\d+.-]*[^\W\d])[\w+.-]+\s+"
START = {
    long_format: re.compile(
        rf"(?<![^\s'])(?:\d+\s+(?:wl|iso):|(?:{_UNTAGGED}){{0,{count}}}\d+\s+gf:)")
    for long_format, count in ((True, 1), (False, 2))
}
# Anywhere in the string, for strings without a tag.
LOOSE = re.compile(rf"\s\d+\s+({_VALUE})(?=[\s'])")
USES = {"wl": "wavelength", "gf": "log gf", "iso": "isotopes and hyperfine structure"}
# In the long format a line has nine references, always in this order (VALD wiki,
# "Output formats for Extract Stellar request"). The first two carry a label.
LONG_FORMAT_ORDER = (
    "wavelength", "log gf", "lower level energy", "upper level energy",
    "Landé factor", "radiative damping", "Stark damping",
    "van der Waals damping", "term designation",
)
# PySME looks these two up under another spelling (BibTeX ignores the case).
RENAMED = {"LWb": "LWb2", "LGb": "LGb2"}
# PySME puts Loss 2003 (atomic weights) in front of every abundance pattern. The
# calculation does not use that table: SMElib has atomic masses of its own.
LEFT_OUT = {"loss2003atomic"}
# PySME's own two papers, which what PySME stores does not name yet (records
# from Crossref). Each is left to PySME once it names it, found by DOI or arXiv.
PYSME_PAPERS = (
    (("10.1051/0004-6361/202659986", "2605.04007", "260504007"), r"""
@ARTICLE{Jian2026,
    author = {{Jian}, Mingjie and {Piskunov}, Nikolai and {Valenti}, Jeff and {Wang}, Ella Xi and {Thorsbro}, Brian and {J{\"o}nsson}, Henrik and {Wehrhahn}, Ansgar},
    title = "{PySME v1.0: Improved modelling of stellar spectra for survey-scale applications}",
    journal = {\aap},
    year = "2026",
    month = jul,
    volume = {711},
    eid = {A168},
    pages = {A168},
    doi = {10.1051/0004-6361/202659986},
    archivePrefix = {arXiv},
    eprint = {2605.04007},
    primaryClass = {astro-ph.IM}
}
"""),
    (("10.1051/0004-6361/202244482", "2023A&A...671A.171W"), r"""
@ARTICLE{2023A&A...671A.171W,
    author = {{Wehrhahn}, A. and {Piskunov}, N. and {Ryabchikova}, T.},
    title = "{PySME. Spectroscopy Made Easier}",
    journal = {\aap},
    year = "2023",
    month = mar,
    volume = {671},
    eid = {A171},
    pages = {A171},
    doi = {10.1051/0004-6361/202244482}
}
"""),
)
YEAR = re.compile(r"\byear\s*=\s*[{\"]?\s*(\d{4})", re.IGNORECASE)


def split_entries(text):
    """The BibTeX entries in a text, each as it is written there."""
    text = text or ""
    entries, position = [], 0
    while True:
        start = text.find("@", position)
        brace = text.find("{", start) if start >= 0 else -1
        if brace < 0:
            return entries
        depth, end = 0, brace
        while end < len(text):
            depth += {"{": 1, "}": -1}.get(text[end], 0)
            if depth == 0:
                break
            end += 1
        entries.append(text[start:end + 1])
        position = end + 1


def known_keys(bibtex):
    """{key in lower case: key} of the entries in a BibTeX text."""
    return {key.lower(): key for key in ENTRY_KEY.findall(bibtex or "")}


def _entries_of(text, long_format):
    """The entries of a reference string as lists of (tag, key), and whether
    they were found by a tag. Only entries that follow each other count, so
    that numbers in a comment behind them are not read."""
    start = START[long_format].search(text)
    if start:
        found, position = [], start.start()
        while True:
            match = ENTRY.match(text, position)
            if not match:
                break
            found.append(match.group(1))
            position = match.end()
    else:
        found = LOOSE.findall(text)
    entries = []
    for value in found:
        keys, tag = [], None
        for item in value.split(","):
            own, _, key = item.rpartition(":")
            tag = own or tag              # "gf:BWL,BK": both gave the gf
            keys.append((tag, key))
        entries.append(keys)
    return entries, bool(start)


def line_references(reference, known, long_format=False):
    """The references one line names, with what each was used for.

    ``reference`` is the line's text from the VALD file and ``known`` the keys
    there are entries for. With ``long_format``, nine references tell by their
    order which quantity each is for; otherwise only the tags do. Where no tag
    shows where the references begin, only known keys count.
    """
    entries, tagged = _entries_of(f" {reference or ''} ", long_format)
    ordered = (
        long_format and tagged and len(entries) == len(LONG_FORMAT_ORDER)
        and entries[1][0][0] == "gf"
    )
    found = {}
    for place, entry in enumerate(entries):
        for tag, value in entry:
            use = USES.get(tag, "other data")
            if ordered and tag != "iso":
                use = LONG_FORMAT_ORDER[place]
            for name in value.split("+"):
                name = RENAMED.get(name, name)
                key = known.get(name.lower())
                if not name or (key is None and not tagged):
                    continue
                uses = found.setdefault((key or name, key is not None), [])
                if use not in uses:
                    uses.append(use)
    return [{"key": key, "known": listed, "uses": uses} for (key, listed), uses in found.items()]


def missed_by_pysme(references, known, long_format):
    """BibTeX of what the lines name and PySME's list does not hold.

    PySME's reader of VALD files misses keys with a hyphen and, with some
    layouts of the reference column, others too, although its bibliography has
    them. Here they are collected from every line.
    """
    names = set()
    for text in {t for t in references if isinstance(t, str)}:
        for reference in line_references(text, known, long_format):
            if not reference["known"]:
                names.add(reference["key"])
    return ValdFile._format_references(names) if names else ""


# What is shown on one line: name, the use its references are listed under, the
# columns of the list, and how a value is written.
QUANTITIES = (
    ("Wavelength (Å)", "wavelength", ("wlcent",), "{:.4f}"),
    ("log gf", "log gf", ("gflog",), "{:.3f}"),
    ("Lower level energy χ (eV)", "lower level energy", ("excit",), "{:.3f}"),
    ("Upper level energy (eV)", "upper level energy", ("e_upp",), "{:.3f}"),
    ("J, lower → upper", None, ("j_lo", "j_up"), "{:g}"),
    ("Landé factor, lower / upper / mean", "Landé factor",
     ("lande_lower", "lande_upper", "lande"), "{:.3f}"),
    ("Radiative damping", "radiative damping", ("gamrad",), "{:.3f}"),
    ("Stark damping", "Stark damping", ("gamqst",), "{:.3f}"),
    ("van der Waals damping", "van der Waals damping", ("gamvw",), "{:.3f}"),
    ("Lower level term", "term designation", ("couple_lower", "term_lower"), "{}"),
    ("Upper level term", "term designation", ("couple_upper", "term_upper"), "{}"),
    ("Central depth", None, ("depth",), "{:.3f}"),
)
JOINED = {"J, lower → upper": " → ", "Landé factor, lower / upper / mean": " / "}


def _written(value, form):
    """A value of the list as text, or None where it has none."""
    if isinstance(value, str):
        return value.strip() or None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return form.format(number) if number == number else None


def short_term(term):
    """The term symbol without its configuration: "3d5.4s2 b4D" gives "b4D"."""
    parts = term.split() if isinstance(term, str) else []
    return parts[-1] if parts else ""


def line_quantities(values, references, edits, long_format):
    """The quantities of one line, each with the keys of its sources.

    ``values`` maps the list's columns to this line's values and ``edits`` the
    columns changed here to what the file had. A source stands for the file's
    value, not for one typed in later.
    """
    rows = []
    for name, use, columns, form in QUANTITIES:
        present = [c for c in columns if c in values and _written(values[c], form) is not None]
        if not present:
            continue
        if len(columns) == 3 and present == ["lande"]:
            name = "Landé factor"                      # the short format has one
        separator = JOINED.get(name, " ")
        row = {
            "name": name,
            "value": separator.join(_written(values[c], form) for c in present),
            "sources": [r["key"] for r in references if use in r["uses"]],
            "note": None,
            "file": None,
        }
        if use == "wavelength" and long_format and not row["sources"]:
            iso = [r["key"] for r in references if USES["iso"] in r["uses"]]
            if iso:
                row["sources"] = iso
                row["note"] = "marked “iso” by VALD: isotope and hyperfine data"
        if any(c in edits for c in present):
            row["file"] = separator.join(_written(edits.get(c, values[c]), form) for c in present)
        rows.append(row)
    if long_format:
        # The long format starts the reference line with a code on accuracy.
        text = values.get("reference")
        code = text[:10].strip() if isinstance(text, str) else ""
        if code and code != "_":
            rows.append({"name": "VALD accuracy code", "value": code, "sources": [],
                         "note": None, "file": None})
    return rows


def _plain(key, raw):
    try:
        database = pybtex.database.parse_string(raw, "bibtex")
        formatted = next(iter(Style().format_bibliography(database)))
        text = formatted.text.render_as("text")
    except Exception:                       # an entry the style cannot format
        return " ".join(raw.split())
    text = re.sub(r"\\(\w+)\s*", lambda m: JOURNALS[m.group(1)] + " " if m.group(1) in JOURNALS else m.group(0), text)
    text = text.replace("~", " ").replace(" ,", ",")
    # VALD's notes repeat the key, which is shown in front of the entry anyway.
    return " ".join(re.sub(rf"\s*\({re.escape(key)}\)\.?", "", text).split())


def _entries(bibtex):
    entries, seen = [], set()
    for raw in split_entries(bibtex):
        found = ENTRY_KEY.match(raw)
        if not found or found.group(1).lower() in seen | LEFT_OUT:
            continue
        key = found.group(1)
        seen.add(key.lower())
        entries.append({"key": key, "text": _plain(key, raw), "bibtex": raw.strip()})
    return sorted(entries, key=lambda e: e["text"].lower())


def code_entries(stored):
    """PySME and SME: what PySME stores and its own papers, newest first."""
    text = stored or ""
    for marks, entry in PYSME_PAPERS:
        if not any(mark in text for mark in marks):
            text += entry

    def year(entry):
        found = YEAR.search(entry["bibtex"])
        return int(found.group(1)) if found else 0

    return sorted(_entries(text), key=year, reverse=True)


def sections(sme, line_bibtex=None):
    """The references in the order a reader looks for them. ``line_bibtex``
    replaces what PySME stores for the line list."""
    database = set(known_keys(ValdFile.citation_info))
    if line_bibtex is None and len(sme.linelist):
        line_bibtex = sme.linelist.citation_info
    lines = _entries(line_bibtex) if len(sme.linelist) else []
    single = sorted((e for e in lines if e["key"].lower() not in database),
                    key=lambda e: e["key"].lower())
    return [
        {"id": "code", "title": "PySME and SME", "entries": code_entries(sme.citation_info),
         "empty": "No reference stored."},
        {"id": "atmosphere", "title": "Model atmosphere",
         "entries": _entries(sme.atmo.citation_info),
         "empty": "No reference yet: it is filled in when a synthesis uses a model atmosphere."},
        {"id": "abundances", "title": "Abundances", "entries": _entries(sme.abund.citation_info),
         "empty": "No reference stored for this abundance pattern."},
        {"id": "nlte", "title": "NLTE grids", "entries": _entries(sme.nlte.citation_info),
         "empty": ("No reference yet: it is filled in by a synthesis with NLTE."
                   if sme.nlte.elements else "NLTE is not used in this setup.")},
        {"id": "vald", "title": "Line list: the VALD database",
         "entries": [e for e in lines if e["key"].lower() in database],
         "empty": "No line list."},
        {"id": "lines", "title": "Line list: data of single lines", "entries": single,
         "keys": True,         # these are what the lines refer to
         "empty": "The line list names no references."},
    ]


def as_text(parts, output):
    """All sections as one text, to be copied or saved."""
    blocks, number, written = [], 0, set()
    for part in parts:
        lines = []
        for entry in part["entries"]:
            if output == "bibtex":
                if entry["key"].lower() in written:      # BibTeX refuses repeats
                    continue
                written.add(entry["key"].lower())
                lines.append(entry["bibtex"])
            else:
                number += 1
                name = f"{entry['key']}: " if part.get("keys") else ""
                lines.append(f"[{number}] {name}{entry['text']}")
        if lines:
            comment = "% " if output == "bibtex" else ""
            blocks.append("\n".join([f"{comment}{part['title']}", *lines]))
    return "\n\n".join(blocks) + "\n"
