import numpy as np
import pytest

from pysme_gui.spectrum_io import read_text_spectrum


def write(tmp_path, text):
    path = tmp_path / "spec.txt"
    path.write_text(text)
    return str(path)


def test_two_columns_unsorted_with_duplicate(tmp_path):
    segments, notes = read_text_spectrum(write(tmp_path, "5002 0.9\n5000 1.0\n5001 0.8\n5001 0.7\n"))
    assert list(segments[0]["wave"]) == [5000, 5001, 5002]
    assert segments[0]["uncs"] is None
    assert any("repeated" in n for n in notes)


def test_header_overrides_column_order(tmp_path):
    segments, _ = read_text_spectrum(write(tmp_path, "flux,error,wavelength\n1.0,0.01,5000\n0.9,0.02,5001\n"))
    assert list(segments[0]["wave"]) == [5000, 5001]
    assert list(segments[0]["uncs"]) == [0.01, 0.02]


def test_segment_column(tmp_path):
    text = "0 5000 1 0.01\n0 5001 1 0.01\n1 6000 1 0.01\n1 6001 1 0.01\n"
    segments, _ = read_text_spectrum(write(tmp_path, text))
    assert len(segments) == 2 and segments[1]["wave"][0] == 6000


def test_bad_uncertainty_and_nan_flux(tmp_path):
    text = "5000 1 0.01\n5001 nan 0.01\n5002 1 0\n5003 1 0.01\n"
    segments, _ = read_text_spectrum(write(tmp_path, text))
    assert len(segments[0]["wave"]) == 3
    assert list(segments[0]["mask"]) == [1, 0, 1]
    assert np.all(segments[0]["uncs"] > 0)


def test_unnormalised_and_nanometre_warnings(tmp_path):
    _, notes = read_text_spectrum(write(tmp_path, "500.0 2000\n500.1 2100\n"))
    assert any("Angstrom" in n for n in notes)
    assert any("normalised" in n for n in notes)


def test_no_numbers(tmp_path):
    with pytest.raises(ValueError):
        read_text_spectrum(write(tmp_path, "hello world\n"))
