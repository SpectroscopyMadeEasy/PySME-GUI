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

from . import __version__, envmanager
from .jobs import JobManager, LogBuffer
from .session import Session, UserError, available_options

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
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


def is_managed():
    """True when this interpreter belongs to one of the app's own slots."""
    slots = os.path.realpath(os.path.join(envmanager.app_home(), "slots"))
    return os.path.realpath(sys.prefix).startswith(slots + os.sep)


class Updater:
    def __init__(self, log):
        self.log = log
        self.state = {"state": "idle"}
        self.latest = None
        self._lock = threading.Lock()

    def check(self):
        result = {"checked": time.strftime("%H:%M"), "errors": []}
        for key, getter in (
            ("pysme", envmanager.latest_pysme_version),
            ("gui", envmanager.latest_gui_version),
        ):
            try:
                result[key] = getter()
            except Exception as exc:
                result[key] = None
                result["errors"].append(f"{'PySME' if key == 'pysme' else 'GUI'}: {exc}")
        newer = envmanager.version_key
        result["pysme_newer"] = bool(result["pysme"]) and newer(result["pysme"]) > newer(pysme_version())
        result["gui_newer"] = bool(result["gui"]) and newer(result["gui"]) > newer(__version__)
        self.latest = result
        return result

    def info(self):
        record = envmanager.current_record()
        repo, ref = envmanager.update_target()
        return {
            "gui": __version__,
            "pysme": pysme_version(),
            "smelib": smelib_version(),
            "python": sys.executable,
            "python_version": sys.version.split()[0],
            "managed": is_managed(),
            "custom_python": envmanager.settings().get("python"),
            "home": envmanager.app_home(),
            "source": os.environ.get(envmanager.SOURCE_ENV) or f"{repo} @ {ref}",
            "previous": envmanager.slot_info(record.get("previous")),
            "latest": self.latest,
            "task": self.state,
        }

    def _start(self, action):
        with self._lock:
            if self.state.get("state") == "running":
                raise UserError("An update is already running.")
            self.state = {"state": "running", "step": "Starting"}

        def work():
            def log(text):
                self.log.add(f"[update] {text}")
                if text.startswith("STEP "):
                    self.state["step"] = text[5:]

            try:
                action(log)
                self.state = {"state": "restart"}
            except Exception as exc:
                log(f"failed: {exc}")
                self.state = {"state": "failed", "error": str(exc)}

        threading.Thread(target=work, daemon=True).start()

    def apply(self, update_pysme, update_gui):
        if not is_managed():
            raise UserError(
                "This PySME comes from your own Python environment. Update it there "
                "(for example with pip)."
            )
        latest = self.latest or self.check()
        version = latest["pysme"] if update_pysme and latest["pysme_newer"] else None
        gui = bool(update_gui and latest["gui_newer"])
        if not version and not gui:
            raise UserError("Nothing to update.")
        self._start(lambda log: envmanager.update(version, gui, log=log))

    def rollback(self):
        if not is_managed():
            raise UserError("Only the app's own environment can be rolled back.")
        self._start(lambda log: log(f"Now using {envmanager.rollback()}"))


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
        if route == "update/apply":
            self.updater.apply(body.get("pysme"), body.get("gui"))
            return self.updater.info()
        if route == "update/rollback":
            self.updater.rollback()
            return self.updater.info()
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
            name = "index.html" if url.path == "/" else url.path.lstrip("/")
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
