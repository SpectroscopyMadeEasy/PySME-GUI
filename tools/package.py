#!/usr/bin/env python3
"""Make the Windows and Linux downloads of a release.

    tools/package.py                 # into dist/packages/
    tools/package.py --output DIR

Both are made from the committed files (``git archive``), so uncommitted changes
are not in them, and line endings follow .gitattributes. Each holds one folder
with the interface, the installer of that system and a short note on how to
start it. The Mac disk image is made by macos/build.sh.
"""

import argparse
import os
import re
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOTE = "READ ME FIRST.txt"

WINDOWS_NOTE = """\
PySME-GUI {version} for Windows (64-bit, x86)

1. Extract the whole zip file first (right-click it, "Extract All...").
2. Open the folder "windows" and double-click "Install PySME.cmd".
   If Windows shows "Windows protected your PC", choose "More info",
   then "Run anyway". The scripts are not signed.
3. Start PySME from the desktop or the Start menu. It opens in your web
   browser.

No administrator rights are needed. Installing needs a network connection
that reaches github.com and pypi.org: Python and PySME are downloaded.
To remove PySME, double-click "Uninstall PySME.cmd" in the folder "windows".
"""

LINUX_NOTE = """\
PySME-GUI {version} for Linux (x86_64)

1. Unpack the archive.
2. In a terminal, in the unpacked folder, run:   linux/install.sh
3. Start PySME from the applications menu, or run:   ~/.local/bin/pysme-gui
   It opens in your web browser.

No administrator rights are needed. Installing needs curl and a network
connection that reaches github.com and pypi.org: Python and PySME are
downloaded. To remove PySME, run:   linux/install.sh --uninstall
"""

# name: (archive format, what it needs besides LICENSE and the interface, note, line ending)
PACKAGES = {
    "Windows-x64": ("zip", ["windows", "assets/pysme-icon.ico"], WINDOWS_NOTE, "\r\n"),
    "Linux-x86_64": ("tar.gz", ["linux", "assets/pysme-icon.png"], LINUX_NOTE, "\n"),
}


def git(*arguments):
    return subprocess.run(
        ["git", "-C", ROOT, *arguments], check=True, capture_output=True, text=True
    ).stdout


def committed_version():
    found = re.search(r'^__version__ = "([^"]+)"', git("show", "HEAD:pysme_gui/__init__.py"), re.M)
    if not found:
        raise SystemExit("No __version__ in the committed pysme_gui/__init__.py")
    return found.group(1)


def package(output):
    """Write every package into ``output``; return their paths."""
    version = committed_version()
    os.makedirs(output, exist_ok=True)
    written = []
    with tempfile.TemporaryDirectory() as work:
        for system, (kind, extra, text, newline) in PACKAGES.items():
            note = os.path.join(work, NOTE)
            with open(note, "w", encoding="ascii", newline=newline) as handle:
                handle.write(text.format(version=version))
            path = os.path.join(os.path.abspath(output), f"PySME-{version}-{system}.{kind}")
            # --add-file takes the folder name given by the --prefix before it.
            git("archive", f"--format={kind}", f"--prefix=PySME-{version}/",
                f"--add-file={note}", "-o", path, "HEAD", "--", "LICENSE", "pysme_gui", *extra)
            written.append(path)
    return written


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", default=os.path.join(ROOT, "dist", "packages"))
    for path in package(parser.parse_args().output):
        print(path)


if __name__ == "__main__":
    main()
