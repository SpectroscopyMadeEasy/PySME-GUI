#!/usr/bin/env python3
"""Check an installed PySME-GUI as a user meets it: it starts, synthesizes the
solar example, stops a run when asked, is found again when opened twice, and
quits when asked.

    tools/smoke.py ~/.local/bin/pysme-gui             start it with this command
    tools/smoke.py --attach                           use the one already started
                                                      (by a Windows shortcut)

Uses the standard library only, so any Python can run it.
"""

import json
import os
import re
import stat
import subprocess
import sys
import threading
import time
import urllib.request
from urllib.parse import parse_qs, urlsplit

TIMEOUT = 900      # the first synthesis downloads the model grid


def start(command, output):
    process = subprocess.Popen(
        [*command, "--no-browser"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )

    def read():
        for line in process.stdout:
            output.append(line.rstrip())
            # Test logs are public: the access token stays out of them.
            print("  |", re.sub(r"(token=)[A-Za-z0-9_\-]+", r"\1<hidden>", line.rstrip()), flush=True)

    threading.Thread(target=read, daemon=True).start()
    return process


def address(process, output):
    deadline = time.time() + 120
    while time.time() < deadline:
        for line in output:
            if line.startswith("PYSME_GUI_URL "):
                return line.split(" ", 1)[1]
        if process.poll() is not None:
            break
        time.sleep(0.2)
    raise SystemExit("FAILED: the program did not give its address")


class Client:
    def __init__(self, url):
        parts = urlsplit(url)
        self.base = f"{parts.scheme}://{parts.netloc}/api/"
        self.token = parse_qs(parts.query)["token"][0]
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def __call__(self, route, body=None):
        request = urllib.request.Request(
            self.base + route, data=None if body is None else json.dumps(body).encode(),
            headers={"X-PySME-Token": self.token},
        )
        with self.opener.open(request, timeout=120) as response:
            return json.loads(response.read())


def check(condition, what):
    print(("ok      " if condition else "FAILED  ") + what, flush=True)
    if not condition:
        raise SystemExit(1)


def app_home():
    if os.environ.get("PYSME_GUI_HOME"):
        return os.environ["PYSME_GUI_HOME"]
    if sys.platform == "win32":
        return os.path.join(os.environ["LOCALAPPDATA"], "PySME-GUI")
    return os.path.expanduser("~/.local/share/pysme-gui")


def not_overdue(deadline, what):
    """Say something only when time has run out."""
    if time.time() > deadline:
        check(False, what)


def finished_job(api):
    deadline = time.time() + TIMEOUT
    while True:
        job = api("state")["job"]
        if job["state"] != "running":
            return job
        not_overdue(deadline, "the run ends in time")
        time.sleep(1)


def synthesize(api, what):
    api("run", {"kind": "synth"})
    job = finished_job(api)
    check(job["state"] == "done", f"{what} ({job.get('error') or job['state']})")


def inside(path, folder):
    path, folder = (os.path.normcase(os.path.realpath(p)) for p in (path, folder))
    return path.startswith(folder + os.sep)


def exercise(api, record):
    """What a user does with a running interface; ``record`` is its running.json."""
    check(api("state")["can_quit"], "the browser version can be quit from the page")
    # It has answered, so its record is written.
    with open(record, encoding="utf-8") as handle:
        code = json.load(handle).get("code") or ""
    check(bool(code) and inside(code, os.path.join(app_home(), "gui")),
          f"the installed copy of the interface runs ({code or 'not recorded'})")
    if os.name == "posix":
        check(stat.S_IMODE(os.stat(record).st_mode) == 0o600,
              "only this user can read the record with the access token")

    api("example", {})
    synthesize(api, "the solar example synthesizes")
    flux = [value for segment in api("spectrum")["segments"] for value in segment["synth"]]
    # The strong Ca line at 6439 A reaches about 0.31 of the continuum.
    check(len(flux) > 100 and 0.2 < min(flux) < 0.45 and 0.95 < max(flux) <= 1.01,
          f"the spectrum is plausible ({len(flux)} points, {min(flux):.3f} to {max(flux):.3f})")

    api("run", {"kind": "synth"})
    api("cancel", {})
    check(finished_job(api)["state"] == "cancelled", "a run stops when asked")
    synthesize(api, "and the next run works")


def attach():
    record = os.path.join(app_home(), "running.json")
    deadline = time.time() + 180
    while not os.path.exists(record):
        not_overdue(deadline, "the interface started by itself")
        time.sleep(1)
    time.sleep(1)
    with open(record, encoding="utf-8") as handle:
        api = Client(json.load(handle)["url"])
    exercise(api, record)
    check(api("quit", {})["quitting"], "it accepts being quit")
    deadline = time.time() + 60
    while os.path.exists(record):
        not_overdue(deadline, "and ends, leaving no record behind")
        time.sleep(1)
    check(True, "and ends, leaving no record behind")
    log = os.path.join(app_home(), "pysme-gui.log")
    if os.path.exists(log):
        with open(log, encoding="utf-8", errors="replace") as handle:
            check("token=" not in handle.read(), "its log does not hold the access token")


def main():
    if sys.argv[1:] == ["--attach"]:
        return attach()
    command = [os.path.expanduser(sys.argv[1]), *sys.argv[2:]]
    first_output, second_output = [], []
    first = start(command, first_output)
    try:
        url = address(first, first_output)
        api = Client(url)
        record = os.path.join(app_home(), "running.json")
        exercise(api, record)

        second = start(command, second_output)
        check(address(second, second_output) == url, "opening it again leads to the same one")
        check(second.wait(60) == 0 and first.poll() is None,
              "the second start ends, the first stays")

        check(api("quit", {})["quitting"], "it accepts being quit")
        check(first.wait(60) == 0, "and ends")
        check(not os.path.exists(record), "leaving no record behind")
    finally:
        if first.poll() is None:
            first.kill()


if __name__ == "__main__":
    main()
