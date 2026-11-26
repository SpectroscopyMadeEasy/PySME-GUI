"""The two flux scales, and the way from a line label to the Line list tab."""

import threading
import time

import numpy as np
import pytest
from pysme.sme import SME_Structure
from pysme.synthesize import synthesize_spectrum

from pysme_gui.jobs import JobManager, LogBuffer
from pysme_gui.session import Session


def run(jobs, kind, timeout=300):
    jobs.start(kind)
    start = time.time()
    while jobs.running:
        assert time.time() - start < timeout, "job timed out"
        time.sleep(0.2)
    return jobs.status()


def test_a_line_label_points_at_its_row_in_the_line_list():
    session = Session()
    session.load_example()
    table = session.linelist_rows(limit=100000)
    species, wave = table["columns"].index("species"), table["columns"].index("wlcent")
    lines = session.strong_lines(6438, 6442, limit=20)["lines"]
    assert lines
    for line in lines:
        row = table["rows"][line["row"]]
        assert row[species] == line["species"]
        assert row[wave] == pytest.approx(line["wave"])
        # The tab shows one page at a time, found by this offset.
        page = session.linelist_rows(offset=line["row"] // 7 * 7, limit=7)["rows"]
        assert page[line["row"] % 7] == row


def synthesized(session, kind="synth"):
    log = LogBuffer()
    jobs = JobManager(session, threading.RLock(), log)
    assert run(jobs, kind)["state"] == "done", log.tail()
    return session.spectrum()["segments"][0]


def test_one_synthesis_gives_both_scales(tmp_path):
    session = Session()
    session.load_example()
    segment = synthesized(session)
    normalised, flux = np.array(segment["synth"]), np.array(segment["flux"])
    assert normalised.max() <= 1.001
    # Of the order of the surface flux of a black body at this temperature.
    assert 5e6 < flux.max() < 1e7

    # The same as PySME computes when asked for the flux itself.
    saved = session.save(str(tmp_path / "flux.sme"))
    direct = SME_Structure.load(saved)
    direct.normalize_by_continuum = False
    direct = synthesize_spectrum(direct)          # on the same wavelengths
    np.testing.assert_allclose(flux, np.array(direct.synth[0]), rtol=1e-6)

    assert set(session.run_spectrum(1)["segments"][0]) == {"wave", "synth", "flux"}
    path = session.export_csv(str(tmp_path / "flux"))
    with open(path) as handle:
        head = [handle.readline().strip() for _ in range(3)]
    assert head[1].startswith("# synth_surface_flux: model flux at the stellar surface")
    assert head[2] == "segment,wave,synth,synth_surface_flux"

    # A file made elsewhere with the flux not normalised shows both as well.
    outside = str(tmp_path / "outside.sme")
    direct.meta = {}
    direct.save(outside)
    other = Session()
    other.open_path(outside)
    shown = other.spectrum()["segments"][0]
    np.testing.assert_allclose(shown["flux"], flux, rtol=1e-6)
    np.testing.assert_allclose(shown["synth"], normalised, rtol=1e-6)
    # The next run is computed normalised again.
    shown = synthesized(other)
    assert other.sme.normalize_by_continuum
    np.testing.assert_allclose(shown["synth"], normalised, rtol=1e-6)


def test_surface_flux_leaves_out_the_continuum_correction(tmp_path):
    """An observation in counts: the correction scales the model to it, and the
    surface flux must not carry that scale."""
    session = Session()
    session.load_example()
    session.set_synth_setup([[6437, 6443]], 0.02)
    model = synthesized(session)
    # A flat line would not do: PySME's fit needs a spectrum that looks like one.
    rng = np.random.default_rng(2)
    wave = np.array(model["wave"])
    data = 1000 * (np.array(model["synth"]) + rng.normal(0, 0.003, wave.size))
    counts = tmp_path / "counts.txt"
    np.savetxt(counts, np.column_stack([wave, data, np.full_like(wave, 3)]))
    session.import_spectrum(str(counts))
    session.set_params({"vrad_flag": "none", "cscale_flag": "none"})
    reference = np.array(synthesized(session)["flux"])

    session.set_params({"cscale_flag": "constant", "cscale_type": "match"})
    segment = synthesized(session)
    assert 900 < np.median(segment["synth"]) < 1100        # on the scale of the data
    np.testing.assert_allclose(segment["flux"], reference, rtol=1e-6)

    # What was applied is remembered, whatever the settings say afterwards.
    session.set_params({"cscale_flag": "none"})
    np.testing.assert_allclose(session.spectrum()["segments"][0]["flux"], reference, rtol=1e-6)
    saved = session.save(str(tmp_path / "counts.sme"))
    other = Session()
    other.open_path(saved)
    np.testing.assert_allclose(other.spectrum()["segments"][0]["flux"], reference, rtol=1e-6)
    # Coefficients still stored from that run are not applied once it is off.
    np.testing.assert_allclose(synthesized(session)["flux"], reference, rtol=1e-6)
