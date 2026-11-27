"""Manage the app's private Python environments and copies of the interface.

Only the standard library is used: this file runs before PySME is installed.

The interface and PySME are versioned separately. Layout under the app home::

    gui/<id>/pysme_gui/    a copy of the interface code
    envs/<id>/venv/        a Python environment with one PySME version
    envs/<id>/env.json     its versions; written last, so it marks "complete"
    envs/<id>/lock.txt     the exact version of every package in it
    current.json           which interface and environment are in use
    current.sh             the same two names, for the Linux launcher
    launch.py, bin/uv.exe  the Windows launcher and installer tool
    python/, cache/        interpreters and wheels managed by uv
    settings.json          user settings

PySME environments are only removed when the user asks (or replaced by a
repair). Whatever is built is first checked to start, and only then used, so a
failed step leaves the working installation untouched.

``current.json``::

    {"current": {"gui": id, "env": id},
     "previous": {"gui": id, "env": id} or null,   before the latest change
     "previous_gui": id or null,                   for going back one interface
     "bundle_id": ..., "notice": ...}
"""

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from email.utils import parsedate_to_datetime

PYTHON_VERSION = "3.12"
PACKAGE = "pysme-astro"
PYPI_URL = f"https://pypi.org/pypi/{PACKAGE}/json"
SOURCE_ENV = "PYSME_GUI_UPDATE_SOURCE"
# PySME for Windows is published with PySME's releases on GitHub, from this one on.
PYSME_REPO = "SpectroscopyMadeEasy/PySME"
FIRST_WINDOWS_PYSME = "1.3.0rc2"

# Interface releases are git tags. Older tags belong to the Electron program.
RELEASE_TAG = re.compile(rb"refs/tags/v(\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?)(?=[\s\x00^]|$)")
FIRST_RELEASE = "0.3.0a1"
# Each update channel also receives everything from the ones before it.
CHANNELS = ("stable", "beta", "alpha")


class NoRelease(RuntimeError):
    """Nothing has been published for the chosen channel."""


def app_home():
    if os.environ.get("PYSME_GUI_HOME"):
        return os.path.abspath(os.environ["PYSME_GUI_HOME"])
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/PySME-GUI")
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/AppData/Local")
        return os.path.join(base, "PySME-GUI")
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "pysme-gui")


def keep_private():
    """Let only this user into the app's folder (Linux and macOS).

    It holds the programs that get run, the logs and the record with the access
    token. A folder that belongs to someone else is refused. On Windows the
    folder under LOCALAPPDATA is private already.
    """
    if os.name != "posix":
        return
    home = app_home()
    os.makedirs(home, mode=0o700, exist_ok=True)
    info = os.stat(home)
    if info.st_uid != os.getuid():
        raise RuntimeError(f"{home} belongs to another user; PySME-GUI needs a folder of your own.")
    if info.st_mode & 0o077:
        os.chmod(home, info.st_mode & 0o7700)


def _patiently(action):
    """Run a file operation that Windows may refuse for a moment, while another
    request or a virus scanner has the file open."""
    for attempt in range(20):
        try:
            return action()
        except PermissionError:
            if sys.platform != "win32" or attempt == 19:
                raise
            time.sleep(0.05)


def _read_json(path, default):
    def read():
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    try:
        return _patiently(read)
    except (OSError, ValueError):
        return default


def _write_text(path, text):
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    # A temporary file of its own for every write: two requests may write at once.
    handle, temp = tempfile.mkstemp(dir=folder, prefix=os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.chmod(temp, 0o644)
        _patiently(lambda: os.replace(temp, path))
    except BaseException:
        if os.path.exists(temp):
            os.remove(temp)
        raise


def _write_json(path, data):
    _write_text(path, json.dumps(data, indent=2))


def settings():
    return _read_json(os.path.join(app_home(), "settings.json"), {})


def write_settings(data):
    _write_json(os.path.join(app_home(), "settings.json"), data)


_settings_lock = threading.Lock()


def change_settings(remove=(), **values):
    """Change some settings and keep the others, even when requests overlap."""
    with _settings_lock:
        data = settings()
        data.update(values)
        for key in remove:
            data.pop(key, None)
        write_settings(data)


def _new_id():
    return time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]


def is_within(path, directory):
    """True if ``path`` lies inside ``directory`` (Windows ignores letter case)."""
    def real(name):
        return os.path.normcase(os.path.realpath(name))

    return real(path).startswith(real(directory).rstrip(os.sep) + os.sep)


def _is_running_from(directory):
    return is_within(sys.prefix, directory)


# ------------------------------------------------------------------- versions


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


def is_prerelease(text):
    return version_key(text)[1] < 4


def stage(version):
    """The channel a release belongs to: alpha, beta (also rc) or stable."""
    rank = version_key(version)[1]
    return "stable" if rank >= 4 else "beta" if rank >= 2 else "alpha"


def in_channel(version, channel):
    return CHANNELS.index(stage(version)) <= CHANNELS.index(channel)


def channel():
    """Which interface releases the user receives: releases only, unless another
    channel was chosen, whatever stage is installed."""
    chosen = settings().get("channel")
    return chosen if chosen in CHANNELS else "stable"


def set_channel(name):
    if name not in CHANNELS:
        raise RuntimeError(f"Unknown update channel: {name}")
    change_settings(channel=name)


def _constant(text, name):
    match = re.search(rf'^{name}\s*=\s*"([^"]*)"', text, re.M)
    return match.group(1) if match else None


def read_constants(init_text):
    """What an interface release says about itself and the PySME it works with."""
    return {
        name: _constant(init_text, key)
        for name, key in (
            ("version", "__version__"),
            ("recommended", "PYSME_RECOMMENDED"),
            ("minimum", "PYSME_MINIMUM"),
            ("below", "PYSME_BELOW"),
            ("repo", "UPDATE_REPO"),
        )
    }


def package_constants(package_dir):
    with open(os.path.join(package_dir, "__init__.py"), encoding="utf-8") as handle:
        return read_constants(handle.read())


def read_gui_version(text):
    return read_constants(text)["version"]


def supports(constants, pysme_version):
    """True if an interface with these constants accepts this PySME version."""
    key = version_key(pysme_version)
    if constants.get("minimum") and key < version_key(constants["minimum"]):
        return False
    # Compare release numbers only, so that 2.0.0rc1 does not count as "below 2".
    return not constants.get("below") or key[0] < version_key(constants["below"])[0]


def supported_text(constants):
    return f"{constants.get('minimum')} or newer, below {constants.get('below')}"


# --------------------------------------------------- what is installed and used


def gui_dir(gui_id):
    return os.path.join(app_home(), "gui", gui_id)


def gui_package(gui_id):
    return os.path.join(gui_dir(gui_id), "pysme_gui")


def gui_info(gui_id):
    if not gui_id:
        return None
    return _read_json(os.path.join(gui_dir(gui_id), "gui.json"), None)


def env_dir(env_id):
    return os.path.join(app_home(), "envs", env_id)


def venv_python(venv):
    """The Python of a virtual environment."""
    if sys.platform == "win32":
        return os.path.join(venv, "Scripts", "python.exe")
    return os.path.join(venv, "bin", "python")


def env_python(env_id):
    return venv_python(os.path.join(env_dir(env_id), "venv"))


def env_info(env_id):
    """Facts about a complete, usable environment, or None."""
    if not env_id:
        return None
    info = _read_json(os.path.join(env_dir(env_id), "env.json"), None)
    if not info or not os.path.exists(env_python(env_id)):
        return None
    return dict(info, id=env_id, python=env_python(env_id))


def installed_envs():
    try:
        names = os.listdir(os.path.join(app_home(), "envs"))
    except OSError:
        return []
    # A name starting with a dot is an installation that is being removed.
    found = [info for info in (env_info(n) for n in names if not n.startswith(".")) if info]
    return sorted(found, key=lambda i: (version_key(i["pysme_version"]), i["created"]), reverse=True)


def current_record():
    record = _read_json(os.path.join(app_home(), "current.json"), {})
    return record if isinstance(record, dict) else {}


def selection(record=None):
    """The (interface id, environment id) in use; either may be None."""
    chosen = (current_record() if record is None else record).get("current")
    if not isinstance(chosen, dict):
        return None, None
    return chosen.get("gui"), chosen.get("env")


def _write_record(record):
    _write_json(os.path.join(app_home(), "current.json"), record)
    gui_id, env_id = selection(record)
    _write_text(os.path.join(app_home(), "current.sh"), f"gui={gui_id}\nenv={env_id}\n")


def activate(gui_id, env_id, bundle_id=None, notice=None, log=print):
    """Use this interface and environment from the next start on."""
    record = current_record()
    old_gui, old_env = selection(record)
    previous = record.get("previous")
    if (old_gui, old_env) != (gui_id, env_id) and gui_info(old_gui) and env_info(old_env):
        previous = {"gui": old_gui, "env": old_env}
    previous_gui = record.get("previous_gui")
    if old_gui != gui_id and gui_info(old_gui):
        previous_gui = old_gui
    new = {
        "current": {"gui": gui_id, "env": env_id},
        "previous": previous,
        "previous_gui": previous_gui,
        "bundle_id": bundle_id or record.get("bundle_id"),
        "notice": notice,
    }
    _write_record(new)
    _prune_gui({gui_id, previous_gui, (previous or {}).get("gui")}, log)


def _prune_gui(keep, log):
    """Old interface copies are tiny and replaceable, unlike environments."""
    root = os.path.join(app_home(), "gui")
    running = os.path.dirname(os.path.abspath(__file__))
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if name in keep or is_within(running, path):
            continue
        shutil.rmtree(path, ignore_errors=True)


def remove_env(env_id, log=print):
    record = current_record()
    if env_id == selection(record)[1]:
        raise RuntimeError("This PySME is the one in use. Switch to another one first.")
    if not env_info(env_id):
        raise RuntimeError("That PySME installation no longer exists.")
    if _is_running_from(env_dir(env_id)):
        raise RuntimeError("This PySME is still running. Restart first.")
    log(f"Removing PySME {env_info(env_id)['pysme_version']}")
    # Renamed first: Windows refuses that while a program still uses the files,
    # so an installation in use is never left half removed.
    doomed = os.path.join(app_home(), "envs", f".removing-{env_id}")
    try:
        _patiently(lambda: os.rename(env_dir(env_id), doomed))
    except PermissionError:
        raise RuntimeError(
            "This PySME is still in use, perhaps by another PySME window. "
            "Close it and try again."
        )
    shutil.rmtree(doomed, ignore_errors=True)
    if (record.get("previous") or {}).get("env") == env_id:
        record["previous"] = None
        _write_record(record)


# ------------------------------------------------------------------- building


def uv_environment():
    env = dict(os.environ)
    home = app_home()
    env["UV_PYTHON_INSTALL_DIR"] = os.path.join(home, "python")
    env["UV_CACHE_DIR"] = os.path.join(home, "cache")
    for name in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME", "CONDA_PREFIX"):
        env.pop(name, None)
    if sys.platform == "win32":
        # Pythons we start read and write UTF-8, whatever the system's code page.
        env["PYTHONUTF8"] = "1"
    return env


def find_uv():
    candidates = [
        os.environ.get("PYSME_GUI_UV"),
        os.path.join(app_home(), "bin", "uv.exe" if sys.platform == "win32" else "uv"),
        shutil.which("uv"),
    ]
    for path in candidates:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    raise RuntimeError("The 'uv' installer was not found. Reinstall PySME-GUI.")


def _run(command, log, env, cwd=None):
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", env=env, cwd=cwd,
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


def _freeze(uv, python, env):
    result = subprocess.run(
        [uv, "pip", "freeze", "--python", python], capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Could not list the installed packages:\n{result.stderr.strip()}")
    return result.stdout


SMOKE_TEST = (
    "import json, platform, pysme, pysme_gui, pysme_gui.server\n"
    "from pysme.sme_synth import SME_DLL\n"
    "print('SMOKE ' + json.dumps({'pysme': pysme.__version__, "
    "'smelib': str(SME_DLL().SMELibraryVersion()), 'gui': pysme_gui.__version__, "
    "'python': platform.python_version()}))\n"
)


def smoke_test(gui_id, python, log):
    """Check that this interface starts with the PySME of this Python."""
    env = uv_environment()
    env["PYTHONPATH"] = gui_dir(gui_id)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    output = _run([python, "-c", SMOKE_TEST], log, env, cwd=app_home())
    line = next(line for line in output if line.startswith("SMOKE "))
    return json.loads(line[6:])


def create_env(version, gui_id, lock=None, log=print):
    """Install one PySME version into a new environment; return its id.

    With ``lock`` (the text of a lock file) every package gets exactly the
    version it had before.
    """
    uv = find_uv()
    env_id = _new_id()
    directory = env_dir(env_id)
    os.makedirs(directory)
    env = uv_environment()
    python = env_python(env_id)
    try:
        log(f"STEP 1/3 Preparing Python {PYTHON_VERSION}")
        _run(
            [uv, "venv", "--python", PYTHON_VERSION, "--managed-python",
             os.path.join(directory, "venv")],
            log, env,
        )
        log("STEP 2/3 Installing PySME and its dependencies")
        # Never compile PySME: users have no Fortran compiler.
        command = [uv, "pip", "install", "--python", python, "--only-binary", PACKAGE]
        if lock:
            _write_text(os.path.join(directory, "wanted.txt"), lock)
            command += ["-r", os.path.join(directory, "wanted.txt")]
        else:
            command.append(requirement(version))
        _run(command, log, env)
        log("STEP 3/3 Checking that PySME runs")
        versions = smoke_test(gui_id, python, log)
        if version_key(versions["pysme"]) != version_key(version):
            raise RuntimeError(f"Asked for PySME {version} but got {versions['pysme']}.")
        _write_text(os.path.join(directory, "lock.txt"), _freeze(uv, python, env))
        _write_json(os.path.join(directory, "env.json"), {
            "pysme_version": versions["pysme"],
            "smelib_version": versions["smelib"],
            "python_version": versions["python"],
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return env_id


def windows_wheel_url(version):
    """Where a PySME release keeps its package for Windows."""
    python = "cp" + PYTHON_VERSION.replace(".", "")
    name = f"{PACKAGE.replace('-', '_')}-{version}-{python}-{python}-win_amd64.whl"
    return f"https://github.com/{PYSME_REPO}/releases/download/v{version}/{name}"


def requirement(version):
    """What to ask the installer for. PySME for Windows is not on PyPI; it comes
    from its release on GitHub."""
    if sys.platform == "win32":
        return f"{PACKAGE} @ {windows_wheel_url(version)}"
    return f"{PACKAGE}=={version}"


def read_lock(env_id):
    try:
        with open(os.path.join(env_dir(env_id), "lock.txt"), encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


def export_lock(env_id, path):
    """Write the package list that recreates an environment elsewhere."""
    info, lock = env_info(env_id), read_lock(env_id)
    if not info or not lock:
        raise RuntimeError("There is no package list for this installation.")
    header = (
        f"# PySME {info['pysme_version']}, SMElib: {info['smelib_version']}\n"
        f"# Exported by PySME-GUI from a Python {info['python_version']} environment "
        f"on {platform.system()} {platform.machine()}.\n"
        f"# Recreate it with Python {PYTHON_VERSION}:\n"
        f"#     pip install --only-binary {PACKAGE} -r this-file.txt\n"
        "# Builds differ between systems, so results need not agree bit for bit.\n"
        "# Model atmospheres and NLTE grids (~/.sme) are not covered by this list.\n"
    )
    _write_text(os.path.abspath(os.path.expanduser(path)), header + lock)


def add_gui(source):
    """Copy the interface code found in ``source``; return its id."""
    package = os.path.join(os.path.abspath(source), "pysme_gui")
    if not os.path.isfile(os.path.join(package, "__init__.py")):
        raise RuntimeError(f"No pysme_gui package in {source}")
    gui_id = _new_id()
    shutil.copytree(
        package, gui_package(gui_id), ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    _write_json(os.path.join(gui_dir(gui_id), "gui.json"), package_constants(package))
    return gui_id


# ------------------------------------------------------------------- actions


def setup(source, bundle_id=None, repair=False, log=print):
    """First launch, a newer disk image, or a repair.

    The interface comes from ``source``. The PySME the user chose is kept when
    that interface supports it; otherwise, and on the first launch, the
    recommended version is used. A repair rebuilds the chosen environment from
    its package list.
    """
    keep_private()
    gui_id = add_gui(source)
    try:
        constants = gui_info(gui_id)
        chosen = env_info(selection()[1])
        notice = replaced = None
        if chosen and supports(constants, chosen["pysme_version"]):
            env_id = chosen["id"]
            if repair:
                env_id = create_env(
                    chosen["pysme_version"], gui_id, lock=read_lock(env_id), log=log
                )
                replaced = chosen["id"]
            else:
                log("STEP 3/3 Checking that PySME runs")
                smoke_test(gui_id, chosen["python"], log)
        else:
            wanted = constants["recommended"]
            same = [i for i in installed_envs()
                    if version_key(i["pysme_version"]) == version_key(wanted)]
            if same and not repair:
                env_id = same[0]["id"]
                log("STEP 3/3 Checking that PySME runs")
                smoke_test(gui_id, same[0]["python"], log)
            else:
                env_id = create_env(wanted, gui_id, log=log)
            if chosen:
                notice = (
                    f"This interface needs PySME {supported_text(constants)}, so it now uses "
                    f"PySME {wanted}. PySME {chosen['pysme_version']} is still installed."
                )
    except BaseException:
        shutil.rmtree(gui_dir(gui_id), ignore_errors=True)
        raise
    activate(gui_id, env_id, bundle_id=bundle_id, notice=notice, log=log)
    if replaced and not _is_running_from(env_dir(replaced)):
        try:
            remove_env(replaced, log=log)
        except (OSError, RuntimeError) as exc:      # the repair itself succeeded
            log(f"The replaced installation was kept: {exc}")
    log(f"Using PySME {env_info(env_id)['pysme_version']} with PySME-GUI {constants['version']}.")
    return env_id


def _current_parts():
    gui_id, env_id = selection()
    if not gui_info(gui_id) or not env_info(env_id):
        raise RuntimeError("PySME-GUI is not installed in the managed location.")
    return gui_id, env_info(env_id)


def install_pysme(version, log=print):
    """Add a PySME version. It is not used until the user switches to it."""
    gui_id, _ = _current_parts()
    constants = gui_info(gui_id)
    if not supports(constants, version):
        raise RuntimeError(f"This interface needs PySME {supported_text(constants)}.")
    if any(version_key(i["pysme_version"]) == version_key(version) for i in installed_envs()):
        raise RuntimeError(f"PySME {version} is already installed.")
    env_id = create_env(version, gui_id, log=log)
    log(f"Installed PySME {version}.")
    return env_id


def use_env(env_id, log=print):
    gui_id, _ = _current_parts()
    info = env_info(env_id)
    if not info:
        raise RuntimeError("That PySME installation no longer exists.")
    constants = gui_info(gui_id)
    if not supports(constants, info["pysme_version"]):
        raise RuntimeError(f"This interface needs PySME {supported_text(constants)}.")
    log("STEP Checking that PySME runs")
    smoke_test(gui_id, info["python"], log)
    activate(gui_id, env_id, log=log)


def update_gui(log=print):
    """Install the newest interface release. The PySME in use is not changed."""
    current, env = _current_parts()
    published = latest_gui()
    if version_key(published["version"]) <= version_key(gui_info(current)["version"]):
        raise RuntimeError("There is no newer interface.")
    with tempfile.TemporaryDirectory(prefix="pysme-gui-update-") as temp:
        source = fetch_gui_source(temp, published.get("tag"), log)
        constants = package_constants(os.path.join(source, "pysme_gui"))
        if constants["version"] != published["version"]:
            raise RuntimeError("The downloaded interface is not the announced version.")
        if not supports(constants, env["pysme_version"]):
            raise RuntimeError(
                f"The new interface needs PySME {supported_text(constants)}, but you are using "
                f"{env['pysme_version']}. Install and switch to a supported version first; "
                "the one you use now stays installed."
            )
        gui_id = add_gui(source)
    try:
        log("STEP Checking that the new interface runs")
        smoke_test(gui_id, env["python"], log)
    except BaseException:
        shutil.rmtree(gui_dir(gui_id), ignore_errors=True)
        raise
    activate(gui_id, env["id"], log=log)
    return gui_id


def rollback_gui(log=print):
    """Go back to the interface used before; PySME is not changed."""
    _, env = _current_parts()
    previous = current_record().get("previous_gui")
    info = gui_info(previous)
    if not info:
        raise RuntimeError("There is no earlier interface to go back to.")
    if not supports(info, env["pysme_version"]):
        raise RuntimeError(f"The earlier interface needs PySME {supported_text(info)}.")
    log("STEP Checking that the earlier interface runs")
    smoke_test(previous, env["python"], log)
    activate(previous, env["id"], log=log)


# ------------------------------------------------------------------- online


def update_repository():
    constants = package_constants(os.path.dirname(os.path.abspath(__file__)))
    return settings().get("update_repo") or constants["repo"]


def _download(url, timeout=30):
    request = urllib.request.Request(url, headers={"User-Agent": "PySME-GUI-updater"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def compatible_wheel(filename, system, machine, system_version):
    """True if this wheel installs on that system with the app's Python.

    ``system`` is sys.platform; ``system_version`` is the macOS or glibc version.
    """
    if not filename.endswith(".whl"):
        return False
    parts = filename[:-4].split("-")
    if len(parts) < 5:
        return False
    pythons, abis, platforms = (p.split(".") for p in parts[-3:])
    want = "cp" + PYTHON_VERSION.replace(".", "")
    minor = int(PYTHON_VERSION.split(".")[1])
    stable_abi = "abi3" in abis and any(
        re.fullmatch(r"cp3(\d+)", p) and int(p[3:]) <= minor for p in pythons
    )
    if not (want in pythons or "py3" in pythons or stable_abi):
        return False
    have = version_key(system_version)[0] if system_version else None
    for tag in platforms:
        if tag == "any":
            return True
        if system == "darwin":
            match = re.fullmatch(r"macosx_(\d+)_(\d+)_(\w+)", tag)
            if match and match.group(3) in (machine, "universal2"):
                if have is None or version_key(f"{match[1]}.{match[2]}")[0] <= have:
                    return True
        elif system.startswith("linux"):
            match = re.fullmatch(r"(manylinux_(\d+)_(\d+)|manylinux(\d+)|linux)_(\w+)", tag)
            if match and match.group(5) == machine:
                legacy = {"1": "2.5", "2010": "2.12", "2014": "2.17"}.get(match.group(4))
                needs = f"{match[2]}.{match[3]}" if match.group(2) else legacy
                if have is None or needs is None or version_key(needs)[0] <= have:
                    return True
        elif system == "win32":
            if tag == {"amd64": "win_amd64", "arm64": "win_arm64"}.get(machine.lower()):
                return True
    return False


def _this_system():
    if sys.platform == "darwin":
        release = platform.mac_ver()[0]
    else:
        release = platform.libc_ver()[1]
    return sys.platform, platform.machine(), release


def parse_releases(data, system=None):
    """Releases in PyPI's JSON that can be installed here, newest first."""
    system = system or _this_system()
    found = []
    for version, files in data.get("releases", {}).items():
        if any(not f.get("yanked") and compatible_wheel(f["filename"], *system) for f in files):
            found.append({
                "version": version,
                "prerelease": is_prerelease(version),
                "date": min(f.get("upload_time", "") for f in files)[:10],
            })
    return sorted(found, key=lambda r: version_key(r["version"]), reverse=True)


def _file_date(url):
    """The date of a downloadable file, "" if not stated, None if there is none.
    Only its description is fetched. Unlike GitHub's web API this has no hourly
    limit."""
    request = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": "PySME-GUI-updater"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            stated = response.headers.get("Last-Modified")
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise
    try:
        return parsedate_to_datetime(stated).strftime("%Y-%m-%d") if stated else ""
    except (TypeError, ValueError):
        return ""


def windows_releases():
    """PySME releases with a package for Windows, newest first."""
    url = f"https://github.com/{PYSME_REPO}.git/info/refs?service=git-upload-pack"
    tags = {m.decode() for m in RELEASE_TAG.findall(_download(url))}
    first = version_key(FIRST_WINDOWS_PYSME)
    found = []
    for version in sorted(tags, key=version_key, reverse=True):
        if version_key(version) < first:
            break
        date = _file_date(windows_wheel_url(version))
        if date is not None:
            found.append(
                {"version": version, "prerelease": is_prerelease(version), "date": date}
            )
    return found


def available_pysme():
    if sys.platform == "win32":
        if platform.machine().lower() != "amd64":
            return []
        return windows_releases()
    return parse_releases(json.loads(_download(PYPI_URL, timeout=40)))


def parse_tags(advertisement):
    """Release versions among the tags in git's list of references."""
    found = {m.decode() for m in RELEASE_TAG.findall(advertisement)}
    first = version_key(FIRST_RELEASE)
    return sorted((v for v in found if version_key(v) >= first), key=version_key, reverse=True)


def published_releases():
    # The list git itself reads. Unlike the web API it has no hourly limit,
    # which users behind one shared address would quickly reach.
    url = f"https://github.com/{update_repository()}.git/info/refs?service=git-upload-pack"
    return parse_tags(_download(url))


def latest_gui():
    """Constants of the newest interface release in the user's channel."""
    local = os.environ.get(SOURCE_ENV)
    if local:
        return package_constants(os.path.join(local, "pysme_gui"))
    chosen = channel()
    allowed = [v for v in published_releases() if in_channel(v, chosen)]
    if not allowed:
        raise NoRelease("No interface release has been published for this channel yet.")
    tag = f"v{allowed[0]}"
    url = f"https://raw.githubusercontent.com/{update_repository()}/{tag}/pysme_gui/__init__.py"
    constants = read_constants(_download(url).decode("utf-8"))
    if constants["version"] != allowed[0]:
        raise RuntimeError(
            f"Release {tag} is faulty: its code calls itself {constants['version']}."
        )
    return dict(constants, tag=tag)


def fetch_gui_source(directory, tag, log=print):
    """Return a directory that contains the ``pysme_gui`` package of ``tag``."""
    local = os.environ.get(SOURCE_ENV)
    if local:
        log(f"Using GUI source from {local}")
        return local
    repo = update_repository()
    url = f"https://codeload.github.com/{repo}/zip/refs/tags/{tag}"
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
    raise RuntimeError(f"No pysme_gui package found in {repo}@{tag}.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Set up the PySME-GUI environment")
    commands = parser.add_subparsers(dest="command", required=True)
    install_parser = commands.add_parser("install")
    install_parser.add_argument("--source", required=True)
    install_parser.add_argument("--bundle-id")
    install_parser.add_argument("--repair", action="store_true")
    args = parser.parse_args(argv)

    def log(text):
        print(text, flush=True)

    try:
        setup(args.source, bundle_id=args.bundle_id, repair=args.repair, log=log)
    except Exception as exc:
        log(f"ERROR {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
