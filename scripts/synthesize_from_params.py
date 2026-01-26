from pysme.sme import SME_Structure
from pysme.synthesize import synthesize_spectrum
from pysme.util import start_logging
from pysme.linelist.vald import ValdFile

import argparse
import json
import numpy as np

from electron import call


@call
def main(params_file, output_file, log_file=None):
    if log_file is not None:
        start_logging(log_file)

    with open(params_file, "r") as f:
        params = json.load(f)

    teff = float(params["teff"])
    logg = float(params["logg"])
    monh = float(params["monh"])
    wstart = float(params["wstart"])
    wend = float(params["wend"])
    wstep = params.get("wstep", None)

    linelist_file = params.get("linelist_file", None)
    atmo_source = params.get("atmo_source", None)
    atmo_method = params.get("atmo_method", "grid")
    atmo_geom = params.get("atmo_geom", "PP")
    atmo_depth = params.get("atmo_depth", "RHOX")
    atmo_interp = params.get("atmo_interp", "TAU")

    if linelist_file is None or linelist_file == "":
        raise ValueError("linelist_file is required")
    if atmo_source is None or atmo_source == "":
        raise ValueError("atmo_source is required")
    if wstart >= wend:
        raise ValueError("wavelength start must be smaller than end")

    sme = SME_Structure()
    sme.teff = teff
    sme.logg = logg
    sme.monh = monh
    sme.wran = [[wstart, wend]]

    if wstep is not None and str(wstep).strip() != "":
        wstep = float(wstep)
        if wstep <= 0:
            raise ValueError("wavelength step must be > 0")
        wave = np.arange(wstart, wend + 0.5 * wstep, wstep)
        sme.wave = [wave]

    sme.atmo.source = atmo_source
    sme.atmo.method = atmo_method
    sme.atmo.geom = atmo_geom
    sme.atmo.depth = atmo_depth
    sme.atmo.interp = atmo_interp

    sme.linelist = ValdFile(linelist_file)

    sme = synthesize_spectrum(sme)
    sme.save(output_file)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Synthesize spectrum from minimal parameters"
    )
    parser.add_argument("params_file", help="JSON file with synthesis parameters")
    parser.add_argument("output_file", help="Output SME file name")
    parser.add_argument("--log_file", help="Logfile", default=None)

    args = parser.parse_args()

    main(params_file=args.params_file, output_file=args.output_file, log_file=args.log_file)
