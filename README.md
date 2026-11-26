# PySME-GUI

A desktop interface for [PySME](https://github.com/SpectroscopyMadeEasy/PySME):
set up a star, synthesize a spectrum, compare it with an observation, edit the
mask, and fit stellar parameters or abundances.

**This is an alpha version** for internal testing (see
[RELEASING.md](RELEASING.md)).

![PySME-GUI after a fit, shown in light mode above the diagonal and in dark mode below it](assets/screenshot-light-dark.png)

*The solar example fitted to a simulated observation (upper left: light mode,
lower right: dark mode).*

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

Windows is planned for 0.3.0 and **does not work yet** (see
[RELEASING.md](RELEASING.md)). Linux has not been tried on a real computer yet.
Intel Macs are not supported because PySME publishes no binary packages for them.

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
- **Light or dark:** the button at the top right (*View → Appearance* on macOS)
  matches the system, or stays light or dark. The choice is remembered, and
  exported PNG figures are always light.
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

## Updates and PySME versions

The interface and PySME are versioned and updated separately, in *Updates &
settings*. Updates are checked quietly at start-up (a dot marks the button);
nothing is installed or switched on its own.

**Interface.** *Update interface* installs the newest release tag in your update
channel (released versions, or beta versions too) and checks that it starts with
your current PySME; your PySME version is not changed. If the new interface does not support that version, it says so and
changes nothing. *Back to previous interface* undoes it.

**PySME.** Several versions can be installed side by side. *Install* adds one
from PyPI without switching to it; *Use* switches (after a restart); *Remove*
deletes one that is not in use. Only releases with a ready-made package for your
computer are listed, pre-releases only on request. This interface supports the
range given by `PYSME_MINIMUM` and `PYSME_BELOW`, and new users get
`PYSME_RECOMMENDED`.

**Reproducing a setup.** Each installation records the exact version of every
package. *Help → Reinstall PySME…* rebuilds the one in use from that list.
For large runs, either use the Python shown under the version list (scripts then
get the same installation as the app), or save the *Package list…* and install it
elsewhere with Python 3.12:

```bash
pip install --only-binary pysme-astro -r pysme-1.2.0-packages.txt
```

Builds differ between systems, so results need not agree bit for bit. Model
atmospheres and NLTE grids in `~/.sme` are shared by all versions and are not
part of the list. Exported CSV files and saved `.sme` files record the PySME,
SMElib and interface versions that computed the spectrum (in CSV files as a
first line starting with `#`; with pandas use `read_csv(..., comment="#")`).

The native window itself (the Swift part, including the setup screen) only
changes when you install a newer disk image. That also brings the interface in
it, and keeps your PySME version if supported.

**Use your own PySME.** In the same dialog you can point the app at another
Python that has PySME, for example a development build. Version management is
then switched off. Clear the field to return to the app's own environment.

## Reporting a problem

*Report a problem…* (in *Updates & settings*, the ⋯ menu, the *Help* menu on
macOS, and next to error messages) writes one text file with the program
versions, system information and recent log. You read and can edit it before it
is saved to *Downloads*; then you send it by email, or attach it to a GitHub
issue (public; needs a GitHub account, and issues must be enabled in
`ISSUE_REPO`). Nothing is sent automatically. Spectra and line lists are never included; stellar
parameters and fit values only if you tick the box. User names in paths are
removed, but file and star names in the log may remain, so check the text.

The macOS app tries to start an email with the file attached. This needs a mail
program that accepts attachments; with webmail, attach the file yourself. If
setup fails or PySME does not start, the macOS error message has the same button
and writes a shorter report.

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

**Releasing.** Version stages, update channels and the steps for a release are
in [RELEASING.md](RELEASING.md).

Set `FEEDBACK_EMAIL` in `pysme_gui/__init__.py` to the address that should receive problem
reports, then rebuild the disk image.

This interface (0.3 and later) replaces the earlier Electron program, which
remains in the history of `master`. The example line list `pysme_gui/examples/sun.lin` is the VALD
extract from the PySME examples.
