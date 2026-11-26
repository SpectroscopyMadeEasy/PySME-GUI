"""The mu values, typed by hand."""

import threading
import time

import numpy as np
import pytest

from pysme_gui.jobs import JobManager, LogBuffer
from pysme_gui.session import Session, UserError, default_mu


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


def test_the_default_values_are_pysmes():
    session = Session()
    for count in (1, 3, 7, 20):
        session.set_params({"nmu": count})
        np.testing.assert_allclose(session.sme.mu, default_mu(count))
        assert session.state()["params"]["mu_default"]


def test_own_values_are_used_saved_and_replaced_by_a_new_number(tmp_path):
    session = Session()
    session.load_example()
    standard = synthesized(session)

    session.set_params({"mu": " 0.3, 1.0;0.7 "})
    params = session.state()["params"]
    assert params["mu"] == [1.0, 0.7, 0.3] and params["nmu"] == 3
    assert not params["mu_default"] and session.synth_stale
    own = synthesized(session)
    assert np.max(np.abs(own - standard)) > 1e-4
    assert session.state()["params"]["mu"] == [1.0, 0.7, 0.3]

    other = Session()
    other.open_path(session.save(str(tmp_path / "mu.sme")))
    assert other.state()["params"]["mu"] == [1.0, 0.7, 0.3]

    # As in PySME: a number of angles brings its own values.
    session.set_params({"nmu": 7})
    assert session.state()["params"]["mu_default"]
    np.testing.assert_allclose(synthesized(session), standard, rtol=1e-8)


def test_a_single_value_ignores_rotation():
    session = Session()
    session.load_example()
    session.set_synth_setup([[6436, 6444]], 0.01)
    session.set_params({"mu": [1.0], "vsini": 0})
    still = synthesized(session)
    session.set_params({"vsini": 20})
    # Only the oversampling still depends on v sin i; 20 km/s would flatten the lines.
    np.testing.assert_allclose(synthesized(session), still, atol=2e-3)
    session.set_params({"mu": [0.2]})
    assert synthesized(session).min() > still.min() + 0.05       # centre to limb


@pytest.mark.parametrize("value, words", [
    ("", "between 1 and 77"),
    (", ".join(["0.5"] * 78), "between 1 and 77"),
    ("1, abc", "must be a number"),
    ("1, 0", "above 0"),
    ("1.2", "above 0 and at most 1"),
    ("-0.5, 0.5", "above 0"),
    ("0.5, 0.50", "different"),
    (0.5, "list of numbers"),
])
def test_unusable_values_are_refused_and_nothing_changes(value, words):
    session = Session()
    before = session.sme.mu.copy()
    with pytest.raises(UserError, match=words):
        session.set_params({"mu": value})
    np.testing.assert_array_equal(session.sme.mu, before)
