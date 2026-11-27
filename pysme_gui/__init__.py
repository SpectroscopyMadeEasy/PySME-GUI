"""PySME-GUI: a desktop interface for PySME.

This module must stay importable without PySME installed: the installer and
updater read the version from here.
"""

__version__ = "0.3.0a3"

# The one-click updater installs release tags (v0.3.0a1, ...) of this repository.
# Pushing a branch releases nothing; see RELEASING.md.
UPDATE_REPO = "SpectroscopyMadeEasy/PySME-GUI"

# PySME versions this interface works with (pysme-astro on PyPI). New users get
# the recommended one; test it with this interface before every release. Users
# keep the version they chose as long as it stays within these limits.
PYSME_RECOMMENDED = "1.3.0rc3"
PYSME_MINIMUM = "1.2.0"
PYSME_BELOW = "2.0"

# GitHub repository in which "Report a problem" opens an issue. The program holds
# no e-mail address. macos/build.sh also reads this line.
ISSUE_REPO = "SpectroscopyMadeEasy/PySME-GUI"
