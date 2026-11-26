# PySME-GUI

A desktop interface for [PySME](https://github.com/SpectroscopyMadeEasy/PySME):
set up a star, synthesize a spectrum, compare it with an observation, edit the
mask, and fit stellar parameters or abundances.

## Install

### macOS (Apple Silicon, macOS 14 or newer)

1. Open `PySME-<version>-arm64.dmg`.
2. Drag **PySME** onto **Applications**.
3. Open PySME. The first launch downloads Python, PySME and their dependencies
   (needs a network connection and about 900 MB of disk). Later launches start
   in a few seconds.

You do not need Python, conda or Node installed.

The app is not signed with an Apple Developer ID. A disk image built on your own
Mac opens normally. One that was **downloaded** is blocked by Gatekeeper: after
the first attempt, open *System Settings → Privacy & Security* and choose
*Open Anyway*.

### Linux (x86_64)

```bash
linux/install.sh
```

This adds a `pysme-gui` command and a menu entry. The interface opens in your web
browser. `linux/install.sh --uninstall` removes it.

Intel Macs and Windows are not supported because PySME publishes no binary
packages for them.

## Using it

- **Try it:** press *Try the solar example*, then *Synthesize*. The first use of
  a model atmosphere grid downloads it to `~/.sme`.
- **Your own star:** load a VALD line list (extract stellar or extract all), set
  the parameters and a wavelength range, press *Synthesize*.
- **Observations:** *Import spectrum…* reads text or CSV files: `wave flux`,
  `wave flux error`, `wave flux error mask`, or `segment wave flux [error]`. A
  header row with column names overrides the guess. Wavelengths are in Å and
  must be in the same medium (air or vacuum) as the line list.
- **Mask:** choose *Line*, *Continuum* or *Exclude* above the plot and drag across
  pixels.
- **Fit:** pick free parameters in the *Fit* tab, or tick *Fit this abundance* in
  the periodic table, then press *Fit*.
- **Files:** *Save* writes a normal PySME `.sme` file that scripts can load.
  IDL `.inp` / `.sav` files can be opened. Double-clicking an `.sme` file opens
  it in the app.

Things worth knowing when reading results:

- If a setting changes after a synthesis, the plotted model is drawn dotted and
  marked *out of date*.
- If the observation has no error column, a signal-to-noise ratio is assumed.
  χ² and the parameter errors scale with it.
- PySME solves abundances on the *pattern* scale, which excludes [M/H]. The
  results table shows A(X) as used in the synthesis and prints the solver's own
  number underneath. When [M/H] is free too, no error is quoted for A(X).
- Two uncertainties are shown (least-squares covariance and PySME's SME
  estimate). Neither includes model, line-data or continuum systematics.

## Updates

*Updates & settings* compares your versions with PySME on PyPI and with the GUI
branch on GitHub (also checked quietly at start-up; a dot marks the button).
**Update now** builds a second environment, checks that PySME runs in it, and
only then switches over. A failed update changes nothing. After a restart you can
*Go back to previous version*.

The native window itself (the Swift part) only changes when you install a newer
disk image.

**Use your own PySME.** In the same dialog you can point the app at another
Python that has PySME, for example a development build. One-click update is then
switched off. Clear the field to return to the app's own environment.

## Where things live

| What | Where |
|---|---|
| App environment, logs, settings | `~/Library/Application Support/PySME-GUI` (macOS), `~/.local/share/pysme-gui` (Linux) |
| Model atmospheres, NLTE grids | `~/.sme`, shared with other PySME installations |

To uninstall on macOS, delete the app and the first folder. `~/.sme` is yours.
If something is broken, use *Help → Reinstall PySME…*.

## Development

The GUI is a small local web server (`pysme_gui/server.py`) that holds a real
`SME_Structure`; PySME runs in a child process (`worker.py`) so runs can be
stopped and a crash does not close the window. The interface is plain HTML and
JavaScript in `pysme_gui/static/`. On macOS, `macos/PySMEApp.swift` shows it in a
native window.

```bash
python -m pysme_gui                 # in any environment with pysme-astro
python -m pytest tests              # runs real PySME; downloads MARCS once
macos/build.sh                      # dist/PySME.app and the disk image
```

The server listens on 127.0.0.1 only, requires a random per-session token, and
checks the `Host` header. It does not open NumPy pickle files (`.npy`, `.npz`).

To check the packaged app without clicking through it, start the binary with
`PYSME_GUI_SELFTEST=<folder>`: it runs the solar example in the real window,
writes `report.json` and `window.png` there, and quits. Add
`PYSME_GUI_HOME=<empty folder>` to include the first-launch setup.

**Releasing.** The updater reads `UPDATE_REPO` and `UPDATE_REF` in
`pysme_gui/__init__.py` (currently branch `desktop-app`). To ship a GUI update,
raise `__version__` there and push that branch. If the work is merged into
another branch, change `UPDATE_REF` first.

Version 1.0 replaces the earlier Electron prototype, which remains in the history
of `master`. The example line list `pysme_gui/examples/sun.lin` is the VALD
extract from the PySME examples.
