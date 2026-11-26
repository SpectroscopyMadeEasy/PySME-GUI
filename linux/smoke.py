#!/usr/bin/env python3
"""Check an installed PySME-GUI as a user meets it: it starts, synthesizes the
solar example, is found again when opened twice, and quits when asked.

    linux/smoke.py ~/.local/bin/pysme-gui

Uses the standard library only, so any Python can run it.
"""

import json
import os
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
        [command, "--no-browser"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )

    def read():
        for line in process.stdout:
            output.append(line.rstrip())
            print("  |", line.rstrip(), flush=True)

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


def main():
    command = os.path.expanduser(sys.argv[1])
    first_output, second_output = [], []
    first = start(command, first_output)
    try:
        url = address(first, first_output)
        api = Client(url)
        state = api("state")
        check(state["can_quit"], "the browser version can be quit from the page")

        record = os.path.join(
            os.environ.get("PYSME_GUI_HOME")
            or os.path.expanduser("~/.local/share/pysme-gui"), "running.json")
        check(stat.S_IMODE(os.stat(record).st_mode) == 0o600,
              "only this user can read the record with the access token")

        api("example", {})
        api("run", {"kind": "synth"})
        deadline = time.time() + TIMEOUT
        while True:
            job = api("state")["job"]
            if job["state"] != "running":
                break
            check(time.time() < deadline, "the synthesis ends in time")
            time.sleep(2)
        check(job["state"] == "done", f"the solar example synthesizes ({job.get('error') or job['state']})")
        flux = [value for segment in api("spectrum")["segments"] for value in segment["synth"]]
        # The strong Ca line at 6439 A reaches about 0.31 of the continuum.
        check(len(flux) > 100 and 0.2 < min(flux) < 0.45 and 0.95 < max(flux) <= 1.01,
              f"the spectrum is plausible ({len(flux)} points, {min(flux):.3f} to {max(flux):.3f})")

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
