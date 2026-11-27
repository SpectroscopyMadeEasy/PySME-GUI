"""Start PySME-GUI on Windows.

The installer copies this file into the app's data folder, and the shortcuts run
it with pythonw.exe, which opens no window. It starts the interface and the
PySME chosen in current.json, so updating either needs no change here.

    launch.py [--console] [arguments for pysme_gui]

--console keeps what the program prints in the window it was started from.
Only the standard library is used.
"""

import json
import os
import subprocess
import sys

HOME = os.path.dirname(os.path.abspath(__file__))
LOG_LIMIT = 5_000_000


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def interface_command(home, arguments):
    """The command and environment that start the chosen installation."""
    chosen = _read_json(os.path.join(home, "current.json")).get("current") or {}
    gui_id, env_id = chosen.get("gui"), chosen.get("env")
    if not gui_id or not env_id:
        raise RuntimeError("PySME is not installed here. Please run “Install PySME” again.")
    python = _read_json(os.path.join(home, "settings.json")).get("python") or os.path.join(
        home, "envs", env_id, "venv", "Scripts", "python.exe"
    )
    if not os.path.isfile(python):
        raise RuntimeError(f"The Python that runs PySME was not found:\n{python}")
    env = dict(os.environ)
    for name in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME", "CONDA_PREFIX"):
        env.pop(name, None)
    env.update(
        PYSME_GUI_HOME=home,
        PYSME_GUI_UV=os.path.join(home, "bin", "uv.exe"),
        UV_PYTHON_INSTALL_DIR=os.path.join(home, "python"),
        UV_CACHE_DIR=os.path.join(home, "cache"),
        PYTHONPATH=os.path.join(home, "gui", gui_id),
        PYTHONUTF8="1",
    )
    return [python, "-m", "pysme_gui", *arguments], env


def show_error(text):
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, "PySME", 0x10)
    elif sys.stderr:
        print(text, file=sys.stderr)


def main(argv=None, home=HOME):
    arguments = list(sys.argv[1:] if argv is None else argv)
    console = "--console" in arguments
    if console:
        arguments.remove("--console")
    try:
        command, env = interface_command(home, arguments)
        if console:
            return subprocess.call(command, env=env)
        log_path = os.path.join(home, "pysme-gui.log")
        try:
            if os.path.getsize(log_path) > LOG_LIMIT:
                os.replace(log_path, log_path + ".old")
        except OSError:
            pass
        with open(log_path, "ab") as log:
            # A hidden console, which the programs it starts share; without one
            # each of them would flash a window of its own.
            code = subprocess.call(
                command, env=env, cwd=os.path.expanduser("~"),
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
    except (OSError, RuntimeError) as exc:
        show_error(f"PySME could not start.\n\n{exc}")
        return 1
    if code:
        show_error(
            f"PySME stopped with an error (code {code}).\n\n"
            f"What happened is recorded in\n{log_path}\n\n"
            "Please send that file with your report."
        )
    return code


if __name__ == "__main__":
    sys.exit(main())
