"""Read observed spectra from plain text / CSV files."""

import re

import numpy as np

_NAMES = {
    "wave": {"wave", "wavelength", "wl", "lambda", "wav", "waveobs", "wave_aa"},
    "spec": {"flux", "spec", "obs", "intensity", "norm_flux", "flux_norm"},
    "uncs": {"err", "error", "uncs", "unc", "sigma", "eflux", "flux_err", "flux_error"},
    "mask": {"mask", "flag_mask"},
    "segment": {"segment", "seg", "order"},
}


def _split(line):
    return [p for p in re.split(r"[,\s;|]+", line.strip()) if p]


def _to_float(text):
    try:
        return float(text)
    except ValueError:
        return None


def _header_columns(parts):
    """Map column roles to indices from a header row, or None if unusable."""
    cols = {}
    for i, raw in enumerate(parts):
        name = raw.strip().strip("\"'#").lower()
        for role, aliases in _NAMES.items():
            if name in aliases and role not in cols:
                cols[role] = i
    if "wave" in cols and "spec" in cols:
        return cols
    return None


def _guess_columns(table):
    ncol = table.shape[1]
    if ncol >= 4:
        first = table[:, 0]
        if np.all(first == np.round(first)) and np.nanmax(first) < 500:
            return {"segment": 0, "wave": 1, "spec": 2, "uncs": 3}
        cols = {"wave": 0, "spec": 1, "uncs": 2, "mask": 3}
        return cols
    if ncol == 3:
        first = table[:, 0]
        if np.all(first == np.round(first)) and np.nanmax(first) < 500:
            return {"segment": 0, "wave": 1, "spec": 2}
        return {"wave": 0, "spec": 1, "uncs": 2}
    return {"wave": 0, "spec": 1}


def read_text_spectrum(path):
    """Read a text spectrum.

    Accepted layouts, with or without a header row:
    ``wave flux``, ``wave flux uncertainty``, ``wave flux uncertainty mask``,
    ``segment wave flux [uncertainty]``. A header row naming the columns
    overrides the guess.

    Returns
    -------
    segments : list of dict
        Each with float arrays ``wave``, ``spec``, ``uncs`` (or None) and the
        integer array ``mask``.
    notes : list of str
        Things the user should know about how the file was interpreted.
    """
    rows = []
    header = None
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith(("#", "//", "%", "!")):
                if not rows:
                    header = _header_columns(_split(stripped.lstrip("#/%! "))) or header
                continue
            parts = _split(stripped)
            values = [_to_float(p) for p in parts]
            if len(parts) < 2 or any(v is None for v in values):
                if not rows:
                    header = _header_columns(parts) or header
                continue
            rows.append(values)
    if not rows:
        raise ValueError("No numeric spectrum rows found in this file.")

    ncol = min(len(r) for r in rows)
    table = np.array([r[:ncol] for r in rows], dtype=float)
    cols = header if header and max(header.values()) < ncol else _guess_columns(table)
    used = ", ".join(f"{k}=column {v + 1}" for k, v in sorted(cols.items(), key=lambda kv: kv[1]))
    notes = [f"Columns read as {used}."]

    wave = table[:, cols["wave"]]
    spec = table[:, cols["spec"]]
    keep = np.isfinite(wave) & np.isfinite(spec)
    if not np.all(keep):
        notes.append(f"Dropped {int((~keep).sum())} rows with non-finite wavelength or flux.")
    table = table[keep]
    if len(table) == 0:
        raise ValueError("All rows have non-finite wavelength or flux.")

    if "segment" in cols:
        ids = table[:, cols["segment"]]
        groups = [table[ids == v] for v in np.unique(ids)]
    else:
        groups = [table]

    segments = []
    for group in groups:
        order = np.argsort(group[:, cols["wave"]], kind="stable")
        group = group[order]
        wave = group[:, cols["wave"]]
        unique = np.concatenate([[True], np.diff(wave) > 0])
        if not np.all(unique):
            notes.append(f"Dropped {int((~unique).sum())} rows with a repeated wavelength.")
        group = group[unique]
        wave = group[:, cols["wave"]]
        spec = group[:, cols["spec"]]
        mask = np.ones(len(wave), dtype=int)
        if "mask" in cols:
            raw = group[:, cols["mask"]]
            mask = np.where(np.isfinite(raw), raw, 0).astype(int)
        uncs = None
        if "uncs" in cols:
            uncs = np.array(group[:, cols["uncs"]], dtype=float)
            bad = ~np.isfinite(uncs) | (uncs <= 0)
            if np.any(bad):
                mask[bad] = 0
                good = uncs[~bad]
                uncs[bad] = np.median(good) if good.size else 1.0
                notes.append(f"{int(bad.sum())} pixels with unusable uncertainty marked bad.")
        if len(wave) >= 2:
            segments.append({"wave": wave.copy(), "spec": spec.copy(), "uncs": uncs, "mask": mask})
    if not segments:
        raise ValueError("The file needs at least two pixels.")

    median_wave = float(np.median(np.concatenate([s["wave"] for s in segments])))
    if median_wave < 2000:
        notes.append(
            f"Median wavelength is {median_wave:.1f}. PySME expects Angstrom; check the units."
        )
    median_flux = float(np.median(np.concatenate([s["spec"] for s in segments])))
    if not 0.3 < median_flux < 1.7:
        notes.append(
            f"Median flux is {median_flux:.3g}. The spectrum does not look continuum "
            "normalised; normalise it first or choose a continuum fit."
        )
    return segments, notes
