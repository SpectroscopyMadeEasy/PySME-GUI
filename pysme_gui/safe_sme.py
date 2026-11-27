"""Open SME files that may come from anyone.

PySME up to 1.3.0rc2 reads ``.sme`` files with flex-format, which runs the code
a file names. So the interface does not let PySME read them: ``sme_reader`` (the
reader newer PySME uses) reads the current format and PySME only puts the
structure together. The older zip format goes to PySME's own reader for it,
which reads plain data, except for line lists kept as Feather.
"""

import os
import tarfile
import zipfile

from pysme import persistence
from pysme.sme import SME_Structure

from .sme_reader import UnsafeFileError, read

# IDL save files are parsed by scipy; nothing in them is run.
IDL_EXTENSIONS = (".sav", ".inp", ".out")


def _load_old(path):
    # Opened once: PySME reads the very file that was checked, even if another
    # takes its place under this name.
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        # PySME's reader chooses Feather by this ending.
        if any(name.endswith("data.feather") for name in names):
            raise UnsafeFileError(
                "This file was not opened: it is an old SME file that keeps its line "
                "list in the Feather format, which the interface does not read. If "
                "you trust it, convert it with a current PySME and pyarrow."
            )
        return persistence.loads_v1(archive, SME_Structure(), names)


def load(path):
    """Load an ``.sme`` or IDL SME file. Other kinds of file are refused."""
    extension = os.path.splitext(path)[1]
    if extension in IDL_EXTENSIONS:
        return SME_Structure.load(path)
    if extension.lower() != ".sme":
        # PySME would unpickle .npy and .npz files, which can run any code.
        raise UnsafeFileError(
            f"This file was not opened: only .sme files and IDL files "
            f"({', '.join(IDL_EXTENSIONS)}) are opened, not "
            f"“{extension or 'a file without an extension'}”."
        )
    if tarfile.is_tarfile(path):
        sme = persistence.from_flex(read(path), SME_Structure())
    elif zipfile.is_zipfile(path):
        sme = _load_old(path)
    else:
        raise ValueError("This is not an SME file.")
    # PySME 1.3 tidies the stored citations after loading; 1.2 does not.
    tidy = getattr(sme, "_update_stored_citation_info", None)
    if tidy:
        tidy()
    return sme
