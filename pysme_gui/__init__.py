"""PySME-GUI: a desktop interface for PySME.

This module must stay importable without PySME installed: the installer and
updater read the version from here.
"""

__version__ = "1.0.0"

# Where the one-click updater looks for a newer GUI.
UPDATE_REPO = "SpectroscopyMadeEasy/PySME-GUI"
UPDATE_REF = "desktop-app"
