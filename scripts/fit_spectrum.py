from pysme.sme import SME_Structure
from pysme.solve import solve
from pysme.util import start_logging

import argparse
from os.path import dirname, join
from electron import call


@call
def main(fname_in, fname_out, fit_parameters, log_file=None):
    if log_file is not None:
        start_logging(log_file)
    sme = SME_Structure.load(fname_in)
    try:
        import numpy as np
        mask = sme.mask
        if mask is None:
            print("MASK_SUMMARY input: None")
        else:
            segments = mask.segments if hasattr(mask, "segments") else mask
            if isinstance(segments, np.ndarray):
                segments = [segments]
            summary = []
            for idx, seg in enumerate(segments):
                vals, counts = np.unique(np.asarray(seg), return_counts=True)
                summary.append((idx, dict(zip(vals.tolist(), counts.tolist()))))
            print("MASK_SUMMARY input:", summary)
    except Exception as exc:
        print("MASK_SUMMARY input: error", exc)
    sme = solve(sme, fit_parameters)
    try:
        import numpy as np
        mask = sme.mask
        if mask is None:
            print("MASK_SUMMARY output: None")
        else:
            segments = mask.segments if hasattr(mask, "segments") else mask
            if isinstance(segments, np.ndarray):
                segments = [segments]
            summary = []
            for idx, seg in enumerate(segments):
                vals, counts = np.unique(np.asarray(seg), return_counts=True)
                summary.append((idx, dict(zip(vals.tolist(), counts.tolist()))))
            print("MASK_SUMMARY output:", summary)
    except Exception as exc:
        print("MASK_SUMMARY output: error", exc)
    sme.save(fname_out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Create the synthetic spectrum with PySME"
    )
    parser.add_argument("input_file", help="sme file to convert")
    parser.add_argument("output_file", help="output file name")
    parser.add_argument("fit_parameters", nargs="+", help="Parameters to fit")
    parser.add_argument("--log_file", help="Logfile", default=None)

    args = parser.parse_args()

    fname_in = args.input_file
    fname_out = args.output_file
    fit_parameters = args.fit_parameters
    log_file = args.log_file

    main(
        fname_in=fname_in,
        fname_out=fname_out,
        fit_parameters=fit_parameters,
        log_file=log_file,
    )
