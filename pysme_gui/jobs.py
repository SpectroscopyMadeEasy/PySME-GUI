"""Start, watch and cancel the worker process."""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque

PACKAGE_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_PROGRESS = re.compile(r"Synthesizing (?:residual|jacobian) with values (.*)")


class LogBuffer:
    def __init__(self, size=4000):
        self.lines = deque(maxlen=size)
        self.seq = 0
        self.lock = threading.Lock()

    def add(self, text):
        text = _ANSI.sub("", text).rstrip()
        if not text:
            return
        with self.lock:
            self.seq += 1
            self.lines.append((self.seq, text))

    def since(self, seq):
        with self.lock:
            return {"seq": self.seq, "lines": [t for s, t in self.lines if s > seq]}

    def tail(self, count=30):
        with self.lock:
            return [t for _, t in list(self.lines)[-count:]]


def stop_process_tree(process, force=False):
    """Stop a run together with the helper processes PySME started.

    Windows cannot ask a program without a window to end, so there every stop
    is a forced one.
    """
    if sys.platform != "win32":
        try:
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        return
    system = os.environ.get("SystemRoot") or r"C:\Windows"
    try:
        subprocess.run(
            [os.path.join(system, "System32", "taskkill.exe"),
             "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        process.kill()


class JobManager:
    def __init__(self, session, session_lock, log):
        self.session = session
        self.session_lock = session_lock
        self.log = log
        self.lock = threading.Lock()
        self.process = None
        self.info = {"state": "idle"}
        self._cancelled = False
        self._counter = 0

    @property
    def running(self):
        return self.info.get("state") == "running"

    def status(self):
        info = dict(self.info)
        if info.get("state") == "running":
            info["elapsed"] = time.time() - info["started"]
        return info

    def start(self, kind):
        """Launch a job. The caller must hold the session lock."""
        from .session import UserError

        with self.lock:
            if self.running:
                raise UserError("A run is already in progress.")
            ready = self.session.readiness()[kind]
            if ready:
                raise UserError("Not ready: " + "; ".join(ready) + ".")
            session = self.session
            directory = tempfile.mkdtemp(prefix="pysme-gui-job-")
            session.write_job_input(os.path.join(directory, "in.sme"))
            job = {
                "kind": kind,
                "parameters": list(session.sme.fitparameters or []),
                "has_obs": session.obs is not None,
                "ranges": session.ranges,
                "step": session.step,
                "snr": session.snr,
            }
            with open(os.path.join(directory, "job.json"), "w", encoding="utf-8") as handle:
                json.dump(job, handle)

            env = dict(os.environ)
            env["PYTHONPATH"] = os.pathsep.join(
                [PACKAGE_PARENT] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
            )
            env["PYTHONUNBUFFERED"] = "1"
            env.setdefault("MPLBACKEND", "Agg")
            if sys.platform == "win32":
                env["PYTHONUTF8"] = "1"        # the log may name files in any language
            self._counter += 1
            title = "Synthesis" if kind == "synth" else "Fit"
            self.log.add(f"===== {title} started {time.strftime('%H:%M:%S')} =====")
            self._cancelled = False
            self.process = subprocess.Popen(
                [sys.executable, "-m", "pysme_gui.worker", directory],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                text=True, encoding="utf-8", errors="replace", cwd=directory,
                # A group of its own, so that it can be stopped with its helpers
                # (Windows finds those by their parent instead).
                start_new_session=sys.platform != "win32",
            )
            self.info = {
                "id": self._counter, "state": "running", "kind": kind,
                "started": time.time(), "progress": "Preparing model data",
            }
            threading.Thread(
                target=self._watch, args=(self.process, directory, kind), daemon=True
            ).start()

    def _watch(self, process, directory, kind):
        for line in process.stdout:
            for part in line.split("\r"):
                self.log.add(part)
                match = _PROGRESS.search(_ANSI.sub("", part))
                if match:
                    self.info["progress"] = match.group(1).strip()
        code = process.wait()
        elapsed = time.time() - self.info["started"]
        result = {"id": self.info["id"], "kind": kind, "elapsed": elapsed}
        try:
            if self._cancelled:
                result["state"] = "cancelled"
                self.log.add(f"===== Cancelled after {elapsed:.1f} s =====")
            elif code == 0:
                with self.session_lock:
                    self.session.adopt_result(kind, os.path.join(directory, "out.sme"))
                result["state"] = "done"
                self.log.add(f"===== Finished in {elapsed:.1f} s =====")
            else:
                result["state"] = "failed"
                result["error"] = self._read_error(directory, code)
                self.log.add(f"===== Failed after {elapsed:.1f} s =====")
        except Exception as exc:
            result["state"] = "failed"
            result["error"] = f"Could not read the result: {exc}"
            self.log.add(result["error"])
        finally:
            self.info = result
            shutil.rmtree(directory, ignore_errors=True)

    @staticmethod
    def _read_error(directory, code):
        try:
            with open(os.path.join(directory, "error.json"), encoding="utf-8") as handle:
                error = json.load(handle)
            return f"{error['type']}: {error['message']}"
        except (OSError, ValueError, KeyError):
            if code < 0:
                try:
                    name = signal.Signals(-code).name
                except ValueError:
                    name = str(-code)
                return f"PySME crashed ({name}). See the log for the last messages."
            if code >= 0xC0000000:             # how Windows reports a crash
                return (f"PySME crashed (Windows error {code:#010x}). "
                        "See the log for the last messages.")
            return f"PySME exited with code {code}. See the log."

    def cancel(self):
        process = self.process
        if not self.running or process is None or process.poll() is not None:
            return
        self._cancelled = True

        def end():
            stop_process_tree(process)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                stop_process_tree(process, force=True)

        threading.Thread(target=end, daemon=True).start()

    def shutdown(self):
        if self.process is not None and self.process.poll() is None:
            self._cancelled = True
            stop_process_tree(self.process, force=True)
