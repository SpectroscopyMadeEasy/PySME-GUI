"""Run one synthesis or fit in its own process.

Keeping SMElib out of the server process means a run can be cancelled at any
time, and a crash in the C/Fortran code cannot take the window down with it.

Usage: python -m pysme_gui.worker JOB_DIRECTORY
"""

import json
import os
import sys
import traceback

import numpy as np


def prepare(sme, job):
    """Apply the wavelength setup of a job to a freshly loaded structure.

    Returns the flags that were changed for this run only.
    """
    restore = {}
    # Always computed normalised, which is what an observation is compared with.
    # The continuum is kept, so the interface can show the flux without a new run.
    sme.normalize_by_continuum = True
    if not job["has_obs"]:
        for name in ("synth", "cont", "spec", "uncs", "mask", "telluric"):
            setattr(sme, name, None)
        vrad = float(np.atleast_1d(sme.vrad)[0])
        sme.wave = None
        sme.wran = np.array(job["ranges"], dtype=float)
        # Without data there is nothing to fit a velocity or continuum to. PySME
        # switches both off and sets the velocity to 0 in what it returns, so the
        # settings, which are for the observation, are put back after the run.
        restore = {
            "vrad_flag": sme.vrad_flag,
            "cscale_flag": sme.cscale_flag,
            "vrad": np.full(len(job["ranges"]), vrad),
        }
        if sme.vrad_flag in ("whole", "each"):
            sme.vrad_flag = "fix"
        if sme.cscale_flag not in ("none", "fix"):
            sme.cscale_flag = "none"
        step = job.get("step")
        if step:
            grids = []
            for start, end in job["ranges"]:
                count = int(np.floor((end - start) / step + 1e-9)) + 1
                grids.append(start + step * np.arange(count))
            sme.wave = grids
        nseg = len(job["ranges"])
        sme.vrad = np.full(nseg, vrad)
        sme.cscale = None
    else:
        if sme.uncs is None:
            sme.uncs = [
                np.full(len(sme.spec[i]), abs(np.nanmedian(sme.spec[i])) / job["snr"] or 1.0 / job["snr"])
                for i in range(len(sme.spec))
            ]
        # A velocity that is free is found by a fit only, like every other free
        # parameter. PySME would otherwise find it in every synthesis.
        if job["kind"] == "synth" and sme.vrad_flag in ("whole", "each"):
            restore["vrad_flag"] = sme.vrad_flag
            sme.vrad_flag = "fix"
    return restore


def run(directory):
    with open(os.path.join(directory, "job.json"), encoding="utf-8") as handle:
        job = json.load(handle)

    from pysme_gui import safe_sme

    sme = safe_sme.load(os.path.join(directory, "in.sme"))
    restore = prepare(sme, job)

    if job["kind"] == "synth":
        from pysme.synthesize import synthesize_spectrum

        sme = synthesize_spectrum(sme)
    elif job["kind"] == "fit" and job["parameters"]:
        from pysme.solve import solve

        sme = solve(sme, list(job["parameters"]))
    elif job["kind"] == "fit":
        # Only the velocity is free. PySME's solve() fails without another
        # parameter; a synthesis that may fit the velocity does the same job.
        from pysme.synthesize import synthesize_spectrum

        sme.fitresults.clear()              # nothing of an earlier fit
        sme = synthesize_spectrum(sme)
    else:
        raise ValueError(f"Unknown job kind {job['kind']}")

    for name, value in restore.items():
        setattr(sme, name, value)
    sme.save(os.path.join(directory, "out.sme"))


def main():
    directory = sys.argv[1]
    try:
        run(directory)
    except BaseException as exc:
        traceback.print_exc()
        with open(os.path.join(directory, "error.json"), "w", encoding="utf-8") as handle:
            json.dump({"type": type(exc).__name__, "message": str(exc)}, handle)
        sys.stdout.flush()
        sys.exit(2)


if __name__ == "__main__":
    main()
