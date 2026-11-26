"""The state behind the GUI window: one SME structure plus a little bookkeeping.

All science objects are real PySME objects. The browser side only ever sees the
JSON produced here, so there is no second implementation of the SME file format.
"""

import io
import math
import os
import re
import time

import numpy as np
from pysme.abund import Abund
from pysme.linelist.vald import ValdFile
from pysme.sme import MASK_VALUES, SME_Structure

from .spectrum_io import read_text_spectrum

SME_EXTENSIONS = {".sme", ".inp", ".sav", ".out"}
LINELIST_EXTENSIONS = {".lin"}
EXAMPLE_LINELIST = os.path.join(os.path.dirname(__file__), "examples", "sun.lin")

ABUND_PATTERNS = [
    "asplund2021", "asplund2009", "grevesse2007", "asplund2005", "grevesse1998",
    "grevesse1996", "anders1989", "lodders2010", "lodders2003", "amarsi2026",
]

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
BOOL_PARAMS = {"h2broad", "normalize_by_continuum"}
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


def default_sme():
    sme = SME_Structure()
    sme.teff, sme.logg, sme.monh = 5770.0, 4.44, 0.0
    sme.vmic, sme.vmac, sme.vsini = 1.0, 0.0, 0.0
    sme.atmo.source = "marcs2012.sav"
    sme.atmo.method = "grid"
    return sme


def _segments(vector):
    if vector is None:
        return None
    return [np.asarray(vector[i]) for i in range(len(vector))]


def software_versions():
    import pysme

    from . import __version__

    try:
        from pysme.smelib import _smelib

        smelib = str(_smelib.LibraryVersion())
        # "SME Library version: ... [SMElib ref: v6.13.20]" -> "v6.13.20"
        reference = re.search(r"ref: (v[\w.]+)", smelib)
        smelib = reference.group(1) if reference else smelib
    except Exception:
        smelib = "unknown"
    return {"gui": __version__, "pysme": pysme.__version__, "smelib": smelib}


class Session:
    def __init__(self):
        self.revision = 0
        self.reset()

    # ------------------------------------------------------------------ basics

    def reset(self):
        self.sme = default_sme()
        self.path = None
        self.linelist_file = None
        self.obs = None          # full observation, list of segment dicts
        self.windows = None      # list of [start, end] cut out of the observation
        self.has_uncs = False
        self.snr = 100.0
        self.ranges = []         # synthesis ranges when there is no observation
        self.step = None
        self.runs = []
        self._run_counter = 0
        self.synth_stale = False
        self.computed_with = None  # versions that produced the current spectrum
        self.fit_info = None
        self.notes = []
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
        meta = sme.meta or {}
        setup = meta.get("gui_synth_setup")
        self.synth_stale = bool(meta.get("gui_synth_stale", False))
        used = meta.get("gui_computed_with")
        if isinstance(used, dict) and {"gui", "pysme", "smelib"} <= set(used):
            self.computed_with = dict(used)
        self.linelist_file = meta.get("gui_linelist_file")
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
        self.linelist_file = path
        self.notes = [f"Loaded {len(linelist)} lines from {os.path.basename(path)}."]
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
            elif name in CHOICE_PARAMS:
                if value not in CHOICE_PARAMS[name]:
                    raise UserError(f"Unknown value for {name}: {value}")
                setattr(sme, name, value)
                changed_physics = True
            elif name == "nmu":
                number = int(parse_float(value, name))
                if not 1 <= number <= 77:
                    raise UserError("Number of mu angles must be between 1 and 77.")
                sme.nmu = number
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

    def set_mask(self, start, end, value):
        if self.obs is None or self.sme.mask is None:
            raise UserError("Import an observed spectrum before editing the mask.")
        value = int(value)
        allowed = {int(MASK_VALUES.BAD), int(MASK_VALUES.LINE), int(MASK_VALUES.CONT)}
        if value not in allowed:
            raise UserError("Unknown mask value.")
        start, end = sorted((parse_float(start, "start"), parse_float(end, "end")))
        count = 0
        for i in range(len(self.sme.wave)):
            wave = np.asarray(self.sme.wave[i])
            sel = (wave >= start) & (wave <= end)
            if sel.any():
                segment = np.array(self.sme.mask[i])
                segment[sel] = value
                self.sme.mask[i] = segment
                count += int(sel.sum())
        for seg in self.obs:
            sel = (seg["wave"] >= start) & (seg["wave"] <= end)
            seg["mask"][sel] = value
        self.touch()
        return count

    # ------------------------------------------------------------- abundances

    def abundance_table(self):
        from pysme.abund import elements

        pattern = np.array(self.sme.abund._pattern, dtype=float)
        monh = float(self.sme.monh or 0)
        rows = []
        for z, element in enumerate(elements, start=1):
            shift = monh if z > 2 else 0.0
            value = pattern[z - 1] + shift
            delta = pattern[z - 1] - self.abund_baseline[z - 1]
            rows.append({"z": z, "el": element, "A": value, "delta": delta})
        return clean({"elements": rows, "baseline": self.abund_baseline_name, "monh": monh})

    def set_abundance(self, element, value):
        from pysme.abund import elements

        if element not in elements or element == "H":
            raise UserError(f"Cannot change the abundance of '{element}'.")
        index = list(elements).index(element)
        if value is None or value == "":
            monh = float(self.sme.monh or 0) if index > 1 else 0.0
            target = self.abund_baseline[index] + monh
            if not math.isfinite(target):
                raise UserError(f"{element} has no baseline value.")
        else:
            target = parse_float(value, f"A({element})")
        self.sme.abund.A[element] = target
        self.touch(stale=True)

    def apply_abund_pattern(self, name):
        if name not in ABUND_PATTERNS:
            raise UserError(f"Unknown abundance pattern: {name}")
        try:
            abund = Abund(monh=float(self.sme.monh or 0), pattern=name)
        except ValueError as exc:
            raise UserError(str(exc))
        self.sme.abund = abund
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
        if not sme.fitparameters:
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
        vrad = np.atleast_1d(sme.vrad)
        params["vrad"] = vrad[0] if vrad.size else 0
        params["snr"] = self.snr
        params["fitparameters"] = list(sme.fitparameters or [])

        linelist = {"nlines": len(sme.linelist), "file": self.linelist_file}
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
            "fit": self.fit_info,
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
        for i in range(len(sme.wave)):
            segment = {"wave": np.asarray(sme.wave[i])}
            if self.obs is not None and sme.spec is not None:
                segment["obs"] = np.asarray(sme.spec[i])
                if sme.uncs is not None and self.has_uncs:
                    segment["uncs"] = np.asarray(sme.uncs[i])
                if sme.mask is not None:
                    segment["mask"] = np.asarray(sme.mask[i]).astype(int)
            if sme.synth is not None and len(sme.synth[i]) == len(segment["wave"]):
                segment["synth"] = np.asarray(sme.synth[i])
            segments.append(segment)
        return clean({"segments": segments})

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
        return clean({"total": len(frame), "rows": rows, "columns": columns})

    def strong_lines(self, wmin, wmax, limit=30):
        if len(self.sme.linelist) == 0:
            return {"lines": []}
        frame = self.sme.linelist._lines
        frame = frame[(frame["wlcent"] >= wmin) & (frame["wlcent"] <= wmax)]
        if "depth" in frame.columns:
            frame = frame.sort_values("depth", ascending=False)
        else:
            frame = frame.sort_values("gflog", ascending=False)
        frame = frame.head(limit)
        return clean({"lines": [
            {"wave": r["wlcent"], "species": str(r["species"]),
             "depth": r["depth"] if "depth" in frame.columns else None}
            for _, r in frame.iterrows()
        ]})

    def citation(self, output):
        output = "bibtex" if output == "bibtex" else "plaintext"
        try:
            return self.sme.citation(output=output)
        except Exception as exc:
            return f"Could not build the citation list: {exc}"

    # ------------------------------------------------------------------ saving

    def _write_meta(self, sme):
        meta = dict(sme.meta or {})
        meta["gui_linelist_file"] = self.linelist_file
        meta["gui_fit_info"] = self.fit_info
        meta["gui_synth_stale"] = self.synth_stale
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
        if sme.synth is not None:
            columns.append(("synth", sme.synth))
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
        info = {"parameters": rows, "chisq": results.chisq}
        if sme.vrad_flag in ("whole", "each"):
            info["vrad"] = np.atleast_1d(sme.vrad)
        return clean(info)

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
        result = SME_Structure.load(path)
        result.fitparameters = names
        self.sme = result
        self.synth_stale = False
        self.computed_with = software_versions()
        if kind == "fit":
            self.fit_info = self._read_fit_results(result, initial)
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
        segments = [
            {"wave": np.array(sme.wave[i]), "synth": np.array(sme.synth[i])}
            for i in range(len(sme.wave))
        ]
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
