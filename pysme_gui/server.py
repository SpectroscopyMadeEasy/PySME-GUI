"""Local HTTP server: serves the window contents and talks to the session.

It listens on 127.0.0.1 only. Because the API can read and write files, every
API request must carry the random token created at start-up, and the Host
header must name the loopback address (this blocks other web pages in a browser
from reaching it, including through DNS rebinding).
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

from . import FEEDBACK_EMAIL, ISSUE_REPO, __version__, envmanager, report
from .jobs import JobManager, LogBuffer
from .session import Session, UserError, available_options

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
THEMES = ("auto", "light", "dark")
MAX_BODY = 5_000_000


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
        from pysme.smelib import _smelib

        return str(_smelib.LibraryVersion())
    except Exception as exc:
        return f"not loadable: {exc}"


def _inside(path, directory):
    return os.path.realpath(path).startswith(os.path.realpath(directory) + os.sep)


def is_managed():
    """True when this interpreter belongs to one of the app's own environments."""
    return _inside(sys.prefix, os.path.join(envmanager.app_home(), "envs"))


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
        self.latest = None
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
        result = {"checked": time.strftime("%H:%M"), "errors": [], "gui": None, "releases": None,
                  "gui_note": None}
        newer = envmanager.version_key
        constants = self.constants()
        try:
            published = envmanager.latest_gui()
            result["gui"] = published["version"]
        except envmanager.NoRelease as exc:
            published = None
            result["gui_note"] = str(exc)
        except Exception as exc:
            published = None
            result["errors"].append(f"Interface: {exc}")
        try:
            result["releases"] = [
                r for r in envmanager.available_pysme()
                if envmanager.supports(constants, r["version"])
            ]
        except Exception as exc:
            result["errors"].append(f"PySME: {exc}")
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
        self.latest = result
        return result

    def _environments(self, constants):
        selected = envmanager.selection()[1]
        return [
            dict(
                info,
                selected=info["id"] == selected,
                running=_inside(sys.prefix, envmanager.env_dir(info["id"])),
                supported=envmanager.supports(constants, info["pysme_version"]),
            )
            for info in envmanager.installed_envs()
        ]

    def info(self):
        managed = is_managed()
        constants = self.constants()
        record = envmanager.current_record() if managed else {}
        environments = self._environments(constants) if managed else []
        previous = envmanager.gui_info(record.get("previous_gui"))
        gui_id = envmanager.selection(record)[0]
        restart = managed and bool(gui_id) and (
            any(e["selected"] and not e["running"] for e in environments)
            or not _inside(os.path.abspath(__file__), envmanager.gui_dir(gui_id))
        )
        return {
            "gui": __version__,
            "gui_selected": constants["version"],
            "pysme": pysme_version(),
            "smelib": smelib_version(),
            "python": sys.executable,
            "python_version": sys.version.split()[0],
            "managed": managed,
            "custom_python": envmanager.settings().get("python"),
            "home": envmanager.app_home(),
            "source": os.environ.get(envmanager.SOURCE_ENV)
            or f"release tags of {envmanager.update_repository()}",
            "channel": envmanager.channel(),
            # A reinstall of the same release is not worth offering to go back to.
            "previous_gui": (
                previous["version"]
                if previous and previous["version"] != constants["version"] else None
            ),
            "environments": environments,
            "recommended": constants["recommended"],
            "supported": envmanager.supported_text(constants),
            "restart": restart,
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

    def _start(self, title, action, done):
        self._require_managed()
        with self._lock:
            self._require_idle()
            self.state = {"state": "running", "title": title, "step": "Starting"}

        def work():
            def log(text):
                self.log.add(f"[update] {text}")
                if text.startswith("STEP "):
                    self.state["step"] = text[5:]

            try:
                action(log)
                self.state = {"state": "done", "message": done}
            except Exception as exc:
                log(f"failed: {exc}")
                self.state = {"state": "failed", "title": title, "error": str(exc)}
            if self.latest:
                have = [i["pysme_version"] for i in envmanager.installed_envs()]
                if self.latest.get("pysme_new") in have:
                    self.latest["pysme_new"] = None

        threading.Thread(target=work, daemon=True).start()

    def update_gui(self):
        self._require_managed()
        latest = self.latest or self.check()
        if not latest["gui_newer"]:
            raise UserError("There is no newer interface.")
        if latest["gui_blocked"]:
            raise UserError(latest["gui_blocked"])

        def action(log):
            envmanager.update_gui(log=log)
            # It is installed now; do not offer it again.
            self.latest["gui_newer"] = False

        self._start("Updating the interface", action,
                    "Interface updated. PySME is unchanged.")

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
        self._start("Going back to the previous interface",
                    lambda log: envmanager.rollback_gui(log=log),
                    "Went back to the previous interface. PySME is unchanged.")

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
                    "Switched. It takes effect after a restart.")

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


def set_custom_python(path):
    """Use another interpreter (with its own PySME) from the next start on."""
    chosen = envmanager.settings()
    if not path:
        chosen.pop("python", None)
        envmanager.write_settings(chosen)
        return None
    path = os.path.expanduser(path)
    if not os.path.isfile(path) or not os.access(path, os.X_OK):
        raise UserError(f"Not an executable Python: {path}")
    try:
        probe = subprocess.run(
            [path, "-c", "from pysme.smelib import _smelib; import pysme; "
             "_smelib.LibraryVersion(); print('VERSION', pysme.__version__)"],
            capture_output=True, text=True, timeout=90, cwd=os.path.expanduser("~"),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UserError(f"Could not run it: {exc}")
    if probe.returncode != 0:
        last = (probe.stderr.strip().splitlines() or ["unknown error"])[-1]
        raise UserError(f"That Python cannot run PySME: {last}")
    version = [l for l in probe.stdout.splitlines() if l.startswith("VERSION ")][-1][8:]
    chosen["python"] = path
    envmanager.write_settings(chosen)
    return version


def set_theme(theme):
    if theme not in THEMES:
        raise UserError("Unknown appearance.")
    envmanager.write_settings(dict(envmanager.settings(), theme=theme))


def index_page():
    with open(os.path.join(STATIC, "index.html"), "rb") as handle:
        page = handle.read()
    theme = envmanager.settings().get("theme")
    if theme in ("light", "dark"):
        page = page.replace(b'<html lang="en">', f'<html lang="en" data-theme="{theme}">'.encode(), 1)
    return page


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
    return {"dir": path, "parent": os.path.dirname(path), "entries": entries[:3000]}


class App:
    def __init__(self):
        self.log = LogBuffer()
        logging.getLogger("pysme").addHandler(_BufferHandler(self.log))
        self.session = Session()
        self.lock = threading.RLock()
        self.jobs = JobManager(self.session, self.lock, self.log)
        self.updater = Updater(self.log)
        self.token = secrets.token_urlsafe(24)
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
            "email": FEEDBACK_EMAIL,
            "subject": subject,
            "issue_url": report.issue_url(ISSUE_REPO, subject, name, smelib_version()),
            "body": f"My problem report is in the file {name}.\n"
                    "(If it is not attached yet, please attach it before sending.)\n",
        }

    def state(self):
        with self.lock:
            state = self.session.state()
        state["job"] = self.jobs.status()
        state["log_seq"] = self.log.seq
        state["update"] = {"task": self.updater.state, "latest": self.updater.latest}
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
            if route == "lines":
                return session.strong_lines(
                    float(arg("wmin")), float(arg("wmax")), min(int(arg("limit", "30")), 200)
                )
            if route == "citation":
                return {"text": session.citation(arg("format"))}
        raise KeyError(route)

    def post(self, route, body):
        if route == "cancel":
            self.jobs.cancel()
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
            version = set_custom_python(body.get("python"))
            result = self.updater.info()
            result["custom_version"] = version
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
            elif route == "example":
                session.load_example()
            elif route == "save":
                extra["saved"] = session.save(body.get("path"))
            elif route == "export/csv":
                extra["saved"] = session.export_csv(body["path"])
            elif route == "export/citation":
                path = os.path.expanduser(body["path"])
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(session.citation(body.get("format")))
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
                extra["pixels"] = session.set_mask(body["start"], body["end"], body["value"])
            elif route == "abund/set":
                session.set_abundance(body["element"], body.get("value"))
            elif route == "abund/pattern":
                session.apply_abund_pattern(body["name"])
            elif route == "nlte":
                session.set_nlte(body["element"], bool(body["enabled"]), body.get("grid"))
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
                import plotly

                path = os.path.join(os.path.dirname(plotly.__file__), "package_data", "plotly.min.js")
                return self._file(path, cache=True)
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
            kind = mimetypes.guess_type(path)[0] or "application/octet-stream"
            self._send(200, content, kind, cache)

        def do_POST(self):
            # Always consume the body so the connection stays usable.
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self.close_connection = True
                return self._json({"error": "Request too large"}, 413)
            raw = self.rfile.read(length)
            url = urlparse(self.path)
            if not self._host_ok() or not url.path.startswith("/api/"):
                return self._json({"error": "Not found"}, 404)

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


def create_server(port=0):
    app = App()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_address[1]}/?token={app.token}"
    return app, server, url


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pysme-gui", description="PySME graphical interface")
    parser.add_argument("file", nargs="?", help=".sme file, line list or spectrum to open")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true", help="only print the address")
    parser.add_argument("--app-mode", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--parent-pid", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    app, server, url = create_server(args.port)
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

    print(f"PYSME_GUI_URL {url}", flush=True)
    if not args.app_mode:
        print("Press Ctrl-C to quit.", flush=True)
        if not args.no_browser:
            webbrowser.open(url)
    try:
        server.serve_forever()
    finally:
        app.jobs.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
