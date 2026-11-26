"""The mask is a set of flags: painting adds one, removing takes all off."""

import numpy as np
from pysme.sme import MASK_VALUES

from pysme_gui.session import Session


def observed(tmp_path):
    wave = np.linspace(6436, 6444, 801)
    path = tmp_path / "obs.txt"
    np.savetxt(path, np.column_stack([wave, np.ones_like(wave), np.full_like(wave, 0.01)]))
    session = Session()
    session.load_example()
    session.import_spectrum(str(path))
    return session, wave


def values(session, lo, hi):
    wave, mask = np.array(session.sme.wave[0]), np.array(session.sme.mask[0])
    return set(mask[(wave >= lo + 1e-3) & (wave <= hi - 1e-3)].tolist())


def test_painting_adds_a_flag_and_removing_takes_all_off(tmp_path):
    session, _ = observed(tmp_path)
    assert values(session, 6436, 6444) == {MASK_VALUES.LINE}

    session.set_mask(6437, 6439, MASK_VALUES.CONT)
    both = MASK_VALUES.LINE | MASK_VALUES.CONT
    assert values(session, 6437, 6439) == {both}
    # Such a pixel is still fitted, and also used for the continuum.
    chosen = np.array(session.sme.wave[0]) > 6437.001
    chosen &= np.array(session.sme.wave[0]) < 6438.999
    assert np.all(np.array(session.sme.mask_line[0])[chosen])
    assert np.all(np.array(session.sme.mask_cont[0])[chosen])
    session.set_mask(6437, 6439, MASK_VALUES.CONT)             # twice changes nothing
    assert values(session, 6437, 6439) == {both}

    # Continuum only: take everything off first.
    session.set_mask(6440, 6442, MASK_VALUES.BAD)
    assert values(session, 6440, 6442) == {MASK_VALUES.BAD}
    session.set_mask(6440, 6442, MASK_VALUES.CONT)
    assert values(session, 6440, 6442) == {MASK_VALUES.CONT}
    session.set_mask(6440, 6442, MASK_VALUES.LINE)
    assert values(session, 6440, 6442) == {both}


def test_stacked_flags_survive_windows_and_the_file(tmp_path):
    session, _ = observed(tmp_path)
    session.set_mask(6437, 6439, MASK_VALUES.CONT)
    session.set_windows([[6436.5, 6440]])
    assert values(session, 6437, 6439) == {3}
    session.set_mask(6437.5, 6438, MASK_VALUES.BAD)
    session.set_windows(None)
    assert values(session, 6437.5, 6438) == {0} and values(session, 6438, 6439) == {3}

    other = Session()
    other.open_path(session.save(str(tmp_path / "masked.sme")))
    np.testing.assert_array_equal(np.array(other.sme.mask[0]), np.array(session.sme.mask[0]))
    # A velocity flag from elsewhere is kept when another one is added.
    other.sme.mask[0][:10] = MASK_VALUES.VRAD
    other.obs[0]["mask"][:10] = MASK_VALUES.VRAD
    wave = np.array(other.sme.wave[0])
    other.set_mask(wave[0], wave[9], MASK_VALUES.LINE)
    assert set(np.array(other.sme.mask[0])[:10].tolist()) == {MASK_VALUES.VRAD | MASK_VALUES.LINE}
