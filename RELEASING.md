# Versions and releases

## Stages

| Stage | Version | Who uses it | What may change |
|---|---|---|---|
| alpha | `0.3.0a1`, `a2`, … | the two internal testers | anything; may be unstable |
| beta | `0.3.0b1`, `b2`, … | invited testers | **feature freeze**: fixes and wording only |
| release | `0.3.0` | everyone | nothing but the version number since the last beta |

A release candidate (`rc1`) is added only when a last round is wanted before a
release. Versions are written as in Python (PEP 440): a < b < rc < release.

After a release, fixes become `0.3.1`; new features start again at `0.4.0a1`.
`1.0.0` waits until the program has been stable for a long time.

"Alpha is internal" means it is not announced and promises nothing. The
repository is public, so anyone can still see the tags and the code.

The interface and PySME are versioned separately. Each interface release names
the PySME that new users get (`PYSME_RECOMMENDED`). A final interface release
recommends a **final** PySME release; an alpha or beta may recommend a release
candidate. 0.3.0 begins with `1.3.0rc2`, the first PySME with a package for
Windows. From 0.3.0a3 it recommends
`1.3.0rc3`, which no longer runs code named in a `.sme` file.

## Platforms

From 0.3.0, macOS (Apple silicon, 15 or newer: PySME 1.3 has no packages for
older ones), Linux (x86_64) and Windows (x86_64) are supported. The maintainers
have no Windows machine, so Windows and Linux are tried on real computers by
beta testers. Because beta is a feature freeze, all platform code must be
written during alpha.

On Linux and Windows the interface opens in the web browser. PySME for Windows
is not on PyPI; for now the app lists and downloads it from PySME's GitHub
releases (users without access to GitHub cannot install on Windows until another
place is added). The Windows version has so far run only on GitHub's machines.

## From alpha to beta

- The features of 0.3.0 are fixed, and present on every supported platform.
- The main flows work on a maintainer's Mac: open and save, import an
  observation, synthesize, fit, NLTE, switch PySME versions, save a problem
  report and open a GitHub issue with it.
- One real update from one alpha tag to the next works, and so does going back.
- With the same settings, the interface gives the same result as a PySME script.
- A from-scratch install into an empty app folder works
  (`PYSME_GUI_SELFTEST` with an empty `PYSME_GUI_HOME`).
- Testers know how to report problems (a GitHub issue, or the person who
  invited them) and have a download place they can reach.
- The automated tests and a from-scratch install pass on GitHub's own Linux and
  Windows machines, so that testers do not meet failures that could have been
  found without them.

## From beta to release

- `PYSME_RECOMMENDED` is a final PySME release.
- On **each** of macOS, Linux and Windows, an outside tester installs from
  scratch, synthesizes and fits. At least one tester is a student who does not
  develop software.
- On Windows this includes an account without administrator rights and, if a
  tester can be found, Chinese Windows with a Chinese user name.
- No known crash, data loss or wrong result.
- About two weeks without a new serious problem in the last beta.

## Who receives which update

| Update channel | Receives |
|---|---|
| release only (the default) | releases |
| release + beta | beta, release candidates, releases |
| release + beta + alpha (shown only to those already in it) | everything |

Every new installation receives releases only, whatever stage is installed.
**Beta testers have to choose release + beta** in *Updates & settings*; tell them
so in the invitation. A choice once made is kept by later installations, so a
beta tester who moves to `0.3.0` still gets `0.4.0b1`, but not `0.4.0a1`.
Choosing a stricter channel never downgrades. Internal testers can set
`"channel": "alpha"` in `settings.json` in the app's data folder.

## Making a release

A **tag** is the release. The app installs tags only; pushing a branch releases
nothing. Tags older than `v0.3.0a1` belong to the earlier Electron program and
are ignored.

1. Set `__version__` in `pysme_gui/__init__.py`. Test the interface with
   `PYSME_RECOMMENDED`, and with any other PySME version you want to promise.
2. Run `python -m pytest tests`, and commit.
3. Push the commit and wait until its checks have passed, including *Packages*,
   which makes the three downloads and installs them.
4. Tag that commit: `git tag -a v0.3.0a1 -m "PySME-GUI 0.3.0a1"`.
5. Push the tag. **Pushing the tag publishes the update** to those whose channel
   includes it, whether or not the next step succeeds.
6. GitHub Release: none for alpha. For beta, release candidates and releases,
   *Packages* makes a **draft** release with the disk image, the Windows zip, the
   Linux archive and their checksums (marked as *pre-release* except for a
   release). Try the files, then publish the draft on the *Releases* page.
   Users who cannot reach GitHub need another download place (not set up yet).

Never move or reuse a published tag: if it is faulty, publish the next number.
The app refuses a tag whose code states another version.

Before releasing, also check:

- If the new interface needs a Python package that PySME does not already
  bring, existing installations fail the start check and keep the old
  interface. Handle that before adding one.
- If `static/` needs something new from the Swift shell, it only works for users
  who also install the new disk image.
- Online updates also need access to github.com.
