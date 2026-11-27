"""The radial velocity as a free parameter, and its mask."""

import threading
import time

import numpy as np
import pytest
from pysme.sme import MASK_VALUES, SME_Structure

from pysme_gui.jobs import JobManager, LogBuffer
from pysme_gui.session import Session

C = 299792.458
LINE, VRAD = int(MASK_VALUES.LINE), int(MASK_VALUES.VRAD)


def run(session, kind):
    log = LogBuffer()
    jobs = JobManager(session, threading.RLock(), log)
    jobs.start(kind)
    start = time.time()
    while jobs.running:
        assert time.time() - start < 300, "job timed out"
        time.sleep(0.2)
    assert jobs.status()["state"] == "done", log.tail()


@pytest.fixture(scope="module")
def model():
    session = Session()
    session.load_example()
    session.set_synth_setup([[6436, 6444]], 0.01)
    run(session, "synth")
    return np.array(session.sme.wave[0]), np.array(session.sme.synth[0])


def observed(tmp_path, wave, flux, name="obs.txt"):
    path = tmp_path / name
    np.savetxt(path, np.column_stack([wave, flux, np.full_like(wave, 0.005)]))
    session = Session()
    session.load_example()
    session.import_spectrum(str(path))
    session.set_params({"cscale_flag": "none"})
    return session


def receding(wave, flux, velocity):
    """The spectrum of the same star moving away; velocity may differ by pixel."""
    return np.interp(wave / (1 + np.asarray(velocity) / C), wave, flux)


def test_a_synthesis_does_not_fit_the_velocity_but_a_fit_does(tmp_path, model):
    session = observed(tmp_path, model[0], receding(*model, 30))
    assert "Select at least one free parameter" in session.readiness()["fit"]
    session.set_params({"vrad_flag": "whole"})
    assert session.readiness()["fit"] == []                 # v_rad alone is enough

    run(session, "synth")
    assert session.sme.vrad_flag == "whole"
    assert session.state()["params"]["vrad"] == 0           # as typed, not fitted

    run(session, "fit")                                     # only the velocity
    fitted = session.state()["params"]["vrad"]
    assert fitted == pytest.approx(30, abs=0.3)
    info = session.fit_info
    assert info["parameters"] == [] and info["chisq"] is None
    assert info["velocity"]["initial"] == [0] and not info["velocity"]["each"]
    assert info["velocity"]["value"] == [pytest.approx(fitted)]

    # The next synthesis uses what the fit found.
    run(session, "synth")
    segment = session.spectrum()["segments"][0]
    assert np.max(np.abs(np.array(segment["synth"]) - np.array(segment["obs"]))) < 0.05

    session.revert_fit()
    assert session.state()["params"]["vrad"] == 0 and session.sme.vrad_flag == "whole"


def test_the_velocity_is_fitted_along_with_other_parameters(tmp_path, model):
    session = observed(tmp_path, model[0], receding(*model, 12))
    session.set_params({"vrad_flag": "whole", "fitparameters": ["monh"], "monh": -0.2, "vrad": 3})
    run(session, "fit")
    assert [p["name"] for p in session.fit_info["parameters"]] == ["monh"]
    assert session.fit_info["velocity"]["value"][0] == pytest.approx(12, abs=0.3)
    assert session.fit_info["velocity"]["initial"] == [3]
    session.revert_fit()
    assert session.sme.monh == pytest.approx(-0.2) and session.state()["params"]["vrad"] == 3


def test_a_fixed_velocity_is_applied_and_not_fitted(tmp_path, model):
    session = observed(tmp_path, model[0], receding(*model, 30))
    session.sme.vrad_flag = "none"
    session.set_params({"vrad": 30})                        # "none" would ignore it
    assert session.sme.vrad_flag == "fix"
    session.set_params({"fitparameters": ["monh"]})
    run(session, "fit")
    assert "velocity" not in session.fit_info and session.state()["params"]["vrad"] == 30
    stale = session.synth_stale
    session.set_params({"vrad_flag": "whole"})              # only changes what a fit does
    assert session.synth_stale == stale


def test_each_segment_gets_its_own_velocity(tmp_path, model):
    wave, flux = model
    moving = np.where(wave < 6440, receding(wave, flux, 10), receding(wave, flux, 30))
    session = observed(tmp_path, wave, moving)
    session.set_windows([[6436, 6439.8], [6440.2, 6444]])
    session.set_params({"vrad_flag": "each"})
    run(session, "fit")
    values = session.fit_info["velocity"]["value"]
    assert session.fit_info["velocity"]["each"]
    assert values == [pytest.approx(10, abs=0.5), pytest.approx(30, abs=0.5)]
    assert session.state()["params"]["vrad_each"] == [pytest.approx(v) for v in values]
    session.set_params({"vrad": 5})                         # one typed value for all
    assert session.state()["params"]["vrad_each"] == [5, 5]


def test_scripts_may_have_listed_vrad_as_a_fit_parameter(tmp_path, model):
    session = observed(tmp_path, *model)
    saved = session.save(str(tmp_path / "script.sme"))
    sme = SME_Structure.load(saved)
    sme.fitparameters, sme.vrad_flag = ["teff", "vrad"], "fix"
    sme.save(saved)
    other = Session()
    other.open_path(saved)
    assert other.sme.fitparameters == ["teff"] and other.sme.vrad_flag == "whole"


def flags(session, flag, segment=0):
    return (np.array(session.sme.mask[segment]) & flag) != 0


def test_the_velocity_mask_follows_the_line_mask_until_it_is_edited(tmp_path, model):
    session = observed(tmp_path, *model)
    session.set_params({"vrad_flag": "whole"})
    wave = np.array(session.sme.wave[0])
    follows = lambda: [s["velocity_follows"] for s in session.spectrum()["segments"]]
    assert follows() == [True] and not flags(session, VRAD).any()

    # Line edits, and velocity edits that change nothing, leave it following.
    session.set_mask(6437, 6438, LINE, remove=True)
    session.set_mask(6439, 6441, VRAD)                      # already used for it
    session.set_mask(6437.1, 6437.9, VRAD, remove=True)     # already not used
    assert follows() == [True] and not flags(session, VRAD).any()

    # The first real change writes down what was used, then applies the change.
    session.set_mask(6441, 6442, VRAD, remove=True)
    assert follows() == [False]
    inside = (wave >= 6441) & (wave <= 6442)
    np.testing.assert_array_equal(flags(session, VRAD), flags(session, LINE) & ~inside)

    # From then on the two are independent.
    session.set_mask(6443, 6443.5, LINE, remove=True)
    assert flags(session, VRAD)[(wave >= 6443) & (wave <= 6443.5)].all()
    session.set_mask(6437.2, 6437.4, VRAD)
    assert flags(session, VRAD)[(wave >= 6437.2) & (wave <= 6437.4)].all()
    assert not flags(session, LINE)[(wave >= 6437.2) & (wave <= 6437.4)].any()

    # Windows keep it; with every velocity flag gone it follows the lines again.
    session.set_windows([[6438, 6443]])
    assert follows() == [False]
    session.set_windows(None)
    session.set_mask(6436, 6444, VRAD, remove=True)
    assert follows() == [True] and not flags(session, VRAD).any()


@pytest.mark.parametrize("mode, other_segment_follows", [("each", True), ("whole", False)])
def test_two_segments_follow_alone_or_together(tmp_path, model, mode, other_segment_follows):
    session = observed(tmp_path, *model)
    session.set_windows([[6436, 6439.8], [6440.2, 6444]])
    session.set_params({"vrad_flag": mode})
    session.set_mask(6437, 6438, VRAD, remove=True)         # in the first segment
    follows = [s["velocity_follows"] for s in session.spectrum()["segments"]]
    assert follows == [False, other_segment_follows]
    # With a common velocity the second segment must keep its pixels in use.
    assert flags(session, VRAD, 1).all() == (mode == "whole")


@pytest.mark.parametrize("kept, expected", [((6436, 6440), 10), ((6440, 6444), 30)])
def test_pysme_takes_the_velocity_from_the_marked_pixels(tmp_path, model, kept, expected):
    """The two halves move differently, so the result shows which pixels count."""
    wave, flux = model
    moving = np.where(wave < 6440, receding(wave, flux, 10), receding(wave, flux, 30))
    session = observed(tmp_path, wave, moving)
    session.set_params({"vrad_flag": "whole"})
    # PySME searches +-500 km/s by default. With only the weak lines of the right
    # half marked it then matches them to the strong Ca line, 100 km/s away.
    session.sme.vrad_bounds = (-60, 60)
    for lo, hi in ((6436, kept[0]), (kept[1], 6444)):
        if hi > lo:
            session.set_mask(lo, hi, VRAD, remove=True)
    run(session, "fit")
    assert session.fit_info["velocity"]["value"][0] == pytest.approx(expected, abs=0.6)
    # The velocity flags survive the run.
    assert flags(session, VRAD).any() and not flags(session, VRAD).all()
