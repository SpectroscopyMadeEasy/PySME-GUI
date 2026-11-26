"""Editing line data, and where a line of the list appears in the plot."""

import threading
import time

import numpy as np
import pytest

from pysme_gui.jobs import JobManager, LogBuffer
from pysme_gui.session import Session, UserError


def synthesized(session):
    log = LogBuffer()
    jobs = JobManager(session, threading.RLock(), log)
    jobs.start("synth")
    start = time.time()
    while jobs.running:
        assert time.time() - start < 300, "job timed out"
        time.sleep(0.2)
    assert jobs.status()["state"] == "done", log.tail()
    return np.array(session.sme.synth[0])


def example():
    session = Session()
    session.load_example()
    session.set_synth_setup([[6436, 6444]], 0.01)
    return session


def find(session, species, wavelength):
    table = session.linelist_rows(limit=500)
    columns = table["columns"]
    for line_id, row in zip(table["ids"], table["rows"]):
        if row[columns.index("species")] == species and abs(row[columns.index("wlcent")] - wavelength) < 0.01:
            return line_id
    raise AssertionError(f"no {species} line at {wavelength}")


def from_the_start(column, line_id, value):
    """The reference: the list is changed by hand before PySME ever sees it."""
    session = example()
    frame = session.sme.linelist._lines
    frame.iat[line_id, frame.columns.get_loc(column)] = value
    session.sme.linelist._lines = frame.sort_values("wlcent", kind="stable").reset_index(drop=True)
    return synthesized(session)


@pytest.mark.parametrize("species, wavelength, column, change", [
    ("Mn 1", 6440.914, "gflog", 6.0),       # from invisible to deep
    ("Ca 1", 6439.075, "gflog", -2.0),
    ("Ca 1", 6439.075, "gamvw", 1.0),
    ("Si 1", 6440.566, "excit", -1.0),
    ("Si 1", 6440.566, "wlcent", 1.5),      # passes other lines: the order changes
])
def test_a_run_after_an_edit_sees_the_edit(species, wavelength, column, change):
    session = example()
    original = synthesized(session)
    line_id = find(session, species, wavelength)
    old = session.line(line_id)["values"][column]
    session.set_line(line_id, column, old + change)
    assert session.synth_stale
    edited = synthesized(session)
    assert np.max(np.abs(edited - original)) > 0.05
    np.testing.assert_allclose(edited, from_the_start(column, line_id, old + change), rtol=1e-8)

    line = session.line(line_id)                      # still the same line
    assert line["species"] == species
    assert line["values"][column] == pytest.approx(old + change)
    assert line["original"] == {column: old}
    assert np.all(np.diff(session.sme.linelist._lines["wlcent"]) >= 0)

    session.reset_lines(line_id)
    assert session.line(line_id)["original"] == {} and session.state()["linelist"]["edited"] == 0
    np.testing.assert_allclose(synthesized(session), original, rtol=1e-8)


def test_edits_are_saved_with_the_file_and_can_be_undone_later(tmp_path):
    session = example()
    table_before = session.linelist_rows(limit=500)["rows"]
    calcium = find(session, "Ca 1", 6439.075)
    silicon = find(session, "Si 1", 6440.566)
    session.set_line(calcium, "gflog", 0.0)
    session.set_line(silicon, "wlcent", 6442.0)
    changed = synthesized(session)

    saved = session.save(str(tmp_path / "edited.sme"))
    other = Session()
    other.open_path(saved)
    assert other.state()["linelist"]["edited"] == 2
    moved = find(other, "Si 1", 6442.0)
    assert other.line(moved)["original"] == {"wlcent": pytest.approx(6440.5658, abs=1e-3)}
    np.testing.assert_allclose(synthesized(other), changed, rtol=1e-8)
    other.reset_lines()
    assert other.state()["linelist"]["edited"] == 0
    restored = other.linelist_rows(limit=500)["rows"]
    assert [r[0] for r in restored] == [r[0] for r in table_before]
    # PySME writes about ten digits into the file.
    np.testing.assert_allclose([r[1:] for r in restored], [r[1:] for r in table_before], rtol=1e-8)

    # A newly loaded list has no edits, and the interface can tell it is new.
    before = session.state()["linelist"]["version"]
    session.load_example()
    assert session.state()["linelist"]["edited"] == 0
    assert session.state()["linelist"]["version"] > before


def test_edit_values_are_checked():
    session = example()
    line_id = find(session, "Ca 1", 6439.075)
    old = session.line(line_id)["values"]["gflog"]
    for column, value in (("gflog", "abc"), ("gflog", float("nan")), ("species", "Fe 1"),
                          ("depth", 0.5), ("wlcent", -1), ("excit", -0.1)):
        with pytest.raises(UserError):
            session.set_line(line_id, column, value)
    with pytest.raises(UserError, match="not in the line list"):
        session.set_line(10 ** 6, "gflog", 0)
    session.set_line(line_id, "gflog", old + 0.3)
    session.set_line(line_id, "gflog", old)                 # back by hand
    assert session.line(line_id)["original"] == {}

    # Whatever PySME had cached about the lines must not outlive an edit.
    session.sme.linelist.almax_paras = np.array([5770, 4.44, 0, 1, 1e-4, 1e-3])
    session.set_line(line_id, "gflog", old + 0.1)
    assert session.sme.linelist.almax_paras is None


def test_filtered_rows_and_labels_carry_the_same_ids():
    session = example()
    session.set_line(find(session, "Si 1", 6440.566), "wlcent", 6442.0)
    for query in ("", "Si", "6439-6442.5"):
        table = session.linelist_rows(offset=1, limit=4, query=query)
        for line_id, row in zip(table["ids"], table["rows"]):
            values = session.line(line_id)["values"]
            assert row[table["columns"].index("wlcent")] == pytest.approx(values["wlcent"])
    for label in session.strong_lines(6438, 6443)["lines"]:
        assert session.line(label["id"])["values"]["wlcent"] == pytest.approx(label["wave"])


def test_a_line_appears_where_the_velocity_puts_it(tmp_path):
    session = example()
    model = synthesized(session)
    wave = np.array(session.sme.wave[0])
    observed = tmp_path / "moving.txt"           # the same star, receding
    np.savetxt(observed, np.column_stack([wave, np.interp(wave, wave * (1 + 30 / 299792.458), model),
                                          np.full_like(wave, 0.005)]))
    session.import_spectrum(str(observed))
    session.set_params({"vrad_flag": "fix", "vrad": 30, "cscale_flag": "none"})
    synth = synthesized(session)
    segment = session.spectrum()["segments"][0]
    calcium = session.line(find(session, "Ca 1", 6439.075))["values"]["wlcent"]
    deepest = np.array(segment["wave"])[np.argmin(synth)]
    assert deepest == pytest.approx(calcium * segment["shift"], abs=0.02)
    assert abs(deepest - calcium) > 0.6
    # The shift belongs to the spectrum shown, not to a velocity typed since.
    session.set_params({"vrad": 0})
    assert session.spectrum()["segments"][0]["shift"] == segment["shift"]


@pytest.mark.parametrize("settings", [
    {"vrad_flag": "fix", "vrad": 30, "cscale_flag": "fix"},
    {"vrad_flag": "whole", "vrad": 5, "cscale_flag": "linear"},
])
def test_a_synthesis_without_observation_keeps_the_settings_for_one(settings):
    """PySME switches velocity and continuum off here; the user did not."""
    session = example()
    session.set_params(settings)
    synth = synthesized(session)
    shown = session.state()["params"]
    assert {k: shown[k] for k in settings} == settings
    # Such a spectrum is not shifted, so nothing marked on it may be.
    assert session.spectrum()["segments"][0]["shift"] == 1.0
    calcium = session.line(find(session, "Ca 1", 6439.075))["values"]["wlcent"]
    assert np.array(session.sme.wave[0])[np.argmin(synth)] == pytest.approx(calcium, abs=0.02)
