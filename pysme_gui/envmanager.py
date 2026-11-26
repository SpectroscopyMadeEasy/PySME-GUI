"""Create, update and roll back the app's private Python environment.

Only the standard library is used: this file runs before PySME is installed.

Layout under the app home directory::

    slots/<id>/venv/       Python environment with PySME
    slots/<id>/app/        copy of the GUI code that belongs to it
    current.json           which slot is active, and the one before it
    python/, cache/        interpreters and wheels managed by uv
    settings.json          user settings

An install or update always builds a new slot, checks that PySME can be
imported from it, and only then switches over. A failed update therefore leaves
the working installation untouched.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile

PYTHON_VERSION = "3.12"
PYPI_URL = "https://pypi.org/pypi/pysme-astro/json"
SOURCE_ENV = "PYSME_GUI_UPDATE_SOURCE"


def app_home():
    if os.environ.get("PYSME_GUI_HOME"):
        return os.path.abspath(os.environ["PYSME_GUI_HOME"])
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/PySME-GUI")
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "pysme-gui")


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    os.replace(temp, path)


def current_record():
    return _read_json(os.path.join(app_home(), "current.json"), {})


def settings():
    return _read_json(os.path.join(app_home(), "settings.json"), {})


def write_settings(data):
    _write_json(os.path.join(app_home(), "settings.json"), data)


def slot_dir(slot_id):
    return os.path.join(app_home(), "slots", slot_id)


def slot_info(slot_id):
    if not slot_id:
        return None
    return _read_json(os.path.join(slot_dir(slot_id), "slot.json"), None)


def venv_python(slot_id):
    return os.path.join(slot_dir(slot_id), "venv", "bin", "python")


def uv_environment():
    env = dict(os.environ)
    home = app_home()
    env["UV_PYTHON_INSTALL_DIR"] = os.path.join(home, "python")
    env["UV_CACHE_DIR"] = os.path.join(home, "cache")
    for name in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME", "CONDA_PREFIX"):
        env.pop(name, None)
    return env


def find_uv():
    candidates = [
        os.environ.get("PYSME_GUI_UV"),
        os.path.join(app_home(), "bin", "uv"),
        shutil.which("uv"),
    ]
    for path in candidates:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    raise RuntimeError("The 'uv' installer was not found. Reinstall PySME-GUI.")


def _run(command, log, env, cwd=None):
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace", env=env, cwd=cwd,
    )
    output = []
    for line in process.stdout:
        line = line.rstrip()
        if line:
            output.append(line)
            log(line)
    if process.wait() != 0:
        detail = "\n".join(output[-12:])
        raise RuntimeError(f"Command failed: {os.path.basename(command[0])} {command[1]}\n{detail}")
    return output


def version_key(text):
    """Order release strings such as 1.2.0, 1.3.0rc1 and 1.3.0.dev4."""
    match = re.match(r"\s*v?(\d+(?:\.\d+)*)(.*)", str(text or ""))
    if not match:
        return ((0,), 0, 0)
    release = [int(p) for p in match.group(1).split(".")]
    while len(release) > 1 and release[-1] == 0:
        release.pop()
    suffix = match.group(2).split("+")[0].lstrip(".-_").lower()
    rank = 4
    for prefix, value in (("dev", 0), ("a", 1), ("b", 2), ("rc", 3), ("post", 5)):
        if suffix.startswith(prefix):
            rank = value
            break
    number = re.search(r"\d+", suffix)
    return (tuple(release), rank, int(number.group()) if number else 0)


def read_gui_version(text):
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.M)
    return match.group(1) if match else None


def _read_update_target(init_text):
    values = {}
    for name in ("UPDATE_REPO", "UPDATE_REF"):
        match = re.search(rf'^{name}\s*=\s*"([^"]+)"', init_text, re.M)
        values[name] = match.group(1) if match else None
    return values["UPDATE_REPO"], values["UPDATE_REF"]


def update_target():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "__init__.py")
    with open(path, encoding="utf-8") as handle:
        repo, ref = _read_update_target(handle.read())
    chosen = settings()
    return chosen.get("update_repo") or repo, chosen.get("update_ref") or ref


def _download(url, timeout=30):
    request = urllib.request.Request(url, headers={"User-Agent": "PySME-GUI-updater"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def latest_pysme_version():
    return json.loads(_download(PYPI_URL))["info"]["version"]


def latest_gui_version():
    local = os.environ.get(SOURCE_ENV)
    if local:
        with open(os.path.join(local, "pysme_gui", "__init__.py"), encoding="utf-8") as handle:
            return read_gui_version(handle.read())
    repo, ref = update_target()
    url = f"https://raw.githubusercontent.com/{repo}/{ref}/pysme_gui/__init__.py"
    try:
        return read_gui_version(_download(url).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise RuntimeError(f"nothing published yet at {repo}, branch '{ref}'") from exc
        raise


def fetch_gui_source(directory, log=print):
    """Return a directory that contains the newest ``pysme_gui`` package."""
    local = os.environ.get(SOURCE_ENV)
    if local:
        log(f"Using GUI source from {local}")
        return local
    repo, ref = update_target()
    url = f"https://codeload.github.com/{repo}/zip/refs/heads/{ref}"
    log(f"Downloading {url}")
    archive = os.path.join(directory, "gui.zip")
    with open(archive, "wb") as handle:
        handle.write(_download(url, timeout=120))
    target = os.path.realpath(os.path.join(directory, "source"))
    with zipfile.ZipFile(archive) as zipped:
        for member in zipped.namelist():
            resolved = os.path.realpath(os.path.join(target, member))
            if not resolved.startswith(target + os.sep):
                raise RuntimeError("The downloaded archive contains unsafe paths.")
        zipped.extractall(target)
    for name in os.listdir(target):
        candidate = os.path.join(target, name)
        if os.path.isfile(os.path.join(candidate, "pysme_gui", "__init__.py")):
            return candidate
    raise RuntimeError(f"No pysme_gui package found in {repo}@{ref}.")


_SMOKE_TEST = (
    "import json, pysme, pysme_gui, pysme_gui.server\n"
    "from pysme.smelib import _smelib\n"
    "print('SMOKE ' + json.dumps({'pysme': pysme.__version__, "
    "'smelib': str(_smelib.LibraryVersion()), 'gui': pysme_gui.__version__}))\n"
)


def install(source, pysme_requirement=None, bundle_id=None, log=print):
    """Build a new slot from the GUI code in ``source`` and make it current."""
    source = os.path.abspath(source)
    package = os.path.join(source, "pysme_gui")
    if not os.path.isfile(os.path.join(package, "__init__.py")):
        raise RuntimeError(f"No pysme_gui package in {source}")
    uv = find_uv()
    home = app_home()
    slot_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    directory = slot_dir(slot_id)
    os.makedirs(directory)
    env = uv_environment()
    try:
        app = os.path.join(directory, "app")
        shutil.copytree(
            package, os.path.join(app, "pysme_gui"),
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        log(f"STEP 1/3 Preparing Python {PYTHON_VERSION}")
        _run(
            [uv, "venv", "--python", PYTHON_VERSION, "--managed-python",
             os.path.join(directory, "venv")],
            log, env,
        )
        log("STEP 2/3 Installing PySME and its dependencies")
        command = [
            uv, "pip", "install", "--python", venv_python(slot_id),
            "-r", os.path.join(app, "pysme_gui", "requirements.txt"),
        ]
        if pysme_requirement:
            command.append(pysme_requirement)
        _run(command, log, env)
        log("STEP 3/3 Checking that PySME runs")
        test_env = dict(env)
        test_env["PYTHONPATH"] = app
        output = _run([venv_python(slot_id), "-c", _SMOKE_TEST], log, test_env, cwd=directory)
        smoke = next(line for line in output if line.startswith("SMOKE "))
        versions = json.loads(smoke[6:])
        _write_json(os.path.join(directory, "slot.json"), {
            "gui_version": versions["gui"],
            "pysme_version": versions["pysme"],
            "smelib_version": versions["smelib"],
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise

    record = current_record()
    previous = record.get("current")
    new_record = {
        "current": slot_id,
        "previous": previous if slot_info(previous) else None,
        "bundle_id": bundle_id or record.get("bundle_id"),
    }
    _write_json(os.path.join(home, "current.json"), new_record)
    _prune({new_record["current"], new_record["previous"]}, log)
    log(f"Installed PySME {versions['pysme']} with PySME-GUI {versions['gui']}.")
    return slot_id


def _prune(keep, log):
    root = os.path.join(app_home(), "slots")
    running = os.path.realpath(sys.prefix)
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if name in keep or running.startswith(os.path.realpath(path) + os.sep):
            continue
        log(f"Removing old environment {name}")
        shutil.rmtree(path, ignore_errors=True)


def rollback():
    record = current_record()
    previous = record.get("previous")
    if not slot_info(previous) or not os.path.exists(venv_python(previous)):
        raise RuntimeError("There is no earlier version to go back to.")
    record["current"], record["previous"] = previous, record.get("current")
    _write_json(os.path.join(app_home(), "current.json"), record)
    return previous


def update(update_pysme=None, update_gui=False, log=print):
    """Build and activate a new slot with a newer PySME and/or GUI."""
    record = current_record()
    info = slot_info(record.get("current"))
    if info is None:
        raise RuntimeError("PySME-GUI is not installed in the managed location.")
    with tempfile.TemporaryDirectory(prefix="pysme-gui-update-") as temp:
        if update_gui:
            source = fetch_gui_source(temp, log)
        else:
            source = os.path.join(slot_dir(record["current"]), "app")
        version = update_pysme or info["pysme_version"]
        return install(source, pysme_requirement=f"pysme-astro=={version}", log=log)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Manage the PySME-GUI environment")
    commands = parser.add_subparsers(dest="command", required=True)
    install_parser = commands.add_parser("install")
    install_parser.add_argument("--source", required=True)
    install_parser.add_argument("--pysme", help="requirement, e.g. pysme-astro==1.2.0")
    install_parser.add_argument("--bundle-id")
    commands.add_parser("rollback")
    args = parser.parse_args(argv)

    def log(text):
        print(text, flush=True)

    try:
        if args.command == "install":
            install(args.source, args.pysme, args.bundle_id, log=log)
        else:
            log(f"Now using {rollback()}")
    except Exception as exc:
        log(f"ERROR {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
