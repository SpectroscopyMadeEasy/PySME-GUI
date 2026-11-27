"""The state behind the GUI window: one SME structure plus a little bookkeeping.

All science objects are real PySME objects. The browser side only ever sees the
JSON produced here, so there is no second implementation of the SME file format.
"""

import functools
import io
import math
import os
import re
import time

import numpy as np
from pysme.abund import Abund
from pysme.continuum_and_radial_velocity import apply_continuum
from pysme.linelist.vald import ValdFile
from pysme.sme import MASK_VALUES, SME_Structure

from . import citations
from .spectrum_io import read_text_spectrum

SME_EXTENSIONS = {".sme", ".inp", ".sav", ".out"}
LINELIST_EXTENSIONS = {".lin"}
EXAMPLE_LINELIST = os.path.join(os.path.dirname(__file__), "examples", "sun.lin")

ABUND_PATTERNS = [
    "asplund2021", "asplund2009", "grevesse2007", "asplund2005", "grevesse1998",
    "grevesse1996", "anders1989", "lodders2010", "lodders2003", "amarsi2026",
]
# What abundances are written relative to, unless chosen otherwise. PySME starts
# a structure with this table, so a new star reads [X/Fe] = 0.
DEFAULT_SOLAR_REFERENCE = "grevesse2007"

# name -> (attribute, minimum or None, strictly above minimum)
FLOAT_PARAMS = {
    "teff": ("teff", 0.0, True),
    "logg": ("logg", None, False),
    "monh": ("monh", None, False),
    "vmic": ("vmic", 0.0, False),
    "vmac": ("vmac", 0.0, False),
    "vsini": ("vsini", 0.0, False),
    "gam6": ("gam6", 0.0, False),
    "accwi": ("accwi", 0.0, True),
    "accrt": ("accrt", 0.0, True),
}
BOOL_PARAMS = {"h2broad"}
CHOICE_PARAMS = {
    "vrad_flag": ["none", "fix", "whole", "each"],
    "cscale_flag": ["none", "fix", "constant", "linear", "quadratic"],
    "cscale_type": [
        "mask", "match", "match+mask", "matchlines", "matchlines+mask",
        "spline", "spline+mask", "mcmc",
    ],
}
BASIC_FIT_PARAMS = ["teff", "logg", "monh", "vmic", "vmac", "vsini"]
LINE_COLUMNS = ["species", "wlcent", "excit", "gflog", "gamrad", "gamqst", "gamvw", "depth"]
# The line data that enters the synthesis; species and VALD's depth estimate do not.
LINE_EDITABLE = {
    "wlcent": "Wavelength", "excit": "Excitation potential", "gflog": "log gf",
    "gamrad": "Radiative damping", "gamqst": "Stark damping", "gamvw": "van der Waals damping",
}
LIGHT_SPEED = 299792.458       # km/s
VELOCITY_FREE = ("whole", "each")   # vrad_flag values with which a fit finds it

MAX_RUNS = 8
MAX_PLOT_POINTS = 600_000


def canonical_parameter(name):
    """One spelling per fit parameter.

    PySME rewrites ``abund Ca`` as ``Abund ca`` while solving.
    """
    parts = str(name).strip().split()
    if len(parts) == 2 and parts[0].lower() == "abund":
        return f"abund {parts[1].capitalize()}"
    return str(name).strip().lower()


class UserError(Exception):
    """A problem the user can fix; the message is shown as is."""


def clean(value):
    """Convert numpy / non-finite values into plain JSON-safe Python."""
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, np.ndarray):
        return [clean(v) for v in value.tolist()]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def parse_float(value, name):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise UserError(f"{name} must be a number.")
    if not math.isfinite(number):
        raise UserError(f"{name} must be finite.")
    return number


MAX_MU = 77        # SMElib's limit on the number of angles


def default_mu(count):
    """The angles PySME takes for a number of them: the middles of rings of
    equal area, from the disk centre to the limb."""
    return np.sqrt(0.5 * (2 * np.arange(count) + 1) / count)[::-1]


def parse_mu(value):
    """Angles typed as text ("0.9, 0.5 0.2") or given as a list."""
    if isinstance(value, str):
        value = [part for part in re.split(r"[\s,;]+", value) if part]
    if not isinstance(value, (list, tuple)):
        raise UserError("Give the mu values as a list of numbers.")
    if not 1 <= len(value) <= MAX_MU:
        raise UserError(f"Give between 1 and {MAX_MU} mu values.")
    numbers = np.array([parse_float(v, "Each mu value") for v in value])
    # PySME itself accepts 0 and repeated values and computes something anyway.
    if np.any(numbers <= 0) or np.any(numbers > 1):
        raise UserError("Each mu value must be above 0 and at most 1.")
    if len(np.unique(numbers)) != len(numbers):
        raise UserError("The mu values must all be different.")
    return numbers


NO_RATIO = ("H", "He")     # [M/H] does not apply to them, so they are given as A


@functools.lru_cache(maxsize=None)
def solar_abundances(name):
    """A(X) of the Sun in one of PySME's tables, by atomic number - 1; NaN where
    the table has no value."""
    values = np.array(Abund(monh=0, pattern=name)._pattern, dtype=float)
    values.setflags(write=False)
    return values


def ratio_label(element):
    return "[Fe/H]" if element == "Fe" else f"[{element}/Fe]"


def fit_ratio(row, a_fe, names, solar):
    """A fitted abundance relative to the Sun, or None if it cannot be written so.

    [X/Fe] = pattern(X) - pattern(Fe) - the Sun's difference: [M/H] drops out.
    So the solver's own errors, which are on the pattern scale, hold for [X/Fe]
    even if [M/H] was free too. If Fe was solved for as well, the two are
    correlated and no error is given. They never include the Sun's or Fe's own.
    """
    from pysme.abund import elements

    element = row["name"].split()[1]
    order = list(elements)
    sun = solar[order.index(element)]
    value = row.get("value")
    if element in NO_RATIO or value is None or not math.isfinite(sun):
        return None
    errors = {"unc_fit": None, "unc_sme": None}
    if element == "Fe":
        value = value - sun
        errors = {key: row.get(key) for key in errors}     # those of A(Fe)
    else:
        iron = solar[order.index("Fe")]
        if a_fe is None or not math.isfinite(iron):
            return None
        value = value - sun - (a_fe - iron)
        if "abund Fe" not in names:
            solver = row.get("solver") or {}
            errors = {key: solver.get(key) for key in errors}
    return {"label": ratio_label(element), "value": float(value), **errors}


def default_sme():
    sme = SME_Structure()
    sme.teff, sme.logg, sme.monh = 5770.0, 4.44, 0.0
    sme.vmic, sme.vmac, sme.vsini = 1.0, 0.0, 0.0
    # The model atmosphere grid is PySME's own default; the interface does not choose one.
    return sme


def _segments(vector):
    if vector is None:
        return None
    return [np.asarray(vector[i]) for i in range(len(vector))]


def software_versions():
    import pysme

    from . import __version__

    try:
        from pysme.sme_synth import SME_DLL

        smelib = str(SME_DLL().SMELibraryVersion())
        # "SME Library version: ... [SMElib ref: v6.13.20]" -> "v6.13.20"
        reference = re.search(r"ref: (v[\w.]+)", smelib)
        smelib = reference.group(1) if reference else smelib
    except Exception:
        smelib = "unknown"
    return {"gui": __version__, "pysme": pysme.__version__, "smelib": smelib}


class Session:
    def __init__(self):
        self.revision = 0
        self.linelist_version = 0     # counts the lists that have been loaded
        self.reset()

    # ------------------------------------------------------------------ basics

    def reset(self):
        self.sme = default_sme()
        self.path = None
        self.linelist_file = None
        self._new_linelist()
        self.obs = None          # full observation, list of segment dicts
        self.windows = None      # list of [start, end] cut out of the observation
        self.has_uncs = False
        self.snr = 100.0
        self.ranges = []         # synthesis ranges when there is no observation
        self.step = None
        self.runs = []
        self._run_counter = 0
        self.synth_stale = False
        self.synth_correction = None   # continuum correction inside that spectrum
        self.synth_vrad = None         # velocity per segment it was shifted by, km/s
        self.computed_with = None  # versions that produced the current spectrum
        self.fit_info = None
        self.notes = []
        self.solar_reference = DEFAULT_SOLAR_REFERENCE
        self._remember_abund_baseline()
        self.dirty = False
        self.touch(dirty=False)

    def touch(self, dirty=True, stale=False):
        self.revision += 1
        if dirty:
            self.dirty = True
        if stale and self.sme.synth is not None:
            self.synth_stale = True

    def _remember_abund_baseline(self):
        abund = self.sme.abund
        self.abund_baseline = np.array(abund._pattern, dtype=float)
        self.abund_baseline_name = "the values at load time"

    # ----------------------------------------------------------------- loading

    def open_path(self, path):
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isfile(path):
            raise UserError(f"File not found: {path}")
        ext = os.path.splitext(path)[1].lower()
        if ext in SME_EXTENSIONS:
            self.load_sme(path)
        elif ext in LINELIST_EXTENSIONS:
            self.load_linelist(path)
        elif ext in {".npy", ".npz"}:
            raise UserError(
                "NumPy pickle files can run arbitrary code when opened, so the GUI "
                "does not load them. Convert the structure to .sme with PySME first."
            )
        else:
            self.import_spectrum(path)

    def load_sme(self, path):
        sme = SME_Structure.load(path)
        self.reset()
        self.sme = sme
        # IDL files can be read but not written, so never save back over them.
        self.path = path if path.lower().endswith(".sme") else None
        sme.fitparameters = list(dict.fromkeys(
            canonical_parameter(n) for n in (sme.fitparameters or [])
        ))
        if "vrad" in sme.fitparameters:       # how scripts may ask PySME to fit it
            sme.fitparameters = [n for n in sme.fitparameters if n != "vrad"]
            if sme.vrad_flag not in VELOCITY_FREE:
                sme.vrad_flag = "whole"
        meta = sme.meta or {}
        setup = meta.get("gui_synth_setup")
        self.synth_stale = bool(meta.get("gui_synth_stale", False))
        if "gui_synth_continuum" in meta:
            self.synth_correction = meta["gui_synth_continuum"]
        else:
            self.synth_correction = self._applied_continuum(sme, sme.spec is not None)
        if "gui_synth_vrad" in meta:
            self.synth_vrad = meta["gui_synth_vrad"]
        else:
            self.synth_vrad = None if sme.spec is None else self._velocities(sme)
        # Saved by row, and the saved order is the order the rows now have.
        self.line_edits = {
            int(row): dict(was) for row, was in (meta.get("gui_line_edits") or {}).items()
        }
        used = meta.get("gui_computed_with")
        if isinstance(used, dict) and {"gui", "pysme", "smelib"} <= set(used):
            self.computed_with = dict(used)
        self.linelist_file = meta.get("gui_linelist_file")
        if meta.get("gui_solar_reference") in ABUND_PATTERNS:
            self.solar_reference = meta["gui_solar_reference"]
        self._remember_abund_baseline()

        if sme.spec is not None and sme.wave is not None:
            self._adopt_observation_from_sme()
            assumed = meta.get("gui_assumed_snr")
            if assumed:
                self.has_uncs = False
                self.snr = float(assumed)
        elif setup:
            self.ranges = [[float(a), float(b)] for a, b in setup.get("ranges", [])]
            self.step = setup.get("step")
        elif sme.wran is not None:
            self.ranges = [[float(a), float(b)] for a, b in np.atleast_2d(sme.wran)]

        if sme.fitresults is not None and sme.fitresults.parameters is not None:
            self.fit_info = meta.get("gui_fit_info") or self._read_fit_results(sme, None)
        self.notes = [f"Opened {os.path.basename(path)}."]
        self.touch(dirty=False)

    def _adopt_observation_from_sme(self):
        sme = self.sme
        waves = _segments(sme.wave)
        specs = _segments(sme.spec)
        uncs = _segments(sme.uncs)
        masks = _segments(sme.mask)
        self.obs = []
        for i, (wave, spec) in enumerate(zip(waves, specs)):
            mask = masks[i] if masks is not None else np.ones(len(wave))
            self.obs.append({
                "wave": np.array(wave, float),
                "spec": np.array(spec, float),
                "uncs": np.array(uncs[i], float) if uncs is not None else None,
                "mask": np.array(mask).astype(int),
            })
        self.has_uncs = uncs is not None
        self.windows = None

    def import_spectrum(self, path):
        segments, notes = read_text_spectrum(path)
        self.obs = segments
        self.windows = None
        self.has_uncs = all(s["uncs"] is not None for s in segments)
        self.fit_info = None
        self.runs = []
        self._apply_windows()
        self.notes = [f"Imported {os.path.basename(path)}."] + notes
        self.touch()

    def remove_observation(self):
        if self.obs is None:
            return
        wran = [[float(s["wave"][0]), float(s["wave"][-1])] for s in self.obs]
        self.ranges = self.windows or wran
        self.obs = None
        self.windows = None
        self.has_uncs = False
        self.fit_info = None
        self._clear_wave()
        self.touch()

    def _clear_wave(self):
        sme = self.sme
        for name in ("synth", "cont", "spec", "uncs", "mask", "telluric"):
            setattr(sme, name, None)
        sme.wave = None
        sme.wran = np.array(self.ranges) if self.ranges else None
        sme.vrad = None if sme.vrad_flag == "none" else sme.vrad[:1]
        sme.cscale = None
        self.synth_stale = False

    def set_windows(self, windows):
        """Restrict the observation to a list of wavelength windows."""
        if self.obs is None:
            raise UserError("There is no observed spectrum.")
        parsed = None
        if windows:
            parsed = self._parse_ranges(windows)
        previous = self.windows
        self.windows = parsed
        try:
            self._apply_windows()
        except UserError:
            self.windows = previous
            raise
        self.fit_info = None
        self.touch()

    def _window_slices(self):
        if not self.windows:
            return [(seg, np.ones(len(seg["wave"]), bool)) for seg in self.obs]
        slices = []
        for start, end in self.windows:
            for seg in self.obs:
                sel = (seg["wave"] >= start) & (seg["wave"] <= end)
                if sel.sum() >= 2:
                    slices.append((seg, sel))
        return slices

    def _apply_windows(self):
        slices = self._window_slices()
        if not slices:
            raise UserError("No observed pixels fall inside these windows.")
        sme = self.sme
        for name in ("synth", "cont", "spec", "uncs", "mask", "telluric"):
            setattr(sme, name, None)
        sme.wave = None
        waves = [seg["wave"][sel].copy() for seg, sel in slices]
        sme.wave = waves
        sme.spec = [seg["spec"][sel].copy() for seg, sel in slices]
        if self.has_uncs:
            sme.uncs = [seg["uncs"][sel].copy() for seg, sel in slices]
        sme.mask = [seg["mask"][sel].copy() for seg, sel in slices]
        sme.vrad = None if sme.vrad_flag == "none" else np.resize(sme.vrad, len(waves))
        sme.cscale = None
        self.synth_stale = False

    def load_linelist(self, path):
        path = os.path.abspath(os.path.expanduser(path))
        try:
            linelist = ValdFile(path)
        except Exception as exc:
            raise UserError(
                "Could not read this as a VALD line list (extract stellar / extract all, "
                f"short or long format): {exc}"
            )
        if len(linelist) == 0:
            raise UserError("The line list contains no lines.")
        self.sme.linelist = linelist
        self._new_linelist()
        self.linelist_file = path
        self.notes = [f"Loaded {len(linelist)} lines from {os.path.basename(path)}."]
        self.touch(stale=True)

    # ------------------------------------------------------------- line data

    def _new_linelist(self):
        """Forget what belonged to the previous list."""
        self.line_edits = {}    # line id -> {column: value before the first edit}
        self.line_ids = None    # the id of the line in each row, once rows move
        self.linelist_version += 1

    def _line_bibtex(self):
        """The bibliography of the line list: PySME's, and what it skips."""
        linelist = self.sme.linelist
        if not len(linelist):
            return ""
        cached = getattr(self, "_line_bib", None)
        if cached is None or cached[0] != self.linelist_version:
            texts = linelist._lines["reference"] if "reference" in linelist._lines else []
            extra = citations.missed_by_pysme(
                texts, citations.known_keys(linelist.citation_info),
                linelist.lineformat == "long",
            )
            cached = (self.linelist_version, extra)
            self._line_bib = cached
        return (linelist.citation_info or "") + cached[1]

    def _ids(self):
        count = len(self.sme.linelist)
        if self.line_ids is None or len(self.line_ids) != count:
            self.line_ids = np.arange(count)
        return self.line_ids

    def _row_of(self, line_id):
        try:
            rows = np.flatnonzero(self._ids() == int(line_id))
        except (TypeError, ValueError):
            rows = []
        if not len(rows):
            raise UserError("That line is not in the line list.")
        return int(rows[0])

    def line(self, line_id):
        """One line: where it is in the table, its data, and what was changed."""
        row = self._row_of(line_id)
        frame = self.sme.linelist._lines
        return clean({
            "id": int(line_id), "row": row, "species": str(frame["species"].iat[row]),
            "values": {c: frame[c].iat[row] for c in LINE_EDITABLE if c in frame.columns},
            "original": self.line_edits.get(int(line_id), {}),
        })

    def set_line(self, line_id, column, value):
        row = self._row_of(line_id)
        frame = self.sme.linelist._lines
        if column not in LINE_EDITABLE or column not in frame.columns:
            raise UserError(f"{column} cannot be edited.")
        number = parse_float(value, LINE_EDITABLE[column])
        if column == "wlcent" and number <= 0:
            raise UserError("The wavelength must be positive.")
        if column == "excit" and number < 0:
            raise UserError("The excitation potential cannot be negative.")
        old = float(frame[column].iat[row])
        if number == old:
            return
        edits = dict(self.line_edits.get(int(line_id), {}))
        if number == edits.setdefault(column, old):
            del edits[column]                      # back to what the file had
        if edits:
            self.line_edits[int(line_id)] = edits
        else:
            self.line_edits.pop(int(line_id), None)
        self._write_line(row, column, number)
        self._lines_changed()

    def reset_lines(self, line_id=None):
        """Give one line, or every line, the values it was loaded with."""
        targets = list(self.line_edits) if line_id is None else [int(line_id)]
        changed = False
        for target in targets:
            for column, original in self.line_edits.pop(target, {}).items():
                self._write_line(self._row_of(target), column, original)
                changed = True
        if changed:
            self._lines_changed()

    def _write_line(self, row, column, number):
        frame = self.sme.linelist._lines
        ids = self._ids()
        frame.iat[row, frame.columns.get_loc(column)] = number
        if column == "wlcent":
            # SMElib needs the list in order of wavelength.
            order = np.argsort(frame["wlcent"].to_numpy(), kind="stable")
            self.sme.linelist._lines = frame.iloc[order].reset_index(drop=True)
            self.line_ids = ids[order]

    def _lines_changed(self):
        # PySME can keep which lines matter and how far each one reaches. Both
        # depend on the line data, but its test for "out of date" looks only at
        # the star, so have them worked out again.
        for name in ("almax_paras", "cdr_paras"):
            setattr(self.sme.linelist, name, None)
        self.touch(stale=True)

    def load_example(self):
        self.reset()
        sme = self.sme
        sme.vmac, sme.vsini = 3.0, 1.6
        self.load_linelist(EXAMPLE_LINELIST)
        self.ranges = [[6436.0, 6444.0]]
        sme.wran = np.array(self.ranges)
        self.notes = [
            "Solar example: 8 A around 6440 A with a small VALD line list. "
            "Press Synthesize. The first run downloads the MARCS model grid."
        ]
        self.dirty = False   # nothing of the user's would be lost
        self.touch(dirty=False)

    # ----------------------------------------------------------------- editing

    def set_params(self, updates):
        sme = self.sme
        changed_physics = False
        for name, value in updates.items():
            if name in FLOAT_PARAMS:
                attr, minimum, strict = FLOAT_PARAMS[name]
                number = parse_float(value, name)
                if minimum is not None and (number < minimum or (strict and number <= minimum)):
                    raise UserError(f"{name} must be {'>' if strict else '>='} {minimum:g}.")
                setattr(sme, attr, number)
                changed_physics = True
            elif name in BOOL_PARAMS:
                setattr(sme, name, bool(value))
                changed_physics = True
            elif name == "vrad_flag":
                if value not in CHOICE_PARAMS[name]:
                    raise UserError(f"Unknown value for {name}: {value}")
                # Whether the velocity is free changes no spectrum, only what a
                # fit does. Switching it off altogether drops a shift, though.
                if value == "none" and np.any(np.atleast_1d(sme.vrad) != 0):
                    changed_physics = True
                sme.vrad_flag = value
            elif name in CHOICE_PARAMS:
                if value not in CHOICE_PARAMS[name]:
                    raise UserError(f"Unknown value for {name}: {value}")
                setattr(sme, name, value)
                changed_physics = True
            elif name == "nmu":
                number = int(parse_float(value, name))
                if not 1 <= number <= 77:
                    raise UserError("Number of mu angles must be between 1 and 77.")
                sme.nmu = number           # PySME puts its default values in
                changed_physics = True
            elif name == "mu":
                sme.mu = parse_mu(value)   # stored from disk centre to limb
                changed_physics = True
            elif name == "ipres":
                number = parse_float(value, "Resolving power")
                if number < 0:
                    raise UserError("Resolving power cannot be negative.")
                sme.ipres = number
                if number == 0:
                    sme.iptype = None
                elif sme.iptype is None:
                    sme.iptype = "gauss"
                changed_physics = True
            elif name == "vrad":
                number = parse_float(value, "Radial velocity")
                if sme.vrad_flag == "none":
                    sme.vrad_flag = "fix"          # PySME ignores a value when "none"
                sme.vrad = np.full(sme.nseg or 1, number)
                changed_physics = True
            elif name == "snr":
                number = parse_float(value, "S/N")
                if number <= 0:
                    raise UserError("S/N must be positive.")
                self.snr = number
            elif name == "fitparameters":
                sme.fitparameters = self._parse_fit_parameters(value)
            elif name.startswith("atmo."):
                key = name.split(".", 1)[1]
                if key not in {"source", "method", "geom", "depth", "interp"}:
                    raise UserError(f"Unknown atmosphere setting: {key}")
                if value in ("", "auto"):
                    value = None
                if key == "source" and not value:
                    raise UserError("Choose an atmosphere model grid.")
                setattr(sme.atmo, key, value)
                changed_physics = True
            else:
                raise UserError(f"Unknown parameter: {name}")
        self.touch(stale=changed_physics)

    def _parse_fit_parameters(self, values):
        from pysme.abund import elements

        result = []
        for raw in values or []:
            parts = str(raw).split()
            if len(parts) == 2 and parts[0].lower() == "abund":
                element = parts[1].capitalize()
                if element not in elements or element == "H":
                    raise UserError(f"Cannot fit the abundance of '{parts[1]}'.")
                name = f"abund {element}"
            else:
                name = str(raw).strip().lower()
                if name not in BASIC_FIT_PARAMS:
                    raise UserError(f"Unsupported fit parameter: {raw}")
            if name not in result:
                result.append(name)
        return result

    @staticmethod
    def _parse_ranges(ranges):
        parsed = []
        for item in ranges:
            start = parse_float(item[0], "Range start")
            end = parse_float(item[1], "Range end")
            if not 0 < start < end:
                raise UserError(f"Invalid wavelength range {start:g} - {end:g}.")
            parsed.append([start, end])
        parsed.sort()
        for (_, end), (start, _) in zip(parsed, parsed[1:]):
            if start < end:
                raise UserError("Wavelength ranges must not overlap.")
        return parsed

    def set_synth_setup(self, ranges, step):
        if self.obs is not None:
            raise UserError("The wavelength grid comes from the observed spectrum.")
        parsed = self._parse_ranges(ranges)
        if step in (None, ""):
            step = None
        else:
            step = parse_float(step, "Wavelength step")
            if step <= 0:
                raise UserError("Wavelength step must be positive.")
            if sum((b - a) / step for a, b in parsed) > MAX_PLOT_POINTS:
                raise UserError("This step gives too many pixels. Use a coarser step.")
        if parsed == self.ranges and step == self.step:
            return
        self.ranges, self.step = parsed, step
        self.touch(stale=True)

    def set_mask(self, start, end, value, remove=False):
        """Add a mask flag to the pixels from start to end, or take it off.

        PySME's mask values are flags that a pixel can combine: it fits those with
        LINE, finds the continuum from those with CONT and the velocity from those
        with VRAD. So marking continuum leaves a line pixel in the fit. Removing
        the value 0 takes every flag off, and the pixel is not used.
        """
        if self.obs is None or self.sme.mask is None:
            raise UserError("Import an observed spectrum before editing the mask.")
        value = int(value)
        remove = bool(remove) or value == 0
        flags = {int(MASK_VALUES.LINE), int(MASK_VALUES.CONT), int(MASK_VALUES.VRAD)}
        if value not in (flags | {0} if remove else flags):
            raise UserError("Unknown mask value.")
        start, end = sorted((parse_float(start, "start"), parse_float(end, "end")))
        if value == MASK_VALUES.VRAD and not self._separate_velocity(start, end, remove):
            return 0

        def change(mask, sel):
            if not remove:
                mask[sel] |= value
            elif value:
                mask[sel] &= ~value
            else:
                mask[sel] = 0

        count = 0
        for i in range(len(self.sme.wave)):
            wave = np.asarray(self.sme.wave[i])
            sel = (wave >= start) & (wave <= end)
            if sel.any():
                segment = np.array(self.sme.mask[i])
                change(segment, sel)
                self.sme.mask[i] = segment
                count += int(sel.sum())
        for seg in self.obs:
            change(seg["mask"], (seg["wave"] >= start) & (seg["wave"] <= end))
        self.touch()
        return count

    def _velocity_follows(self):
        """Per segment, True where PySME takes the line pixels for the velocity
        because no pixel carries the velocity flag. With one velocity per segment
        it looks at each segment alone, with a common one at all together."""
        marked = [
            bool(np.any(np.asarray(self.sme.mask[i]) & MASK_VALUES.VRAD))
            for i in range(len(self.sme.wave))
        ]
        if self.sme.vrad_flag == "each":
            return [not m for m in marked]
        return [not any(marked)] * len(marked)

    def _separate_velocity(self, start, end, remove):
        """Before the first change of the velocity flag, write down what PySME
        uses so far, the line pixels, so that the change is made to that.

        False if the edit would change nothing; the velocity then keeps
        following the line mask.
        """
        follows = self._velocity_follows()
        touched, changes = [], False
        for i in range(len(self.sme.wave)):
            wave = np.asarray(self.sme.wave[i])
            sel = (wave >= start) & (wave <= end)
            if not sel.any():
                continue
            touched.append(i)
            used = MASK_VALUES.LINE if follows[i] else MASK_VALUES.VRAD
            marked = (np.asarray(self.sme.mask[i])[sel] & used) != 0
            changes = changes or bool(np.any(marked if remove else ~marked))
        if not changes:
            return False
        everywhere = self.sme.vrad_flag != "each"
        for i in range(len(self.sme.wave)) if everywhere else touched:
            if not follows[i]:
                continue
            segment = np.array(self.sme.mask[i])
            segment[(segment & MASK_VALUES.LINE) != 0] |= MASK_VALUES.VRAD
            self.sme.mask[i] = segment
            wave = np.asarray(self.sme.wave[i])
            for seg in self.obs:
                inside = (seg["wave"] >= wave[0]) & (seg["wave"] <= wave[-1])
                seg["mask"][inside & ((seg["mask"] & MASK_VALUES.LINE) != 0)] |= MASK_VALUES.VRAD
        return True

    # ------------------------------------------------------------- abundances

    def abundance_table(self):
        """Every element: A(X) as used in the synthesis, and the same relative
        to the solar reference, [X/Fe] (for Fe itself [Fe/H])."""
        from pysme.abund import elements

        pattern = np.array(self.sme.abund._pattern, dtype=float)
        monh = float(self.sme.monh or 0)
        solar = solar_abundances(self.solar_reference)
        values = pattern + np.where(np.arange(len(pattern)) >= 2, monh, 0.0)
        iron = list(elements).index("Fe")
        feh = values[iron] - solar[iron]
        rows = []
        for z, element in enumerate(elements, start=1):
            i = z - 1
            ratio = math.nan
            if element not in NO_RATIO:
                ratio = values[i] - solar[i] - (0.0 if element == "Fe" else feh)
            rows.append({
                "z": z, "el": element, "A": values[i],
                "delta": pattern[i] - self.abund_baseline[i],
                "solar": solar[i], "ratio": ratio,
            })
        return clean({
            "elements": rows, "baseline": self.abund_baseline_name, "monh": monh,
            "reference": self.solar_reference, "feh": feh,
        })

    def set_abundance(self, element, value, scale="A"):
        """Change one element. ``value`` is A(X), or with ``scale="ratio"``
        [X/Fe] ([Fe/H] for Fe). Only that element's A changes: the others keep
        theirs, so a new Fe moves their [X/Fe]. None restores the baseline."""
        from pysme.abund import elements

        if element not in elements or element == "H":
            raise UserError(f"Cannot change the abundance of '{element}'.")
        if scale not in ("A", "ratio"):
            raise UserError(f"Unknown abundance scale: {scale}")
        index = list(elements).index(element)
        if value is None or value == "":
            monh = float(self.sme.monh or 0) if index > 1 else 0.0
            target = self.abund_baseline[index] + monh
            if not math.isfinite(target):
                raise UserError(f"{element} has no baseline value.")
        elif scale == "ratio":
            target = self._from_ratio(element, value)
        else:
            target = parse_float(value, f"A({element})")
        self.sme.abund.A[element] = target
        self.touch(stale=True)

    def _from_ratio(self, element, value):
        """A(X) for a given [X/Fe], or [Fe/H] for Fe."""
        from pysme.abund import elements

        if element in NO_RATIO:
            raise UserError(f"{element} is given as A({element}) only.")
        names = list(elements)
        label = ratio_label(element)
        number = parse_float(value, label)
        solar = solar_abundances(self.solar_reference)
        sun = solar[names.index(element)]
        if not math.isfinite(sun):
            raise UserError(
                f"{self.solar_reference} has no solar value for {element}. "
                f"Enter A({element}) instead."
            )
        if element == "Fe":
            return sun + number
        feh = float(self.sme.abund.A["Fe"]) - solar[names.index("Fe")]
        if not math.isfinite(feh):
            raise UserError(f"There is no Fe abundance to give {label} against.")
        return sun + number + feh

    def set_solar_reference(self, name):
        """The solar table [X/Fe] is written against. No abundance changes."""
        if name not in ABUND_PATTERNS:
            raise UserError(f"Unknown abundance pattern: {name}")
        if name != self.solar_reference:
            self.solar_reference = name
            self.touch()

    def apply_abund_pattern(self, name):
        if name not in ABUND_PATTERNS:
            raise UserError(f"Unknown abundance pattern: {name}")
        try:
            abund = Abund(monh=float(self.sme.monh or 0), pattern=name)
        except ValueError as exc:
            raise UserError(str(exc))
        self.sme.abund = abund
        self.solar_reference = name
        self._remember_abund_baseline()
        self.abund_baseline_name = f"the {name} pattern"
        self.touch(stale=True)

    # ------------------------------------------------------------------- NLTE

    def set_nlte(self, element, enabled, grid=None):
        from pysme.abund import elements

        if element not in elements:
            raise UserError(f"Unknown element: {element}")
        if enabled:
            try:
                self.sme.nlte.set_nlte(element, grid or None)
            except ValueError as exc:
                raise UserError(str(exc))
        elif element in self.sme.nlte.elements:
            self.sme.nlte.remove_nlte(element)
        self.touch(stale=True)

    def nlte_off(self):
        """Compute every element in LTE again."""
        elements = list(self.sme.nlte.elements)
        for element in elements:
            self.sme.nlte.remove_nlte(element)
        if elements:
            self.touch(stale=True)

    # ----------------------------------------------------------------- queries

    def readiness(self):
        sme = self.sme
        synth, fit, warnings = [], [], []
        nlines = len(sme.linelist)
        if nlines == 0:
            synth.append("Load a line list")
        if self.obs is None and not self.ranges:
            synth.append("Set a wavelength range")
        if sme.atmo.method == "grid" and not sme.atmo.source:
            synth.append("Choose a model atmosphere grid")
        if self.obs is None:
            fit.append("Import an observed spectrum")
        if not sme.fitparameters and sme.vrad_flag not in VELOCITY_FREE:
            fit.append("Select at least one free parameter")

        spans = self.ranges
        if self.obs is not None and sme.wave is not None:
            spans = [[float(sme.wave[i][0]), float(sme.wave[i][-1])] for i in range(len(sme.wave))]
        if nlines and spans:
            centers = np.asarray(sme.linelist.wlcent, float)
            lo, hi = float(centers.min()), float(centers.max())
            empty = [s for s in spans if not np.any((centers >= s[0]) & (centers <= s[1]))]
            if len(empty) == len(spans):
                synth.append(f"Line list ({lo:.0f} - {hi:.0f} A) has no lines in the wavelength range")
            elif empty:
                warnings.append(f"{len(empty)} of {len(spans)} segments contain no line from the list.")
            if any(s[0] < lo - 5 or s[1] > hi + 5 for s in spans) and len(empty) < len(spans):
                warnings.append(
                    f"The line list covers {lo:.1f} - {hi:.1f} A, not the whole wavelength "
                    "range. Lines outside it are missing from the synthesis."
                )
        if self.obs is not None and not self.has_uncs:
            warnings.append(
                f"The observation has no uncertainties. S/N = {self.snr:g} is assumed, so "
                "chi-square and parameter errors scale with that guess."
            )
        if self.obs is not None and nlines:
            warnings.append(
                f"The line list uses {sme.linelist.medium} wavelengths; the observed "
                "spectrum must use the same."
            )
        if sme.nlte.elements:
            warnings.append(
                "NLTE grids are downloaded the first time they are used. Some are several GB."
            )
        if sme.cscale_flag in ("linear", "quadratic") and self.obs is None:
            warnings.append("Continuum fitting is ignored without an observation.")
        fit = synth + fit
        # A fit gets its wavelength grid from the observation.
        if self.obs is None:
            fit = [m for m in fit if m != "Set a wavelength range"]
        return {"synth": synth, "fit": fit, "warnings": warnings}

    def state(self):
        sme = self.sme
        params = {name: getattr(sme, attr) for name, (attr, _, _) in FLOAT_PARAMS.items()}
        params.update({name: getattr(sme, name) for name in BOOL_PARAMS})
        params.update({name: getattr(sme, name) for name in CHOICE_PARAMS})
        ipres = np.atleast_1d(sme.ipres)
        params["ipres"] = ipres[0] if ipres.size else 0
        params["iptype"] = sme.iptype
        params["nmu"] = sme.nmu
        params["mu"] = [float(m) for m in sme.mu]
        params["mu_default"] = bool(
            np.allclose(np.sort(sme.mu)[::-1], default_mu(len(sme.mu)), rtol=0, atol=1e-8)
        )
        vrad = np.atleast_1d(sme.vrad)
        params["vrad"] = vrad[0] if vrad.size else 0
        params["vrad_each"] = self._velocity_values()
        params["snr"] = self.snr
        params["fitparameters"] = list(sme.fitparameters or [])

        linelist = {
            "nlines": len(sme.linelist), "file": self.linelist_file,
            "version": self.linelist_version, "edited": len(self.line_edits),
        }
        if len(sme.linelist):
            centers = np.asarray(sme.linelist.wlcent, float)
            linelist.update(
                wmin=centers.min(), wmax=centers.max(),
                medium=sme.linelist.medium, format=sme.linelist.lineformat,
            )

        segments = []
        if sme.wave is not None:
            for i in range(len(sme.wave)):
                wave = sme.wave[i]
                if len(wave):
                    segments.append({"wmin": wave[0], "wmax": wave[-1], "n": len(wave)})
        observation = None
        if self.obs is not None:
            observation = {
                "npix": int(sum(len(s["wave"]) for s in self.obs)),
                "used": int(sum(s["n"] for s in segments)),
                "has_uncs": self.has_uncs,
                "full": [[s["wave"][0], s["wave"][-1]] for s in self.obs],
                "windows": self.windows,
            }
        return clean({
            "revision": self.revision,
            "path": self.path,
            "dirty": self.dirty,
            "params": params,
            "atmo": {k: getattr(sme.atmo, k) for k in ("source", "method", "geom", "depth", "interp")},
            "linelist": linelist,
            "nlte": {"elements": list(sme.nlte.elements), "grids": dict(sme.nlte.grids)},
            "setup": {"ranges": self.ranges, "step": self.step},
            "observation": observation,
            "segments": segments,
            "has_synth": sme.synth is not None,
            "synth_stale": self.synth_stale,
            "fit": self._fit_shown(),
            "solar_reference": self.solar_reference,
            "ready": self.readiness(),
            "runs": [{k: r[k] for k in ("id", "label", "kind", "time")} for r in self.runs],
            "notes": self.notes,
        })

    def spectrum(self):
        sme = self.sme
        segments = []
        if sme.wave is None:
            return {"segments": segments}
        total = sum(len(sme.wave[i]) for i in range(len(sme.wave)))
        if total > MAX_PLOT_POINTS:
            raise UserError(
                f"The spectrum has {total} pixels, too many to plot. Choose wavelength "
                "windows under Star > Wavelength."
            )
        both = self._both_scales()
        velocities = self.synth_vrad if sme.synth is not None else self._velocities(sme)
        follows = (
            self._velocity_follows() if self.obs is not None and sme.mask is not None else None
        )
        for i in range(len(sme.wave)):
            segment = {"wave": np.asarray(sme.wave[i]), "shift": self._doppler(velocities, i)}
            if self.obs is not None and sme.spec is not None:
                segment["obs"] = np.asarray(sme.spec[i])
                if sme.uncs is not None and self.has_uncs:
                    segment["uncs"] = np.asarray(sme.uncs[i])
                if sme.mask is not None:
                    segment["mask"] = np.asarray(sme.mask[i]).astype(int)
                    # True: the velocity mask shown is the line mask, as PySME uses it.
                    segment["velocity_follows"] = follows[i]
            if both:
                for name, values in zip(("synth", "flux"), both[i]):
                    if values is not None:
                        segment[name] = values
            segments.append(segment)
        return clean({"segments": segments})

    @staticmethod
    def _velocities(sme):
        """The radial velocity of each segment that PySME shifts the model by."""
        if sme.vrad_flag == "none" or sme.vrad is None:
            return None
        return [float(v) for v in np.atleast_1d(sme.vrad)] or None

    @staticmethod
    def _doppler(velocities, segment):
        """Where a rest wavelength appears: its factor in this segment."""
        if not velocities:
            return 1.0
        beta = velocities[min(segment, len(velocities) - 1)] / LIGHT_SPEED
        return math.sqrt((1 + beta) / (1 - beta))      # as PySME does it

    @staticmethod
    def _applied_continuum(sme, observed):
        """The continuum correction that is inside a spectrum just computed.

        PySME multiplies it into the synthetic spectrum but not into the continuum.
        Without an observation, or with the correction off, there is none, even if
        coefficients from an earlier run are still stored.
        """
        if not observed or sme.cscale_flag == "none" or sme.cscale is None or sme.wave is None:
            return None
        return {
            "type": str(sme.cscale_type),
            "coeffs": [np.asarray(sme.cscale[i], float).tolist() for i in range(len(sme.wave))],
        }

    def _both_scales(self):
        """(normalised, flux at the stellar surface) of the synthesis, per segment.

        Both come from one run: PySME keeps the continuum it divided by. Either
        is None where the stored structure does not allow it.
        """
        sme = self.sme
        if sme.synth is None or sme.wave is None:
            return None
        pairs = []
        for i in range(len(sme.wave)):
            wave = np.asarray(sme.wave[i], float)
            synth = np.asarray(sme.synth[i], float)
            if len(synth) != len(wave):
                pairs.append((None, None))
                continue
            cont = None if sme.cont is None else np.asarray(sme.cont[i], float)
            if cont is not None and len(cont) != len(wave):
                cont = None
            factor = np.ones(len(wave))
            if self.synth_correction:
                coeffs = np.asarray(self.synth_correction["coeffs"][i], float)
                apply_continuum([wave], [factor], [wave], [coeffs], self.synth_correction["type"], [0])
            if sme.normalize_by_continuum:
                pairs.append((synth, None if cont is None else synth * cont / factor))
            else:
                # Only in files made elsewhere; the interface computes normalised.
                flux = synth / factor
                pairs.append((None if cont is None else flux / cont, flux))
        return pairs

    def run_spectrum(self, run_id):
        for run in self.runs:
            if run["id"] == run_id:
                return clean({"segments": run["segments"]})
        raise UserError("That earlier run is no longer stored.")

    def linelist_rows(self, offset=0, limit=100, query=""):
        if len(self.sme.linelist) == 0:
            return {"total": 0, "rows": [], "columns": LINE_COLUMNS}
        frame = self.sme.linelist._lines
        columns = [c for c in LINE_COLUMNS if c in frame.columns]
        query = (query or "").strip()
        if query:
            parts = query.replace("–", "-").split("-")
            bounds = None
            if len(parts) == 2:
                try:
                    bounds = (float(parts[0]), float(parts[1]))
                except ValueError:
                    bounds = None
            if bounds:
                frame = frame[(frame["wlcent"] >= bounds[0]) & (frame["wlcent"] <= bounds[1])]
            else:
                names = frame["species"].astype(str).str.lower()
                words = query.lower().split()
                if len(words) == 2:
                    frame = frame[names == " ".join(words)]
                else:
                    frame = frame[names.str.split().str[0] == words[0]]
        page = frame.iloc[offset:offset + limit]
        rows = [[row[c] for c in columns] for _, row in page.iterrows()]
        # The table's index is the position in the whole list.
        positions = self.sme.linelist._lines.index.get_indexer(page.index)
        ids = [int(i) for i in self._ids()[positions]]
        edited = {i: self.line_edits[i] for i in ids if i in self.line_edits}
        known = citations.known_keys(self._line_bibtex())
        long_format = self.sme.linelist.lineformat == "long"
        terms = [None] * len(page)
        if {"term_lower", "term_upper"} <= set(page.columns):
            terms = [
                f"{low} → {up}" if low and up else None
                for low, up in zip(map(citations.short_term, page["term_lower"]),
                                   map(citations.short_term, page["term_upper"]))
            ]
        references = [
            citations.line_references(text if isinstance(text, str) else "", known, long_format)
            for text in page.get("reference", [""] * len(page))
        ]
        return clean({
            "total": len(frame), "rows": rows, "columns": columns,
            "ids": ids, "edited": edited, "references": references, "terms": terms,
        })

    def line_detail(self, line_id):
        """Everything the list holds on one line, and where each quantity is from."""
        row = self._row_of(line_id)
        linelist = self.sme.linelist
        values = linelist._lines.iloc[row].to_dict()
        long_format = linelist.lineformat == "long"
        text = values.get("reference")
        references = citations.line_references(
            text if isinstance(text, str) else "",
            citations.known_keys(self._line_bibtex()), long_format,
        )
        edits = self.line_edits.get(int(line_id), {})
        return clean({
            "id": int(line_id),
            "line": f"{values['species']} {values['wlcent']:.4f} Å",
            "quantities": citations.line_quantities(values, references, edits, long_format),
            "references": references,
        })

    def strong_lines(self, wmin, wmax, limit=30):
        if len(self.sme.linelist) == 0:
            return {"lines": []}
        full = self.sme.linelist._lines
        inside = ((full["wlcent"] >= wmin) & (full["wlcent"] <= wmax)).to_numpy()
        frame = full[inside].assign(_id=self._ids()[inside])
        if "depth" in frame.columns:
            frame = frame.sort_values("depth", ascending=False)
        else:
            frame = frame.sort_values("gflog", ascending=False)
        frame = frame.head(limit)
        return clean({"lines": [
            {"wave": r["wlcent"], "species": str(r["species"]), "id": int(r["_id"]),
             "depth": r["depth"] if "depth" in frame.columns else None}
            for _, r in frame.iterrows()
        ]})

    def citations(self, output):
        """The references by section, and the same as one text."""
        output = "bibtex" if output == "bibtex" else "plaintext"
        try:
            parts = citations.sections(self.sme, self._line_bibtex())
            return {"sections": parts, "text": citations.as_text(parts, output)}
        except Exception as exc:
            return {"sections": [], "text": f"Could not build the citation list: {exc}"}

    # ------------------------------------------------------------------ saving

    def _write_meta(self, sme):
        meta = dict(sme.meta or {})
        meta["gui_linelist_file"] = self.linelist_file
        meta["gui_solar_reference"] = self.solar_reference
        meta["gui_fit_info"] = self.fit_info
        meta["gui_synth_stale"] = self.synth_stale
        meta["gui_synth_continuum"] = self.synth_correction
        meta["gui_synth_vrad"] = self.synth_vrad
        meta["gui_line_edits"] = {
            str(self._row_of(line_id)): was for line_id, was in self.line_edits.items()
        }
        meta["gui_computed_with"] = self.computed_with
        meta["gui_synth_setup"] = (
            {"ranges": self.ranges, "step": self.step} if self.obs is None else None
        )
        meta["gui_assumed_snr"] = (
            self.snr if self.obs is not None and not self.has_uncs and sme.uncs is not None
            else None
        )
        sme.meta = meta

    def write_job_input(self, path):
        """Save the structure that a worker process will compute from."""
        self._write_meta(self.sme)
        self.sme.save(path)

    def save(self, path=None):
        path = path or self.path
        if not path:
            raise UserError("Choose a file name.")
        path = os.path.abspath(os.path.expanduser(path))
        if not path.lower().endswith(".sme"):
            path += ".sme"
        if self.obs is None and self.sme.wave is None and self.ranges:
            self.sme.wran = np.array(self.ranges)
        self._write_meta(self.sme)
        # PySME appends ".sme" to other names, so the temporary file keeps it.
        temp = path[:-4] + ".part.sme"
        self.sme.save(temp)
        os.replace(temp, path)
        self.path = path
        self.dirty = False
        self.revision += 1
        self.notes = [f"Saved {os.path.basename(path)}."]
        return path

    def export_csv(self, path):
        sme = self.sme
        if sme.wave is None:
            raise UserError("There is no spectrum to export.")
        if not path.lower().endswith(".csv"):
            path += ".csv"
        columns = [("wave", sme.wave)]
        if self.obs is not None:
            columns.append(("flux_obs", sme.spec))
            if self.has_uncs:
                columns.append(("uncertainty", sme.uncs))
            columns.append(("mask", sme.mask))
        # Both scales, whichever of them the plot happens to show.
        both = self._both_scales()
        surface = None
        if both:
            if all(normalised is not None for normalised, _ in both):
                columns.append(("synth", [normalised for normalised, _ in both]))
            if all(flux is not None for _, flux in both):
                surface = [flux for _, flux in both]
                columns.append(("synth_surface_flux", surface))
        columns = [(n, v) for n, v in columns if v is not None]
        if len(columns) < 2:
            raise UserError("Synthesize or import a spectrum first.")
        buffer = io.StringIO()
        if self.computed_with and sme.synth is not None:
            used = self.computed_with
            buffer.write(
                f"# synth computed with PySME {used['pysme']}, SMElib {used['smelib']}, "
                f"PySME-GUI {used['gui']}\n"
            )
        if surface is not None:
            buffer.write(
                "# synth_surface_flux: model flux at the stellar surface, erg s^-1 cm^-2 A^-1, "
                "not scaled by radius or distance, without the continuum correction\n"
            )
        if self.synth_stale and sme.synth is not None:
            buffer.write("# WARNING: parameters changed after 'synth' was computed\n")
        buffer.write("segment," + ",".join(n for n, _ in columns) + "\n")
        for seg in range(len(sme.wave)):
            data = [np.asarray(v[seg]) for _, v in columns]
            for values in zip(*data):
                buffer.write(f"{seg}," + ",".join(f"{v:.10g}" for v in values) + "\n")
        with open(os.path.expanduser(path), "w", encoding="utf-8") as handle:
            handle.write(buffer.getvalue())
        return path

    # ----------------------------------------------------------------- results

    def _read_fit_results(self, sme, initial):
        """Summarise ``sme.fitresults``.

        PySME solves for abundances on the *pattern* scale, which excludes
        [M/H]. The table shows A(X) as used in the synthesis and keeps the raw
        solver number next to it. The solver's error bars carry over to A(X)
        only when [M/H] was held fixed; otherwise the two are correlated and no
        A(X) error is quoted.
        """
        results = sme.fitresults
        raw_names = results.parameters if results.parameters is not None else []
        names = [canonical_parameter(n) for n in raw_names]
        initial = initial or {}

        def pick(array, i):
            if array is None:
                return None
            array = np.atleast_1d(array)
            return array[i] if i < len(array) else None

        rows = []
        for i, name in enumerate(names):
            row = {
                "name": name,
                "initial": initial.get(name),
                "value": pick(results.values, i),
                "unc_sme": pick(results.uncertainties, i),
                "unc_fit": pick(results.fit_uncertainties, i),
            }
            if name.startswith("abund "):
                element = name.split()[1]
                row["solver"] = {k: row[k] for k in ("value", "unc_sme", "unc_fit")}
                row["value"] = sme.abund.A[element]
                if "monh" in names:
                    row["unc_sme"] = row["unc_fit"] = None
            rows.append(row)
        return clean({
            "parameters": rows, "chisq": results.chisq,
            "a_fe": sme.abund.A["Fe"],       # what [X/Fe] of this fit is against
        })

    def _fit_shown(self):
        """The last fit, with its abundances also relative to the Sun."""
        info = self.fit_info
        if not info or not info.get("parameters"):
            return info
        names = [p["name"] for p in info["parameters"]]
        solar = solar_abundances(self.solar_reference)
        rows = [
            {**p, "ratio": fit_ratio(p, info.get("a_fe"), names, solar)}
            if p["name"].startswith("abund ") else p
            for p in info["parameters"]
        ]
        return {**info, "parameters": rows}

    def _velocity_values(self):
        """One radial velocity per segment, in km/s."""
        values = np.atleast_1d(self.sme.vrad)
        if not values.size:
            values = np.zeros(self.sme.nseg or 1)
        return [float(v) for v in values]

    def parameter_value(self, name):
        if name.startswith("abund "):
            return self.sme.abund.A[name.split()[1]]
        return getattr(self.sme, name)

    def _restore_point(self, names):
        """Values that undo a fit; abundances are kept on the pattern scale so
        the order of restoring them and [M/H] does not matter."""
        point = {}
        for name in names:
            if name.startswith("abund "):
                point[name] = self.sme.abund.pattern[name.split()[1]]
            else:
                point[name] = getattr(self.sme, name)
        return point

    def adopt_result(self, kind, path):
        """Replace the structure with the one a worker process produced."""
        names = list(self.sme.fitparameters or [])
        initial = {n: self.parameter_value(n) for n in names}
        restore = self._restore_point(names)
        velocity_before = self._velocity_values() if self.sme.vrad_flag in VELOCITY_FREE else None
        result = SME_Structure.load(path)
        result.fitparameters = names
        self.sme = result
        self.synth_stale = False
        self.synth_correction = self._applied_continuum(result, self.obs is not None)
        # PySME leaves a spectrum computed without an observation unshifted.
        self.synth_vrad = None if self.obs is None else self._velocities(result)
        self.computed_with = software_versions()
        if kind == "fit":
            self.fit_info = self._read_fit_results(result, initial)
            if velocity_before is not None:
                # PySME finds it apart from the other parameters and, on the
                # paths used here, computes no uncertainty for it.
                self.fit_info["velocity"] = clean({
                    "initial": velocity_before, "value": self._velocity_values(),
                    "each": result.vrad_flag == "each",
                })
                restore["vrad"] = velocity_before
            self.fit_info["restore"] = clean(restore)
            if self.obs is not None and result.mask is not None:
                # The solver marks zero-uncertainty pixels bad; keep our copy in step.
                for i, (seg, sel) in enumerate(self._window_slices()):
                    seg["mask"][sel] = np.asarray(result.mask[i]).astype(int)
        elif self.fit_info is not None:
            self.fit_info["outdated"] = True
        self._store_run(kind)
        self.notes = []
        self.touch()

    def revert_fit(self):
        """Put the free parameters back to their values from before the last fit."""
        restore = (self.fit_info or {}).get("restore")
        if not restore:
            raise UserError("There is no previous fit to undo.")
        for name, value in restore.items():
            if name.startswith("abund "):
                self.sme.abund.pattern[name.split()[1]] = value
            elif name == "vrad":
                self.sme.vrad = np.resize(np.array(value, float), self.sme.nseg or 1)
            else:
                setattr(self.sme, name, value)
        self.fit_info = None
        self.touch(stale=True)

    def _store_run(self, kind):
        sme = self.sme
        if sme.synth is None:
            return
        self._run_counter += 1
        label = (
            f"#{self._run_counter} {kind}  {sme.teff:.0f} / {sme.logg:.2f} / {sme.monh:+.2f}"
        )
        segments = []
        for i, (normalised, flux) in enumerate(self._both_scales()):
            segment = {"wave": np.array(sme.wave[i])}
            for name, values in (("synth", normalised), ("flux", flux)):
                if values is not None:
                    segment[name] = np.array(values)
            segments.append(segment)
        self.runs.append({
            "id": self._run_counter, "label": label, "kind": kind,
            "time": time.strftime("%H:%M:%S"), "segments": segments,
        })
        del self.runs[:-MAX_RUNS]


def available_options():
    """Model grids, NLTE grids and abundance patterns this PySME can use."""
    from pysme.config import Config
    from pysme.large_file_storage import get_available_atmospheres, get_available_nlte_grids
    from pysme.nlte import NLTE

    config = Config()
    result = {"atmospheres": [], "nlte_grids": [], "nlte_defaults": {}, "patterns": [], "errors": []}
    for key, getter, field, suffix in (
        ("data.atmospheres", get_available_atmospheres, "atmospheres", ".sav"),
        ("data.nlte_grids", get_available_nlte_grids, "nlte_grids", ".grd"),
    ):
        try:
            os.makedirs(os.path.expanduser(config[key]), exist_ok=True)
            result[field] = sorted(f for f in getter(config) if f.lower().endswith(suffix))
        except Exception as exc:
            result["errors"].append(f"Could not list {field}: {exc}")
    result["nlte_defaults"] = dict(getattr(NLTE, "_default_grids", {}))
    for name in ABUND_PATTERNS:
        try:
            Abund(pattern=name)
            result["patterns"].append(name)
        except Exception:
            pass
    return result
