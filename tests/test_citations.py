"""References sorted by source, and from a line of the list to its entry."""

import threading
import time

import pybtex.database
import pytest

from pysme_gui import citations
from pysme_gui.jobs import JobManager, LogBuffer
from pysme_gui.session import Session, UserError

ORDER = ["code", "atmosphere", "abundances", "nlte", "vald", "lines"]


def keys(parts, name):
    return [e["key"] for e in parts[ORDER.index(name)]["entries"]]


def example():
    session = Session()
    session.load_example()
    return session


def test_the_references_are_sorted_by_what_they_are_for():
    session = example()
    parts = session.citations("plaintext")["sections"]
    assert [p["id"] for p in parts] == ORDER
    assert keys(parts, "code") and "2017A&A...597A..16P" in keys(parts, "code")
    assert keys(parts, "atmosphere") == [] and "synthesis" in parts[1]["empty"]
    # The pattern's own reference, without the atomic weights PySME adds to it.
    assert keys(parts, "abundances") == ["2007SSRv..130..105G"]
    assert parts[3]["empty"] == "NLTE is not used in this setup."
    assert "2015PhyS...90e4005R" in keys(parts, "vald")
    assert {"BPM", "FMW", "K14", "LWHS"} <= set(keys(parts, "lines"))
    assert not set(keys(parts, "vald")) & set(keys(parts, "lines"))

    # The atmosphere's reference arrives with the first synthesis.
    log = LogBuffer()
    jobs = JobManager(session, threading.RLock(), log)
    jobs.start("synth")
    while jobs.running:
        time.sleep(0.2)
    assert jobs.status()["state"] == "done", log.tail()
    assert len(keys(session.citations("plaintext")["sections"], "atmosphere")) == 1


def test_pysme_and_sme_are_listed_newest_first_with_pysmes_own_papers():
    session = example()
    code = session.citations("plaintext")["sections"][0]["entries"]
    assert [e["key"] for e in code] == [
        "Jian2026", "2023A&A...671A.171W", "2017A&A...597A..16P", "1996A&AS..118..595V"]
    jian, wehrhahn = code[0]["text"], code[1]["text"]
    assert "Jönsson" in jian and "survey-scale applications" in jian and "711:A168" in jian
    assert "Spectroscopy Made Easier" in wehrhahn and "671:A171" in wehrhahn
    text = session.citations("plaintext")["text"]
    assert text.index("survey-scale") < text.index("Made Easier") < text.index("Evolution")
    assert "Jian2026" in pybtex.database.parse_string(
        session.citations("bibtex")["text"], "bibtex").entries

    # Once PySME itself names a paper, it is not listed twice.
    session.sme.citation_info += (
        "\n@article{theirKey, title={PySME v1.0}, author={Jian, M.}, year={2026},"
        " journal={A&A}, doi={10.1051/0004-6361/202659986}}\n")
    keys_now = keys(session.citations("plaintext")["sections"], "code")
    assert "theirKey" in keys_now and "Jian2026" not in keys_now and len(keys_now) == 4


def test_the_text_to_copy_keeps_the_sections_and_names_the_line_data():
    session = example()
    text = session.citations("plaintext")["text"]
    assert "Atomic weights" not in text + session.citations("bibtex")["text"]
    places = [text.index(title) for title in
              ("PySME and SME", "Abundances", "Line list: the VALD database", "Line list: data of single lines")]
    assert places == sorted(places)
    assert "] K14: R. L. Kurucz" in text
    assert "\\aap" not in text and "\\ssr" not in text and "(BPM)" not in text

    bibtex = session.citations("bibtex")["text"]
    assert bibtex.startswith("% PySME and SME")
    # BibTeX must be able to read it: no key twice, nothing cut short.
    database = pybtex.database.parse_string(bibtex, "bibtex")
    expected = {e["key"] for p in session.citations("bibtex")["sections"] for e in p["entries"]}
    assert set(database.entries) == expected


def test_each_line_names_references_that_are_listed():
    session = example()
    table = session.linelist_rows(limit=500)
    assert len(table["references"]) == len(table["rows"])
    listed = set(keys(session.citations("plaintext")["sections"], "lines"))
    for references in table["references"]:
        assert references
        assert all(r["known"] and r["key"] in listed for r in references)
    species = [row[table["columns"].index("species")] for row in table["rows"]]
    calcium = next(refs for name, refs in zip(species, table["references"]) if name == "Ca 1")
    by_key = {r["key"]: r["uses"] for r in calcium}
    assert {"wavelength", "log gf"} <= set(by_key["SR"]) and "Sm" in by_key and "BPM" in by_key
    # A page, a search and the whole table agree.
    page = session.linelist_rows(offset=2, limit=3, query="Ca")
    assert all(page["references"])


def test_in_the_long_format_the_order_says_what_each_reference_is_for():
    known = {"k07": "K07", "mfw": "MFW", "bpm": "BPM"}
    text = ("NC Kurucz MnI 2007 Mn  8 wl:K07  11 gf:MFW   8 K07   8 K07   8 K07   8 K07   8 K07"
            "   6 BPM   8 K07 \\hfs BBAOKb BBAOKb")
    found = {r["key"]: r["uses"] for r in citations.line_references(text, known, long_format=True)}
    assert found["MFW"] == ["log gf"] and found["BPM"] == ["van der Waals damping"]
    assert found["K07"] == [
        "wavelength", "lower level energy", "upper level energy", "Landé factor",
        "radiative damping", "Stark damping", "term designation"]
    # Otherwise only the labels are trusted: the short format has another order.
    found = {r["key"]: r["uses"] for r in citations.line_references(text, known)}
    assert found["BPM"] == ["other data"]


def test_the_example_list_says_which_quantity_each_reference_gave():
    session = example()
    table = session.linelist_rows(limit=500, query="Fe 2")
    iron = {r["key"]: r for r in table["references"][0]}
    # PySME's reader skips names with a hyphen, although it has the entry.
    assert iron["BA-J"]["uses"] == ["van der Waals damping"] and iron["BA-J"]["known"]
    assert iron["RU"]["uses"] == ["log gf"]
    parts = session.citations("plaintext")["sections"]
    assert "BA-J" in keys(parts, "lines")
    assert "BA-J" in session.citations("bibtex")["text"]


@pytest.mark.parametrize("reference, expected", [
    # one entry with several keys, with and without their own tag
    ("_  Sc-Ni Wisconsin   53 wl:AA,iso:BB 41 gf:CC,FF-G   4 HH    Ni",
     {"AA": ["wavelength"], "BB": ["isotopes and hyperfine structure"],
      "CC": ["log gf"], "FF-G": ["log gf"], "HH": ["other data"]}),
    # the wavelength entry in front of "gf:" has no tag
    ("_  La1 to Lu 2 Bell  19 AA  19 gf:BB  19 DD    La",
     {"AA": ["other data"], "BB": ["log gf"], "DD": ["other data"]}),
    # the entries start early, and numbers behind them are not read
    ("_  Kurucz   1 wl:AA   2 gf:BB   3 CC    hfs: seen 7 times 8 more",
     {"AA": ["wavelength"], "BB": ["log gf"], "CC": ["other data"]}),
])
def test_other_layouts_of_the_reference_column(reference, expected):
    found = citations.line_references(reference, {}, long_format=True)
    assert {r["key"]: r["uses"] for r in found} == expected


def test_what_pysmes_reader_misses_is_looked_up_from_the_lines():
    texts = ["_ x  9 wl:K13  10 gf:RU,BPM   6 BA-J   9 NOT-AN-ENTRY", None]
    bibtex = citations.missed_by_pysme(texts, {"k13": "K13"}, long_format=True)
    found = set(citations.known_keys(bibtex).values())
    assert found == {"RU", "BPM", "BA-J"}
    assert citations.missed_by_pysme(["_ x  9 wl:K13"], {"k13": "K13"}, True) == ""


def find(session, species, wavelength=None):
    table = session.linelist_rows(limit=500)
    columns = table["columns"]
    for index, row in enumerate(table["rows"]):
        if row[columns.index("species")] == species and (
                wavelength is None or abs(row[columns.index("wlcent")] - wavelength) < 1e-3):
            return table["ids"][index], table["terms"][index]
    raise AssertionError(f"no {species} line")


def test_one_line_shows_every_quantity_beside_its_source():
    session = example()
    line_id, terms = find(session, "Mn 1", 6443.4404)
    assert terms == "b4D → z4D*"
    detail = session.line_detail(line_id)
    assert detail["line"] == "Mn 1 6443.4404 Å"
    rows = {q["name"]: q for q in detail["quantities"]}
    assert rows["log gf"]["value"] == "-3.170" and rows["log gf"]["sources"] == ["MFW"]
    assert rows["van der Waals damping"]["sources"] == ["BPM"]
    for name in ("Wavelength (Å)", "Upper level energy (eV)", "Landé factor, lower / upper / mean",
                 "Lower level term", "Upper level term"):
        assert rows[name]["sources"] == ["K07"], name
    assert rows["J, lower → upper"]["value"] == "1.5 → 2.5"
    for name in ("J, lower → upper", "Central depth", "VALD accuracy code"):    # no source, no remark
        assert rows[name]["sources"] == [] and rows[name]["note"] is None, name
    assert rows["Lower level term"]["value"] == "LS 3d5.4s2 b4D"
    assert rows["VALD accuracy code"]["value"] == "NC"
    assert all(q["file"] is None for q in detail["quantities"])
    assert [r["key"] for r in detail["references"]] == ["K07", "MFW", "BPM"]

    # A typed value: the source stays with what the file had.
    session.set_line(line_id, "gflog", -2.5)
    rows = {q["name"]: q for q in session.line_detail(line_id)["quantities"]}
    assert (rows["log gf"]["value"], rows["log gf"]["file"]) == ("-2.500", "-3.170")
    assert rows["Stark damping"]["file"] is None
    with pytest.raises(UserError, match="not in the line list"):
        session.line_detail(10**6)


def test_a_wavelength_marked_iso_names_that_source():
    session = example()
    line_id, _ = find(session, "Eu 2")
    wavelength = session.line_detail(line_id)["quantities"][0]
    assert wavelength["sources"] == ["LWHS"] and "iso" in wavelength["note"]


def test_reading_one_reference_line():
    known = {"k14": "K14", "fmw": "FMW", "lwb2": "LWb2"}
    found = citations.line_references(
        "N D   NBS 1988 data   1 wl:K14   2 gf:FMW   1 K14   3 LWb   4 NEW  \\hfs AB AB", known)
    assert found == [
        {"key": "K14", "known": True, "uses": ["wavelength", "other data"]},
        {"key": "FMW", "known": True, "uses": ["log gf"]},
        {"key": "LWb2", "known": True, "uses": ["other data"]},
        {"key": "NEW", "known": False, "uses": ["other data"]},
    ]
    # Without a labelled wavelength, only what the list has an entry for counts.
    assert citations.line_references("something 1988 data  2 K14", known) == [
        {"key": "K14", "known": True, "uses": ["other data"]}]
    assert citations.line_references("", known) == [] == citations.line_references(None, known)


def test_a_setup_without_a_line_list_still_has_its_references():
    result = Session().citations("plaintext")
    assert "PySME and SME" in result["text"] and "Line list" not in result["text"]
