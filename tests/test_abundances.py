"""Abundances as [X/Fe] against a solar reference, and turning NLTE off."""

import numpy as np
import pytest

from pysme_gui.session import (
    DEFAULT_SOLAR_REFERENCE,
    Session,
    UserError,
    fit_ratio,
    solar_abundances,
)


def element(session, name):
    return next(r for r in session.abundance_table()["elements"] if r["el"] == name)


def sun(name, reference=DEFAULT_SOLAR_REFERENCE):
    from pysme.abund import elements

    return solar_abundances(reference)[list(elements).index(name)]


def test_a_new_star_is_solar_in_proportion():
    session = Session()
    session.set_params({"monh": -0.5})
    table = session.abundance_table()
    assert table["reference"] == session.state()["solar_reference"] == DEFAULT_SOLAR_REFERENCE
    assert table["feh"] == pytest.approx(-0.5)
    ratios = [r["ratio"] for r in table["elements"] if r["el"] not in ("H", "He", "Fe") and r["ratio"] is not None]
    assert len(ratios) == 80           # grevesse2007 has no value for the others
    np.testing.assert_allclose(ratios, 0, atol=1e-9)
    assert element(session, "Fe")["ratio"] == pytest.approx(-0.5)      # [Fe/H]
    assert element(session, "H")["ratio"] is None and element(session, "He")["ratio"] is None
    assert element(session, "Tc")["solar"] is None and element(session, "Tc")["ratio"] is None


def test_a_ratio_typed_changes_that_element_only():
    session = Session()
    session.set_params({"monh": -0.5})
    session.set_abundance("Mg", "0.3", "ratio")
    assert session.sme.abund.A["Mg"] == pytest.approx(sun("Mg") + 0.3 - 0.5)
    assert element(session, "Mg")["ratio"] == pytest.approx(0.3)

    # The Fe box holds [Fe/H]. Mg keeps its A(Mg), so its [Mg/Fe] moves.
    magnesium = session.sme.abund.A["Mg"]
    session.set_abundance("Fe", -0.7, "ratio")
    assert session.sme.abund.A["Fe"] == pytest.approx(sun("Fe") - 0.7)
    assert session.sme.abund.A["Mg"] == pytest.approx(magnesium)
    assert element(session, "Mg")["ratio"] == pytest.approx(0.5)
    assert session.abundance_table()["feh"] == pytest.approx(-0.7)

    # A further [Mg/Fe] is against the Fe there is now.
    session.set_abundance("Mg", 0.1, "ratio")
    assert session.sme.abund.A["Mg"] == pytest.approx(sun("Mg") + 0.1 - 0.7)

    # [M/H] moves Fe and Mg together.
    session.set_params({"monh": -1.0})
    assert element(session, "Mg")["ratio"] == pytest.approx(0.1)
    assert session.abundance_table()["feh"] == pytest.approx(-1.2)

    session.set_abundance("Mg", None, "ratio")
    assert session.sme.abund.A["Mg"] == pytest.approx(sun("Mg") - 1.0)


@pytest.mark.parametrize("name, value, scale, words", [
    ("He", 0.1, "ratio", "given as A\\(He\\) only"),
    ("H", 0.1, "ratio", "Cannot change"),
    ("Tc", 0.1, "ratio", "no solar value for Tc"),
    ("Mg", "abc", "ratio", "\\[Mg/Fe\\]"),
    ("Mg", 7.0, "x/h", "Unknown abundance scale"),
])
def test_what_cannot_be_typed_as_a_ratio(name, value, scale, words):
    session = Session()
    before = np.array(session.sme.abund._pattern, float)
    with pytest.raises(UserError, match=words):
        session.set_abundance(name, value, scale)
    np.testing.assert_array_equal(np.array(session.sme.abund._pattern, float), before)


def test_the_reference_changes_only_how_abundances_are_written(tmp_path):
    session = Session()
    session.load_example()
    session.set_abundance("Mg", 7.8)
    session.synth_stale = False
    pattern = np.array(session.sme.abund._pattern, float)
    before = element(session, "Mg")["ratio"]

    session.set_solar_reference("anders1989")
    np.testing.assert_array_equal(np.array(session.sme.abund._pattern, float), pattern)
    assert not session.synth_stale and session.dirty
    expected = before - (sun("Mg", "anders1989") - sun("Mg")) + (sun("Fe", "anders1989") - sun("Fe"))
    assert element(session, "Mg")["ratio"] == pytest.approx(expected)
    assert abs(expected - before) > 0.05
    with pytest.raises(UserError, match="Unknown abundance pattern"):
        session.set_solar_reference("nobody2000")

    other = Session()
    other.open_path(session.save(str(tmp_path / "reference.sme")))
    assert other.solar_reference == "anders1989"
    other.reset()
    assert other.solar_reference == DEFAULT_SOLAR_REFERENCE

    # A file that does not say, as one saved by a script.
    session.sme.meta = {}
    path = str(tmp_path / "script.sme")
    session.sme.save(path)
    other.open_path(path)
    assert other.solar_reference == DEFAULT_SOLAR_REFERENCE

    # Applying a solar pattern makes it the reference.
    session.apply_abund_pattern("asplund2009")
    assert session.solar_reference == "asplund2009"
    assert element(session, "Mg")["ratio"] == pytest.approx(0, abs=1e-9)


def test_which_errors_a_fitted_ratio_gets():
    from pysme.abund import elements

    solar = solar_abundances(DEFAULT_SOLAR_REFERENCE)
    a_fe = sun("Fe") - 0.4
    calcium = {"name": "abund Ca", "value": sun("Ca") - 0.1, "unc_fit": None, "unc_sme": None,
               "solver": {"unc_fit": 0.02, "unc_sme": 0.05}}
    # [M/H] was free, so A(Ca) has no error; [Ca/Fe] does not contain [M/H].
    ratio = fit_ratio(calcium, a_fe, ["monh", "abund Ca"], solar)
    assert ratio["label"] == "[Ca/Fe]" and ratio["value"] == pytest.approx(0.3)
    assert (ratio["unc_fit"], ratio["unc_sme"]) == (0.02, 0.05)
    # Fe solved for too: the two are correlated.
    ratio = fit_ratio(calcium, a_fe, ["abund Fe", "abund Ca"], solar)
    assert ratio["unc_fit"] is None and ratio["unc_sme"] is None
    iron = {"name": "abund Fe", "value": a_fe, "unc_fit": 0.01, "unc_sme": 0.03}
    ratio = fit_ratio(iron, a_fe, ["abund Fe"], solar)
    assert ratio["label"] == "[Fe/H]" and ratio["value"] == pytest.approx(-0.4)
    assert (ratio["unc_fit"], ratio["unc_sme"]) == (0.01, 0.03)
    # Nothing to write it against.
    assert fit_ratio(calcium, None, ["abund Ca"], solar) is None
    assert fit_ratio({**calcium, "name": "abund He"}, a_fe, [], solar) is None
    assert "Tc" in elements
    assert fit_ratio({**calcium, "name": "abund Tc"}, a_fe, [], solar) is None


def test_all_nlte_can_be_turned_off_at_once():
    session = Session()
    session.load_example()
    session.set_nlte("Fe", True)
    session.set_nlte("Mg", True)
    revision = session.revision
    session.nlte_off()
    assert session.state()["nlte"]["elements"] == [] and session.revision > revision
    revision = session.revision
    session.nlte_off()
    assert session.revision == revision
