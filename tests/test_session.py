"""End-to-end tests. They run real PySME and need the MARCS grid (downloaded once)."""

import threading
import time

import numpy as np
import pytest

from pysme_gui.jobs import JobManager, LogBuffer
from pysme_gui.session import Session, UserError, available_options


@pytest.fixture
def app():
    session = Session()
    log = LogBuffer()
    jobs = JobManager(session, threading.RLock(), log)
    return session, jobs, log


def run(jobs, kind, timeout=300):
    jobs.start(kind)
    start = time.time()
    while jobs.running:
        assert time.time() - start < timeout, "job timed out"
        time.sleep(0.2)
    return jobs.status()


def test_empty_session_explains_what_is_missing(app):
    session, jobs, _ = app
    ready = session.state()["ready"]
    assert "Load a line list" in ready["synth"]
    assert "Set a wavelength range" in ready["synth"]
    with pytest.raises(UserError, match="line list"):
        jobs.start("synth")


def test_parameter_validation(app):
    session = app[0]
    with pytest.raises(UserError):
        session.set_params({"teff": -5})
    with pytest.raises(UserError):
        session.set_params({"vmic": "abc"})
    with pytest.raises(UserError):
        session.set_params({"fitparameters": ["abund Xx"]})
    with pytest.raises(UserError):
        session.set_synth_setup([[5000, 5010], [5005, 5020]], None)
    session.set_params({"fitparameters": ["Teff", "abund mg"]})
    assert session.sme.fitparameters == ["teff", "abund Mg"]


def test_abundance_edit_sets_synthesis_abundance(app):
    session = app[0]
    session.apply_abund_pattern("asplund2009")
    session.set_params({"monh": -1.0})
    session.set_abundance("Mg", 7.8)
    assert session.sme.abund.A["Mg"] == pytest.approx(7.8)
    row = next(r for r in session.abundance_table()["elements"] if r["el"] == "Mg")
    assert row["A"] == pytest.approx(7.8)
    assert row["delta"] == pytest.approx(7.8 - (7.60 - 1.0))
    session.set_abundance("Mg", None)
    assert session.sme.abund.A["Mg"] == pytest.approx(6.60)


def test_options(app):
    options = available_options()
    assert "marcs2012.sav" in options["atmospheres"]
    assert options["nlte_grids"] and "asplund2009" in options["patterns"]


def test_save_and_reload_settings(app, tmp_path):
    session = app[0]
    session.load_example()
    session.set_params({"teff": 6000, "fitparameters": ["monh"], "ipres": 40000})
    session.set_synth_setup([[6436, 6439], [6440, 6444]], 0.02)
    session.set_nlte("Fe", True)
    session.set_abundance("Si", 7.9)
    path = session.save(str(tmp_path / "setup"))
    other = Session()
    other.open_path(path)
    assert other.sme.teff == 6000
    assert other.ranges == [[6436, 6439], [6440, 6444]] and other.step == 0.02
    assert other.sme.nlte.elements == ["Fe"]
    assert other.sme.abund.A["Si"] == pytest.approx(7.9)
    assert other.sme.iptype == "gauss"
    assert len(other.sme.linelist) == len(session.sme.linelist)
    assert not other.dirty


def test_synthesis_then_fit_recovers_injected_value(app, tmp_path):
    session, jobs, log = app
    session.load_example()
    status = run(jobs, "synth")
    assert status["state"] == "done", log.tail()
    spectrum = session.spectrum()["segments"][0]
    assert 0 < min(spectrum["synth"]) < 0.8 and max(spectrum["synth"]) <= 1.001

    # Changing a parameter must flag the plotted synthesis as out of date, and
    # changing the range must then give a spectrum on the new range.
    session.set_params({"monh": -0.2})
    assert session.synth_stale
    session.set_synth_setup([[6437, 6443]], 0.02)
    assert run(jobs, "synth")["state"] == "done", log.tail()
    assert not session.synth_stale
    wave = np.array(session.sme.wave[0])
    assert wave[0] == pytest.approx(6437) and wave[-1] == pytest.approx(6443)
    assert len(wave) == 301
    assert len(session.runs) == 2

    # Mock observation with the injected [M/H] = -0.2 plus noise.
    rng = np.random.default_rng(1)
    flux = np.array(session.sme.synth[0]) + rng.normal(0, 0.003, len(wave))
    mock = tmp_path / "mock.txt"
    np.savetxt(mock, np.column_stack([wave, flux, np.full(len(wave), 0.003)]))

    session.set_params({"monh": 0.0, "fitparameters": ["monh"]})
    session.import_spectrum(str(mock))
    assert session.sme.synth is None
    session.set_windows([[6437.5, 6442.5]])
    assert session.sme.wave[0][0] >= 6437.5 and session.sme.wave[0][-1] <= 6442.5
    assert session.set_mask(6437.5, 6437.7, 0) > 0

    status = run(jobs, "fit")
    assert status["state"] == "done", log.tail()
    result = session.fit_info["parameters"][0]
    assert result["name"] == "monh" and result["initial"] == 0.0
    assert result["value"] == pytest.approx(-0.2, abs=0.02)
    assert result["unc_fit"] > 0
    assert np.sum(np.array(session.sme.mask[0]) == 0) > 0

    saved = session.save(str(tmp_path / "fit.sme"))
    other = Session()
    other.open_path(saved)
    assert other.obs is not None and other.has_uncs
    assert other.fit_info["parameters"][0]["value"] == pytest.approx(result["value"])
    assert other.sme.synth is not None

    csv = session.export_csv(str(tmp_path / "out"))
    with open(csv) as handle:
        versions, header = handle.readline(), handle.readline().strip()
    assert versions.startswith("# synth computed with PySME ")
    assert header == "segment,wave,flux_obs,uncertainty,mask,synth"

    session.revert_fit()
    assert session.sme.monh == 0.0 and session.synth_stale

    session.remove_observation()
    assert session.obs is None and session.sme.spec is None
    assert run(jobs, "synth")["state"] == "done", log.tail()


def test_fit_without_uncertainties_keeps_the_assumption_visible(app, tmp_path):
    session, jobs, log = app
    session.load_example()
    assert run(jobs, "synth")["state"] == "done", log.tail()
    wave = np.array(session.sme.wave[0])
    mock = tmp_path / "noerr.txt"
    np.savetxt(mock, np.column_stack([wave, np.array(session.sme.synth[0])]))
    session.import_spectrum(str(mock))
    session.set_params({"snr": 200, "fitparameters": ["monh"], "monh": 0.1})
    assert any("S/N = 200" in w for w in session.readiness()["warnings"])
    assert run(jobs, "fit")["state"] == "done", log.tail()
    other = Session()
    other.open_path(session.save(str(tmp_path / "noerr.sme")))
    assert not other.has_uncs and other.snr == 200


def test_synthesis_with_observation_and_free_velocity(app, tmp_path):
    session, jobs, log = app
    session.load_example()
    assert run(jobs, "synth")["state"] == "done", log.tail()
    wave = np.array(session.sme.wave[0])
    mock = tmp_path / "obs.txt"
    np.savetxt(mock, np.column_stack([wave, np.array(session.sme.synth[0])]))
    session.import_spectrum(str(mock))
    session.set_params({"vrad_flag": "whole", "cscale_flag": "constant"})
    assert run(jobs, "synth")["state"] == "done", log.tail()
    # Removing the data must not leave flags behind that need data.
    session.remove_observation()
    assert run(jobs, "synth")["state"] == "done", log.tail()
    assert session.sme.vrad_flag == "whole" and session.sme.cscale_flag == "constant"


def test_cancel_and_failure_leave_session_intact(app, tmp_path):
    session, jobs, log = app
    session.load_example()
    jobs.start("synth")
    time.sleep(0.3)
    jobs.cancel()
    start = time.time()
    while jobs.running:
        assert time.time() - start < 20
        time.sleep(0.1)
    assert jobs.status()["state"] == "cancelled"
    assert session.sme.synth is None

    session.set_params({"atmo.source": "does_not_exist.sav"})
    status = run(jobs, "synth")
    assert status["state"] == "failed"
    assert "does_not_exist" in status["error"]
    assert session.sme.synth is None


@pytest.mark.parametrize("free_monh", [True, False])
def test_abundance_fit_is_reported_on_the_synthesis_scale(app, tmp_path, free_monh):
    session, jobs, log = app
    session.load_example()
    truth_monh = -0.2
    session.set_params({"monh": truth_monh})
    session.set_synth_setup([[6436, 6444]], 0.025)
    session.set_abundance("Ca", session.sme.abund.A["Ca"] + 0.15)
    truth = session.sme.abund.A["Ca"]
    assert run(jobs, "synth")["state"] == "done", log.tail()
    wave = np.array(session.sme.wave[0])
    flux = np.array(session.sme.synth[0]) + np.random.default_rng(2).normal(0, 0.003, wave.size)
    mock = tmp_path / "mock.txt"
    np.savetxt(mock, np.column_stack([wave, flux, np.full(wave.size, 0.003)]))

    session.set_abundance("Ca", None)
    session.set_params({"monh": 0.0 if free_monh else truth_monh})
    session.import_spectrum(str(mock))
    free = ["monh", "abund Ca"] if free_monh else ["abund Ca"]
    session.set_params({"fitparameters": free})
    start_a = session.sme.abund.A["Ca"]
    start_monh = session.sme.monh
    assert run(jobs, "fit")["state"] == "done", log.tail()

    assert session.sme.fitparameters == free
    row = next(r for r in session.fit_info["parameters"] if r["name"] == "abund Ca")
    assert row["initial"] == pytest.approx(start_a)
    assert row["value"] == pytest.approx(truth, abs=0.05)
    assert row["value"] == pytest.approx(session.sme.abund.A["Ca"])
    assert row["solver"]["value"] == pytest.approx(row["value"] - session.sme.monh, abs=1e-6)
    assert (row["unc_fit"] is None) == free_monh

    # The summary survives saving, and a second fit still works.
    other = Session()
    other.open_path(session.save(str(tmp_path / "abund.sme")))
    assert other.fit_info["parameters"] == session.fit_info["parameters"]
    assert other.sme.fitparameters == free
    session.revert_fit()
    assert session.sme.monh == pytest.approx(start_monh)
    assert session.sme.abund.A["Ca"] == pytest.approx(start_a)
    assert run(jobs, "fit")["state"] == "done", log.tail()
