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

The interface and PySME are versioned separately. Each interface release
recommends a **final** PySME release (`PYSME_RECOMMENDED`).

## Platforms

From 0.3.0, macOS (Apple silicon), Linux (x86_64) and Windows (x86_64) are
supported. The maintainers have no Windows machine, so Windows and Linux are
tried on real computers by beta testers. Because beta is a feature freeze, all
platform code must be written during alpha.

Windows support is **not written yet**. It needs, at least:

- a PySME release with the Windows fixes, as a ready-made Windows package. It
  will not be on PyPI; for now the app lists and downloads it from GitHub
  releases (users without access to GitHub cannot install on Windows until
  another place is added);
- an installer that needs no administrator rights;
- changes in the environment manager, process handling and file browser.

## From alpha to beta

- The features of 0.3.0 are fixed, and present on every supported platform.
- The main flows work on a maintainer's Mac: open and save, import an
  observation, synthesize, fit, NLTE, switch PySME versions, mail a report with
  its attachment.
- One real update from one alpha tag to the next works, and so does going back.
- With the same settings, the interface gives the same result as a PySME script.
- A from-scratch install into an empty app folder works
  (`PYSME_GUI_SELFTEST` with an empty `PYSME_GUI_HOME`).
- The report address and a download place the testers can reach are set.
- The automated tests and a from-scratch install pass on GitHub's own Linux and
  Windows machines, so that testers do not meet failures that could have been
  found without them.

## From beta to release

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
| released versions only | releases |
| beta versions too | beta, release candidates, releases |
| alpha (shown only to those already in it) | everything |

The first installation sets the channel from the stage of what is installed.
Later installations keep it. So a beta tester who moves to `0.3.0` still gets
`0.4.0b1`, but not `0.4.0a1`. Choosing a stricter channel never downgrades.
Internal testers can set `"channel": "alpha"` in `settings.json` in the app's
data folder.

## Making a release

A **tag** is the release. The app installs tags only; pushing a branch releases
nothing. Tags older than `v0.3.0a1` belong to the earlier Electron program and
are ignored.

1. Set `__version__` in `pysme_gui/__init__.py`. Test the interface with
   `PYSME_RECOMMENDED`, and with any other PySME version you want to promise.
2. Run `python -m pytest tests`, and commit.
3. Tag that commit: `git tag -a v0.3.0a1 -m "PySME-GUI 0.3.0a1"`.
4. Build the disk image from the clean, tagged checkout with `macos/build.sh`
   (it prints a note if the build is not the tagged release).
5. Push the commit and the tag. **Pushing the tag publishes the update.**
6. GitHub Release: none for alpha; for beta, one marked as *pre-release* with the
   disk image; for a release, a normal one. Users who cannot reach GitHub need
   another download place (not set up yet).

Never move or reuse a published tag: if it is faulty, publish the next number.
The app refuses a tag whose code states another version.

Before releasing, also check:

- If the new interface needs a Python package that PySME does not already
  bring, existing installations fail the start check and keep the old
  interface. Handle that before adding one.
- If `static/` needs something new from the Swift shell, it only works for users
  who also install the new disk image.
- Online updates also need access to github.com.
