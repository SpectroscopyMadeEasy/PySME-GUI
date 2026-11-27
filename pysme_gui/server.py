"""Local HTTP server: serves the window contents and talks to the session.

It listens on 127.0.0.1 only. Because the API can read and write files, every
API request must carry the random token created at start-up, and the Host
header must name the loopback address (this blocks other web pages in a browser
from reaching it, including through DNS rebinding). On Linux, connections of
other users are refused altogether (see localpeer.py).
"""

import argparse
import json
import logging
import mimetypes
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import ISSUE_REPO, __version__, envmanager, instance, localpeer, report
from .envmanager import is_within
from .jobs import JobManager, LogBuffer
from .session import Session, UserError, available_options

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
THEMES = ("auto", "light", "dark")
MAX_BODY = 5_000_000
# A connection that sends nothing for this long is closed (seconds).
IDLE_LIMIT = 120
# Stated here: Windows takes these from its registry, where other programs may
# have changed them, and a browser then refuses to run the page's script.
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


class _BufferHandler(logging.Handler):
    def __init__(self, buffer):
        super().__init__(logging.INFO)
        self.buffer = buffer
        self.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))

    def emit(self, record):
        self.buffer.add(self.format(record))


def pysme_version():
    import pysme

    return pysme.__version__


def smelib_version():
    try:
        from pysme.sme_synth import SME_DLL

        return str(SME_DLL().SMELibraryVersion())
    except Exception as exc:
        return f"not loadable: {exc}"


def is_managed():
    """True when this interpreter belongs to one of the app's own environments."""
    return is_within(sys.prefix, os.path.join(envmanager.app_home(), "envs"))


def own_constants():
    return envmanager.package_constants(os.path.dirname(os.path.abspath(__file__)))


class Updater:
    """Updates of the interface, and the installed PySME versions.

    The two are independent: updating the interface never changes PySME, and
    installing a PySME version never switches to it.
    """

    def __init__(self, log):
        self.log = log
        self.state = {"state": "idle"}
        self._found = None              # what the last check found
        self._tasks = 0
        self._lock = threading.Lock()

    @staticmethod
    def constants():
        """Constants of the interface that will run after a restart."""
        if is_managed():
            chosen = envmanager.gui_info(envmanager.selection()[0])
            if chosen:
                return chosen
        return own_constants()

    def check(self):
        """Ask what has been published, and judge it."""
        found = {"checked": time.strftime("%H:%M"), "errors": [], "published": None,
                 "releases": None, "gui_note": None}
        try:
            found["published"] = envmanager.latest_gui()
        except envmanager.NoRelease as exc:
            found["gui_note"] = str(exc)
        except Exception as exc:
            found["errors"].append(f"Interface: {exc}")
        try:
            found["releases"] = envmanager.available_pysme()
        except Exception as exc:
            found["errors"].append(f"PySME: {exc}")
        self._found = found
        return self.latest

    @property
    def latest(self):
        """What the last check found, as it stands against what is chosen now,
        or None before a check. Judged anew each time: after an update, a switch
        or a way back, the same releases mean something else."""
        found = self._found
        if found is None:
            return None
        newer = envmanager.version_key
        constants = self.constants()
        published = found["published"]
        result = {key: found[key] for key in ("checked", "errors", "gui_note")}
        result["gui"] = published["version"] if published else None
        result["releases"] = None if found["releases"] is None else [
            r for r in found["releases"] if envmanager.supports(constants, r["version"])
        ]
        result["gui_newer"] = bool(result["gui"]) and newer(result["gui"]) > newer(constants["version"])
        result["gui_blocked"] = None
        if result["gui_newer"] and is_managed():
            env = envmanager.env_info(envmanager.selection()[1])
            if env and not envmanager.supports(published, env["pysme_version"]):
                result["gui_blocked"] = (
                    f"The new interface needs PySME {envmanager.supported_text(published)}. "
                    "Install and switch to such a version before updating."
                )
        # Only tell about a release that is newer than everything installed, so
        # that staying on an older version on purpose is not nagged about.
        have = [i["pysme_version"] for i in envmanager.installed_envs()] if is_managed() else []
        have.append(pysme_version())
        stable = [r["version"] for r in result["releases"] or [] if not r["prerelease"]]
        newest = stable[0] if stable else None
        result["pysme_new"] = (
            newest if newest and newer(newest) > max(map(newer, have)) else None
        )
        return result

    def _environments(self, constants):
        selected = envmanager.selection()[1]
        return [
            dict(
                info,
                selected=info["id"] == selected,
                running=is_within(sys.prefix, envmanager.env_dir(info["id"])),
                supported=envmanager.supports(constants, info["pysme_version"]),
            )
            for info in envmanager.installed_envs()
        ]

    @staticmethod
    def restart_needed():
        """True when what is chosen now is not what is running."""
        custom = envmanager.settings().get("python")
        if custom and not same_program(sys.executable, custom):
            return True
        gui_id, env_id = envmanager.selection()
        if not custom:
            if not is_managed():
                # The user has just gone back to the app's own PySME.
                return bool(envmanager.env_info(env_id))
            if gui_id and envmanager.env_info(env_id) \
                    and not is_within(sys.prefix, envmanager.env_dir(env_id)):
                return True
        # Which interface runs matters with either Python. Code run from a
        # checkout, as in development, is not an installed copy to be replaced.
        code = os.path.abspath(__file__)
        if not gui_id or not is_within(code, os.path.join(envmanager.app_home(), "gui")):
            return False
        return not is_within(code, envmanager.gui_dir(gui_id))

    def seen(self, task_id):
        """The user has looked at the result of this installation or update.

        Named, because another window may report an older result after a newer
        one has arrived.
        """
        state = self.state
        if state.get("state") in ("done", "failed") and state.get("id") == task_id:
            state["seen"] = True

    def info(self):
        managed = is_managed()
        constants = self.constants()
        record = envmanager.current_record() if managed else {}
        environments = self._environments(constants) if managed else []
        previous = envmanager.gui_info(record.get("previous_gui"))
        # The Mac app's own "go back" leaves the newer one only in "previous".
        swapped = envmanager.gui_info((record.get("previous") or {}).get("gui"))
        chosen = envmanager.version_key(constants["version"])
        older = previous and envmanager.version_key(previous["version"]) < chosen
        left = [g["version"] for g in (previous, swapped)
                if g and envmanager.version_key(g["version"]) > chosen]
        custom = envmanager.settings().get("python")
        custom_pysme = envmanager.settings().get("python_pysme")
        return {
            "gui": __version__,
            "gui_selected": constants["version"],
            "pysme": pysme_version(),
            "smelib": smelib_version(),
            "python": sys.executable,
            "python_version": sys.version.split()[0],
            "managed": managed,
            "custom_python": custom,
            "custom_pysme": custom_pysme,
            "custom_supported": (
                envmanager.supports(constants, custom_pysme) if custom_pysme else None
            ),
            "home": envmanager.app_home(),
            "source": os.environ.get(envmanager.SOURCE_ENV)
            or f"release tags of {envmanager.update_repository()}",
            "channel": envmanager.channel(),
            # Offered only as a way back: a newer one comes through the update.
            "previous_gui": previous["version"] if older else None,
            # The newer release the user has gone back from.
            "left_gui": max(left, key=envmanager.version_key) if left else None,
            "environments": environments,
            "recommended": constants["recommended"],
            "supported": envmanager.supported_text(constants),
            "minimum": constants["minimum"],
            "restart": self.restart_needed(),
            "notice": record.get("notice"),
            "latest": self.latest,
            "task": self.state,
        }

    @staticmethod
    def _require_managed():
        if not is_managed():
            raise UserError(
                "This PySME comes from your own Python environment. Manage versions there "
                "(for example with pip)."
            )

    def _require_idle(self):
        if self.state.get("state") == "running":
            raise UserError("Wait for the running installation or update to finish.")

    def _start(self, title, action, done, restarts=False):
        """Run ``action`` in the background. ``restarts`` says that its result
        takes effect only after a restart."""
        self._require_managed()
        with self._lock:
            self._require_idle()
            self._tasks += 1
            number = self._tasks
            self.state = {"state": "running", "id": number, "title": title, "step": "Starting"}

        def work():
            def log(text):
                self.log.add(f"[update] {text}")
                if text.startswith("STEP "):
                    self.state["step"] = text[5:]

            try:
                action(log)
                self.state = {"state": "done", "id": number, "message": done,
                              "restarts": restarts, "seen": False}
            except Exception as exc:
                log(f"failed: {exc}")
                self.state = {"state": "failed", "id": number, "title": title,
                              "error": str(exc), "seen": False}

        threading.Thread(target=work, daemon=True).start()

    def update_gui(self):
        self._require_managed()
        latest = self.latest or self.check()
        if not latest["gui_newer"]:
            raise UserError("There is no newer interface.")
        if latest["gui_blocked"]:
            raise UserError(latest["gui_blocked"])

        self._start("Updating the interface", lambda log: envmanager.update_gui(log=log),
                    "Interface updated. PySME is unchanged.", restarts=True)

    def set_channel(self, name):
        with self._lock:
            self._require_idle()
            try:
                envmanager.set_channel(name)
            except RuntimeError as exc:
                raise UserError(str(exc))
        # The earlier answer was for another channel.
        self.check()

    def rollback_gui(self):
        self._start("Going back to the earlier interface",
                    lambda log: envmanager.rollback_gui(log=log),
                    "Went back to the earlier interface. PySME is unchanged.", restarts=True)

    def install_pysme(self, version):
        version = str(version or "").strip()
        if not re.fullmatch(r"[0-9][0-9A-Za-z.]*", version):
            raise UserError("Choose a version to install.")
        self._start(f"Installing PySME {version}",
                    lambda log: envmanager.install_pysme(version, log=log),
                    f"PySME {version} is installed. Press “Use” to switch to it.")

    def use(self, env_id):
        self._start("Switching PySME",
                    lambda log: envmanager.use_env(str(env_id), log=log),
                    "PySME switched.", restarts=True)

    def _known(self, env_id):
        if not any(i["id"] == env_id for i in envmanager.installed_envs()):
            raise UserError("That PySME installation no longer exists.")
        return env_id

    def remove(self, env_id):
        self._require_managed()
        with self._lock:
            self._require_idle()
            try:
                envmanager.remove_env(self._known(env_id), log=self.log.add)
            except (OSError, RuntimeError) as exc:
                raise UserError(str(exc))
            self.state = {"state": "idle"}

    def export(self, env_id, path):
        self._require_managed()
        if not path:
            raise UserError("Choose a file name.")
        try:
            envmanager.export_lock(self._known(env_id), path)
        except (OSError, RuntimeError) as exc:
            raise UserError(str(exc))


def same_program(first, second):
    """True if two paths name the same Python.

    The folders may be reached through links (macOS rewrites /private/tmp as
    /tmp). The last link is not followed: the "python" of two environments often
    points at one base interpreter.
    """
    def where(path):
        folder, name = os.path.split(os.path.abspath(path))
        return os.path.normcase(os.path.join(os.path.realpath(folder), name))

    return where(first) == where(second)


def _next_interface():
    """The folder holding the interface code that will run after a restart."""
    if is_managed():
        gui_id = envmanager.selection()[0]
        if envmanager.gui_info(gui_id):
            return envmanager.gui_dir(gui_id)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def set_custom_python(path, accept_unsupported=False):
    """Use a Python with the user's own PySME from the next start on.

    It is only stored if the interface starts with it. A PySME outside the
    supported range (a development build can be) is stored only when the user
    confirms. Returns what was found, or None when the setting was cleared.
    """
    path = (path or "").strip()
    if len(path) > 1 and path[0] == path[-1] and path[0] in "'\"":
        path = path[1:-1].strip()
    if not path:
        envmanager.change_settings(remove=("python", "python_pysme"))
        return None
    if os.path.dirname(path) or path.startswith("~"):
        path = os.path.abspath(os.path.expanduser(path))
    windows = sys.platform == "win32"
    if os.path.isdir(path):
        places = [envmanager.venv_python(path)]
        if windows:
            places.append(os.path.join(path, "python.exe"))       # a conda environment
        inside = next((p for p in places if os.path.isfile(p)), None)
        hint = f" Did you mean {inside}?" if inside else ""
        raise UserError(f"That is a folder; enter the Python program inside it.{hint}")
    # Windows calls every file executable, so there the name has to tell.
    runnable = path.lower().endswith(".exe") if windows else os.access(path, os.X_OK)
    if not os.path.isfile(path) or not runnable:
        raise UserError(f"Not an executable Python: {path}. Enter its full path.")

    interface = _next_interface()
    env = envmanager.uv_environment()                 # without the caller's Python settings
    env.update(PYTHONPATH=interface, PYTHONDONTWRITEBYTECODE="1", MPLBACKEND="Agg")
    try:
        probe = subprocess.run(
            [path, "-c", envmanager.SMOKE_TEST], capture_output=True, text=True,
            errors="replace", timeout=120, cwd=os.path.expanduser("~"), env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UserError(f"Could not run it: {exc}")
    found = [l for l in probe.stdout.splitlines() if l.startswith("SMOKE ")]
    if probe.returncode != 0 or not found:
        last = (probe.stderr.strip().splitlines() or ["unknown error"])[-1]
        raise UserError(f"PySME-GUI cannot start with that Python: {last}")
    versions = json.loads(found[-1][6:])

    constants = envmanager.package_constants(os.path.join(interface, "pysme_gui"))
    result = {
        "pysme": versions["pysme"], "smelib": versions["smelib"], "python": path,
        "supported": envmanager.supports(constants, versions["pysme"]),
        "range": envmanager.supported_text(constants),
        "saved": False,
    }
    if result["supported"] or accept_unsupported:
        # The check took a while; other settings may have changed meanwhile.
        envmanager.change_settings(python=path, python_pysme=versions["pysme"])
        result["saved"] = True
    return result


def set_theme(theme):
    if theme not in THEMES:
        raise UserError("Unknown appearance.")
    envmanager.change_settings(theme=theme)


def index_page():
    with open(os.path.join(STATIC, "index.html"), "rb") as handle:
        page = handle.read()
    theme = envmanager.settings().get("theme")
    if theme in ("light", "dark"):
        page = page.replace(b'<html lang="en">', f'<html lang="en" data-theme="{theme}">'.encode(), 1)
    return page


def drives():
    if hasattr(os, "listdrives"):          # Python 3.12 and newer
        return os.listdrives()
    letters = (f"{chr(c)}:\\" for c in range(ord("A"), ord("Z") + 1))
    return [d for d in letters if os.path.isdir(d)]


def list_directory(path):
    path = os.path.abspath(os.path.expanduser(path or "~"))
    if not os.path.isdir(path):
        path = os.path.expanduser("~")
    entries = []
    try:
        with os.scandir(path) as scan:
            for entry in scan:
                if entry.name.startswith("."):
                    continue
                try:
                    entries.append({"name": entry.name, "dir": entry.is_dir()})
                except OSError:
                    continue
    except PermissionError:
        raise UserError(f"No permission to read {path}")
    entries.sort(key=lambda e: (not e["dir"], e["name"].lower()))
    result = {"dir": path, "parent": os.path.dirname(path), "entries": entries[:3000]}
    if sys.platform == "win32":
        # ".." ends at the top of a drive, so the others are offered beside it.
        result["drives"] = drives()
    return result


class App:
    def __init__(self):
        self.log = LogBuffer()
        logging.getLogger("pysme").addHandler(_BufferHandler(self.log))
        self.session = Session()
        self.lock = threading.RLock()
        self.jobs = JobManager(self.session, self.lock, self.log)
        self.updater = Updater(self.log)
        self.token = secrets.token_urlsafe(24)
        self.on_quit = None             # set where the page may end the program
        self._options = None
        self.report_path = None

    def report_preview(self, body):
        smelib = smelib_version()
        with self.lock:
            text = report.build(
                str(body.get("description") or "")[:20_000], self.session, self.jobs.status(),
                self.log.tail(report.LOG_LINES), smelib, is_managed(), self.updater.state,
                client=body.get("client"), include_settings=bool(body.get("settings")),
            )
        return {"text": text}

    def report_save(self, body):
        text = str(body.get("text") or "")
        if not text.strip():
            raise UserError("The report is empty.")
        try:
            self.report_path = report.save(text)
        except OSError as exc:
            raise UserError(str(exc))
        name = os.path.basename(self.report_path)
        subject = f"PySME-GUI problem report (GUI {__version__}, {report.system_name()})"
        return {
            "path": self.report_path,
            "issue_url": report.issue_url(ISSUE_REPO, subject, name, smelib_version()),
        }

    def state(self):
        with self.lock:
            state = self.session.state()
        state["job"] = self.jobs.status()
        state["log_seq"] = self.log.seq
        state["update"] = {"task": self.updater.state, "latest": self.updater.latest,
                           "restart": self.updater.restart_needed()}
        state["release"] = {"version": __version__, "stage": envmanager.stage(__version__)}
        state["can_quit"] = self.on_quit is not None
        return state

    # ------------------------------------------------------------------ routes

    def get(self, route, query):
        def arg(name, default=""):
            return query.get(name, [default])[0]

        if route == "state":
            return self.state()
        if route == "log":
            return self.log.since(int(arg("since", "0")))
        if route == "options":
            if self._options is None:
                self._options = available_options()
            return self._options
        if route == "fs":
            return list_directory(arg("dir"))
        if route == "settings":
            return self.updater.info()
        with self.lock:
            session = self.session
            if route == "spectrum":
                return session.spectrum()
            if route == "run":
                return session.run_spectrum(int(arg("id", "0")))
            if route == "abund":
                return session.abundance_table()
            if route == "linelist":
                return session.linelist_rows(
                    max(int(arg("offset", "0")), 0),
                    min(max(int(arg("limit", "100")), 1), 500), arg("q"),
                )
            if route == "linelist/line":
                return session.line(arg("id"))
            if route == "linelist/detail":
                return session.line_detail(arg("id"))
            if route == "lines":
                return session.strong_lines(
                    float(arg("wmin")), float(arg("wmax")), min(int(arg("limit", "30")), 200)
                )
            if route == "citation":
                return session.citations(arg("format"))
        raise KeyError(route)

    def post(self, route, body):
        if route == "quit":
            if self.on_quit is None:
                raise UserError("This window is quit from its own menu.")
            threading.Timer(0.3, self.on_quit).start()      # after this answer
            return {"quitting": True}
        if route == "cancel":
            self.jobs.cancel()
            return self.state()
        if route == "update/seen":
            self.updater.seen(body.get("id"))
            return self.state()
        if route == "update/check":
            self.updater.check()
            return self.updater.info()
        if route == "update/channel":
            self.updater.set_channel(body.get("channel"))
            return self.updater.info()
        if route == "update/gui":
            self.updater.update_gui()
            return self.updater.info()
        if route == "pysme/install":
            self.updater.install_pysme(body.get("version"))
            return self.updater.info()
        if route == "pysme/use":
            self.updater.use(body.get("id"))
            return self.updater.info()
        if route == "pysme/remove":
            self.updater.remove(body.get("id"))
            return self.updater.info()
        if route == "pysme/export":
            self.updater.export(body.get("id"), body.get("path"))
            return self.updater.info()
        if route == "report/preview":
            return self.report_preview(body)
        if route == "report/save":
            return self.report_save(body)
        if route == "report/reveal":
            if not self.report_path:
                raise UserError("No report has been saved yet.")
            report.reveal(self.report_path)
            return {}
        if route == "update/rollback":
            self.updater.rollback_gui()
            return self.updater.info()
        if route == "settings/theme":
            set_theme(body.get("theme"))
            return {}
        if route == "settings/python":
            checked = set_custom_python(body.get("python"), bool(body.get("accept_unsupported")))
            result = self.updater.info()
            result["checked"] = checked
            return result

        extra = {}
        with self.lock:
            if self.jobs.running:
                raise UserError("Wait for the current run to finish, or press Stop.")
            session = self.session
            if route == "new":
                session.reset()
            elif route == "open":
                session.open_path(body["path"])
            elif route == "linelist/load":
                session.load_linelist(body["path"])
            elif route == "linelist/edit":
                session.set_line(body["id"], body["column"], body.get("value"))
            elif route == "linelist/reset":
                session.reset_lines(body.get("id"))
            elif route == "example":
                session.load_example()
            elif route == "save":
                extra["saved"] = session.save(body.get("path"))
            elif route == "export/csv":
                extra["saved"] = session.export_csv(body["path"])
            elif route == "export/citation":
                path = os.path.expanduser(body["path"])
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(session.citations(body.get("format"))["text"])
                extra["saved"] = path
            elif route == "params":
                session.set_params(body["values"])
            elif route == "synthsetup":
                session.set_synth_setup(body.get("ranges") or [], body.get("step"))
            elif route == "windows":
                session.set_windows(body.get("windows"))
            elif route == "observation/remove":
                session.remove_observation()
            elif route == "mask":
                extra["pixels"] = session.set_mask(
                    body["start"], body["end"], body["value"], bool(body.get("remove"))
                )
            elif route == "abund/set":
                session.set_abundance(body["element"], body.get("value"), body.get("scale", "A"))
            elif route == "abund/reference":
                session.set_solar_reference(body["name"])
            elif route == "abund/pattern":
                session.apply_abund_pattern(body["name"])
            elif route == "nlte":
                session.set_nlte(body["element"], bool(body["enabled"]), body.get("grid"))
            elif route == "nlte/off":
                session.nlte_off()
            elif route == "run":
                if body.get("kind") not in ("synth", "fit"):
                    raise UserError("Unknown run type.")
                self.jobs.start(body["kind"])
            elif route == "revert_fit":
                session.revert_fit()
            else:
                raise KeyError(route)
        result = self.state()
        result.update(extra)
        return result


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "PySME-GUI"
        protocol_version = "HTTP/1.1"
        timeout = IDLE_LIMIT

        def log_message(self, format, *args):
            pass

        def _send(self, status, content, content_type, cache=False):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "max-age=86400" if cache else "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(content)

        def _json(self, data, status=200):
            self._send(status, json.dumps(data, default=str).encode(), "application/json")

        def _host_ok(self):
            port = self.server.server_address[1]
            return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

        def _authorised(self):
            given = self.headers.get("X-PySME-Token", "")
            return secrets.compare_digest(given.encode(), app.token.encode())

        def do_GET(self):
            if not self._host_ok():
                return self._json({"error": "Bad host"}, 403)
            url = urlparse(self.path)
            if url.path.startswith("/api/"):
                return self._api(lambda: app.get(url.path[5:], parse_qs(url.query)))
            if url.path == "/vendor/plotly.min.js":
                # The interface brings its own copy; PySME need not have Plotly.
                return self._file(os.path.join(STATIC, "vendor", "plotly.min.js"), cache=True)
            if url.path in ("/", "/index.html"):
                return self._send(200, index_page(), "text/html; charset=utf-8")
            name = url.path.lstrip("/")
            path = os.path.realpath(os.path.join(STATIC, name))
            if not path.startswith(os.path.realpath(STATIC) + os.sep):
                return self._json({"error": "Not found"}, 404)
            return self._file(path)

        def _file(self, path, cache=False):
            try:
                with open(path, "rb") as handle:
                    content = handle.read()
            except OSError:
                return self._json({"error": "Not found"}, 404)
            kind = (CONTENT_TYPES.get(os.path.splitext(path)[1].lower())
                    or mimetypes.guess_type(path)[0] or "application/octet-stream")
            self._send(200, content, kind, cache)

        def _refuse(self, message, status):
            """Answer without reading what was sent, and hang up."""
            self.close_connection = True
            self._json({"error": message}, status)

        def do_POST(self):
            url = urlparse(self.path)
            if not self._host_ok() or not url.path.startswith("/api/"):
                return self._refuse("Not found", 404)
            # Before the body is read: someone without the token must not be
            # able to keep the program waiting for it.
            if not self._authorised():
                return self._refuse("Missing or wrong access token", 403)
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0:
                return self._refuse("Bad request", 400)
            if length > MAX_BODY:
                return self._refuse("Request too large", 413)
            raw = self.rfile.read(length)

            def action():
                body = json.loads(raw or b"{}")
                return app.post(url.path[5:], body)

            return self._api(action)

        def _api(self, action):
            if not self._authorised():
                return self._json({"error": "Missing or wrong access token"}, 403)
            try:
                return self._json(action())
            except UserError as exc:
                return self._json({"error": str(exc)}, 400)
            except Exception as exc:
                app.log.add(traceback.format_exc())
                return self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    return Handler


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    check_user = False              # set where the system names a connection's user

    def verify_request(self, request, client_address):
        # Called before the connection gets a thread or is read from.
        if not self.check_user:
            return True
        return localpeer.is_this_user(client_address[:2], request.getsockname()[:2])


def create_server(port=0):
    app = App()
    server = LocalServer(("127.0.0.1", port), make_handler(app))
    server.check_user = localpeer.can_tell()
    if sys.platform.startswith("linux") and not server.check_user:
        app.log.add("This system does not say which user a local connection comes from; "
                    "only the access token protects the interface.")
    url = f"http://127.0.0.1:{server.server_address[1]}/?token={app.token}"
    return app, server, url


def announce(url, wanted):
    """Print the address if someone is there to read it.

    The address holds the access token. Started from the menu, what is printed
    goes into a log file, which must not hold it: others on a shared computer
    might read it, and the file may be sent with a problem report.
    """
    if wanted or (sys.stdout is not None and sys.stdout.isatty()):
        print(f"PYSME_GUI_URL {url}", flush=True)


def show(url):
    """Open the interface in the web browser."""
    if not webbrowser.open(url):
        print("No web browser could be opened. Run pysme-gui --no-browser in a "
              "terminal to get the address.", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pysme-gui", description="PySME graphical interface")
    parser.add_argument("file", nargs="?", help=".sme file, line list or spectrum to open")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true", help="only print the address")
    parser.add_argument("--app-mode", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--parent-pid", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    # Opened again from the menu: show what runs. A file given to open gets an
    # interface of its own, so that it never replaces unsaved work.
    browser_version = not args.app_mode
    if browser_version and not args.file and not args.port:
        url = instance.running()
        if url:
            announce(url, args.no_browser)
            print("PySME is already running; this shows it again.", flush=True)
            if not args.no_browser:
                show(url)
            return

    if envmanager.is_within(os.path.dirname(os.path.abspath(__file__)), envmanager.app_home()):
        try:
            envmanager.keep_private()           # also for installations made earlier
        except (OSError, RuntimeError) as exc:
            print(f"WARNING {exc}", flush=True)
    try:
        envmanager.hold_running_env()       # so that no other window removes it
    except OSError as exc:
        print(f"WARNING could not mark this PySME as in use: {exc}", flush=True)
    app, server, url = create_server(args.port)
    # --no-browser asks for the address; the Mac app reads it and hides it. It
    # takes the first one it sees, so this comes before any file is opened:
    # opening one prints its name, which could be made to look like an address.
    announce(url, args.no_browser or args.app_mode)
    if args.file:
        try:
            app.session.open_path(args.file)
        except Exception as exc:
            app.session.notes = [f"Could not open {args.file}: {exc}"]

    def stop(*_):
        app.jobs.shutdown()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    if args.parent_pid:
        def watch_parent():
            while os.getppid() == args.parent_pid:
                time.sleep(1)
            stop()

        threading.Thread(target=watch_parent, daemon=True).start()

    if browser_version:
        app.on_quit = stop              # the Mac app quits by its own menu
        instance.register(url)
        print("To quit: ⋯ → Quit PySME in the window, or Ctrl-C here.", flush=True)
        if not args.no_browser:
            show(url)
    try:
        server.serve_forever()
    finally:
        app.jobs.shutdown()
        if browser_version:
            instance.unregister(url)
        server.server_close()


if __name__ == "__main__":
    main()
