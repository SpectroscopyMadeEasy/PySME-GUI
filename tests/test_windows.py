"""What differs on Windows. Most of it can be checked anywhere by saying the
system is Windows; stopping a run is checked for real on whichever system runs
the tests."""

import importlib.util
import io
import json
import mimetypes
import os
import subprocess
import sys
import threading
import urllib.error

import pytest

from pysme_gui import PYSME_RECOMMENDED, envmanager, jobs, server
from pysme_gui.session import UserError

from test_server_and_env import request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def home(tmp_path, monkeypatch):
    folder = tmp_path / "用户 张" / "PySME-GUI"
    monkeypatch.setenv("PYSME_GUI_HOME", str(folder))
    return folder


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")


# ------------------------------------------------------------- where PySME is


def test_the_recommended_pysme_exists_for_windows():
    assert (envmanager.version_key(PYSME_RECOMMENDED)
            >= envmanager.version_key(envmanager.FIRST_WINDOWS_PYSME))


def test_windows_gets_pysme_from_its_github_release(windows):
    url = ("https://github.com/SpectroscopyMadeEasy/PySME/releases/download/v1.3.0rc2/"
           "pysme_astro-1.3.0rc2-cp312-cp312-win_amd64.whl")
    assert envmanager.windows_wheel_url("1.3.0rc2") == url
    assert envmanager.requirement("1.3.0rc2") == f"pysme-astro @ {url}"


def test_other_systems_get_pysme_from_pypi(monkeypatch):
    for system in ("darwin", "linux"):
        monkeypatch.setattr(sys, "platform", system)
        assert envmanager.requirement("1.3.0rc2") == "pysme-astro==1.3.0rc2"


def test_only_releases_with_a_windows_package_are_listed(windows, monkeypatch):
    tags = b"".join(
        b"0000 refs/tags/v%s\n" % v for v in (b"1.2.0", b"1.3.0rc1", b"1.3.0rc2", b"1.3.0", b"1.3.1")
    )
    asked = []

    def file_date(url):
        asked.append(url.split("/download/")[1].split("/")[0])
        return None if "/v1.3.1/" in url else "2026-11-27"

    monkeypatch.setattr(envmanager, "_download", lambda url, timeout=30: tags)
    monkeypatch.setattr(envmanager, "_file_date", file_date)
    monkeypatch.setattr(envmanager.platform, "machine", lambda: "AMD64")
    assert envmanager.available_pysme() == [
        {"version": "1.3.0", "prerelease": False, "date": "2026-11-27"},
        {"version": "1.3.0rc2", "prerelease": True, "date": "2026-11-27"},
    ]
    assert asked == ["v1.3.1", "v1.3.0", "v1.3.0rc2"]      # older ones are not asked about

    monkeypatch.setattr(envmanager.platform, "machine", lambda: "ARM64")
    assert envmanager.available_pysme() == []


def test_a_missing_package_is_told_apart_from_a_failed_question(monkeypatch):
    def answer(code):
        def urlopen(request, timeout=None):
            assert request.get_method() == "HEAD"
            raise urllib.error.HTTPError(request.full_url, code, "no", {}, io.BytesIO())

        return urlopen

    monkeypatch.setattr(envmanager.urllib.request, "urlopen", answer(404))
    assert envmanager._file_date("https://example.invalid/x.whl") is None
    monkeypatch.setattr(envmanager.urllib.request, "urlopen", answer(503))
    with pytest.raises(urllib.error.HTTPError):
        envmanager._file_date("https://example.invalid/x.whl")


# ------------------------------------------------------------ files and paths


def test_where_windows_keeps_things(windows, monkeypatch, tmp_path):
    monkeypatch.delenv("PYSME_GUI_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert envmanager.app_home() == os.path.join(str(tmp_path), "PySME-GUI")
    assert envmanager.env_python("e").endswith(os.path.join("venv", "Scripts", "python.exe"))
    assert envmanager.uv_environment()["PYTHONUTF8"] == "1"


def test_a_python_folder_and_a_plain_file_are_explained_on_windows(home, windows, tmp_path):
    scripts = tmp_path / "venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_text("")
    with pytest.raises(UserError) as refusal:
        server.set_custom_python(str(tmp_path / "venv"))
    assert f"Did you mean {scripts / 'python.exe'}" in str(refusal.value)
    (tmp_path / "notes.txt").write_text("")
    with pytest.raises(UserError, match="Not an executable Python"):
        server.set_custom_python(str(tmp_path / "notes.txt"))


def test_the_file_browser_offers_the_other_drives(windows, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "drives", lambda: ["C:\\", "D:\\"])
    assert server.list_directory(str(tmp_path))["drives"] == ["C:\\", "D:\\"]
    monkeypatch.setattr(sys, "platform", "darwin")
    assert "drives" not in server.list_directory(str(tmp_path))


def test_the_page_script_has_its_type_whatever_the_system_says(monkeypatch):
    monkeypatch.setattr(mimetypes, "guess_type", lambda *a, **k: ("text/plain", None))
    app, httpd, _ = server.create_server()
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        import urllib.request

        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        for name, kind in (("app.js", "text/javascript"), ("app.css", "text/css"),
                           ("vendor/plotly.min.js", "text/javascript")):
            with urllib.request.urlopen(f"{base}/{name}", timeout=30) as response:
                assert response.headers["Content-Type"].startswith(kind), name
        assert request(base, "/app.js")[0] == 200
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_an_installation_in_use_is_not_half_removed(home, monkeypatch):
    for env_id in ("one", "two"):
        python = envmanager.env_python(env_id)
        os.makedirs(os.path.dirname(python))
        open(python, "w").close()
        envmanager._write_json(os.path.join(envmanager.env_dir(env_id), "env.json"),
                               {"pysme_version": "1.3.0rc2", "created": env_id})

    def in_use(source, target):
        raise PermissionError("used by another process")

    monkeypatch.setattr(envmanager.os, "rename", in_use)
    with pytest.raises(RuntimeError, match="still in use"):
        envmanager.remove_env("two", log=lambda text: None)
    assert {i["id"] for i in envmanager.installed_envs()} == {"one", "two"}


# ------------------------------------------------------------- stopping a run


def test_stopping_a_run_also_stops_what_it_started():
    # The helper inherits the output, so the output ends only when both are gone.
    program = (
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "print('ready', flush=True)\n"
        "time.sleep(120)\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", program], stdout=subprocess.PIPE, text=True,
        start_new_session=sys.platform != "win32",
    )
    assert process.stdout.readline().strip() == "ready"
    ended = threading.Event()
    threading.Thread(target=lambda: (process.stdout.read(), ended.set()), daemon=True).start()
    jobs.stop_process_tree(process, force=True)
    assert ended.wait(30), "a helper process is still running"
    assert process.wait(30) != 0


def test_a_windows_crash_is_called_a_crash(tmp_path):
    message = jobs.JobManager._read_error(str(tmp_path), 0xC0000005)
    assert "crashed" in message and "0xc0000005" in message
    assert "exited with code 2" in jobs.JobManager._read_error(str(tmp_path), 2)


# --------------------------------------------------------------- the launcher


def load_launcher():
    spec = importlib.util.spec_from_file_location(
        "pysme_launch", os.path.join(REPO, "windows", "launch.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def installed(home, code="print('hello 中文')", python=None):
    """A minimal installation whose interface runs ``code``."""
    package = home / "gui" / "g1" / "pysme_gui"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "__main__.py").write_text(code, encoding="utf-8")
    (home / "current.json").write_text(json.dumps({"current": {"gui": "g1", "env": "e1"}}))
    if python:
        (home / "settings.json").write_text(json.dumps({"python": python}))


def test_the_launcher_starts_what_is_chosen(tmp_path, monkeypatch):
    launch = load_launcher()
    home = tmp_path / "app"
    installed(home)
    own = home / "envs" / "e1" / "venv" / "Scripts" / "python.exe"
    with pytest.raises(RuntimeError, match="was not found"):
        launch.interface_command(str(home), [])
    own.parent.mkdir(parents=True)
    own.write_text("")
    monkeypatch.setenv("PYTHONPATH", "elsewhere")
    command, env = launch.interface_command(str(home), ["a b.sme"])
    assert command == [str(own), "-m", "pysme_gui", "a b.sme"]
    assert env["PYTHONPATH"] == os.path.join(str(home), "gui", "g1")
    assert env["PYSME_GUI_HOME"] == str(home) and env["PYTHONUTF8"] == "1"

    (home / "settings.json").write_text(json.dumps({"python": sys.executable}))
    assert launch.interface_command(str(home), [])[0][0] == sys.executable


@pytest.mark.skipif(sys.version_info < (3, 11), reason="needs PYTHONSAFEPATH")
def test_the_launcher_ignores_a_copy_in_the_current_folder(tmp_path, monkeypatch):
    launch = load_launcher()
    home = tmp_path / "app"
    installed(home, code="print('the installed copy')", python=sys.executable)
    other = tmp_path / "elsewhere" / "pysme_gui"
    other.mkdir(parents=True)
    (other / "__init__.py").write_text("")
    (other / "__main__.py").write_text("raise SystemExit(7)")
    monkeypatch.chdir(other.parent)
    # With --console the launcher stays in the folder it was started from.
    assert launch.main(["--console"], home=str(home)) == 0


def test_the_launcher_keeps_what_is_printed_and_reports_a_failure(tmp_path, monkeypatch):
    launch = load_launcher()
    home = tmp_path / "用户 张"
    installed(home, python=sys.executable)
    shown = []
    monkeypatch.setattr(launch, "show_error", shown.append)
    assert launch.main([], home=str(home)) == 0 and shown == []
    assert "hello 中文" in (home / "pysme-gui.log").read_text(encoding="utf-8")

    (home / "gui" / "g1" / "pysme_gui" / "__main__.py").write_text("raise SystemExit(3)")
    assert launch.main([], home=str(home)) == 3
    assert "code 3" in shown[0] and "pysme-gui.log" in shown[0]

    shown.clear()
    assert launch.main([], home=str(tmp_path / "nothing")) == 1
    assert "not installed" in shown[0]
