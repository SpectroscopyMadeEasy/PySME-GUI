import json
import os
import threading
import urllib.error
import urllib.request

import pytest

from pysme_gui import envmanager
from pysme_gui.server import create_server


@pytest.fixture
def server():
    app, httpd, url = create_server()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield app, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def request(base, path, token=None, body=None, host=None):
    headers = {}
    if token:
        headers["X-PySME-Token"] = token
    if host:
        headers["Host"] = host
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def test_api_needs_token_and_loopback_host(server):
    app, base = server
    assert request(base, "/api/state")[0] == 403
    assert request(base, "/api/state", token="wrong")[0] == 403
    assert request(base, "/api/state", token=app.token, host="evil.example:80")[0] == 403
    assert request(base, "/api/new", body={})[0] == 403
    status, body = request(base, "/api/state", token=app.token)
    assert status == 200 and json.loads(body)["job"]["state"] == "idle"


def test_static_files_cannot_escape(server):
    _, base = server
    assert request(base, "/")[0] == 200
    assert request(base, "/../server.py")[0] == 404
    assert request(base, "/..%2fserver.py")[0] == 404
    assert len(request(base, "/vendor/plotly.min.js")[1]) > 1_000_000


def test_user_errors_are_400_with_message(server):
    app, base = server
    status, body = request(base, "/api/open", token=app.token, body={"path": "/nope"})
    assert status == 400 and "not found" in json.loads(body)["error"].lower()
    status, body = request(base, "/api/run", token=app.token, body={"kind": "synth"})
    assert status == 400 and "line list" in json.loads(body)["error"]


def test_pickle_files_are_refused(server, tmp_path):
    app, base = server
    path = tmp_path / "x.npy"
    path.write_bytes(b"x")
    status, body = request(base, "/api/open", token=app.token, body={"path": str(path)})
    assert status == 400 and "pickle" in json.loads(body)["error"]


def test_version_order():
    key = envmanager.version_key
    order = ["1.1.9", "1.2.0.dev3", "1.2.0a1", "1.2.0rc1", "1.2.0", "1.2.0.post1", "1.2.1", "1.10.0"]
    assert sorted(order, key=key) == order
    assert key("1.2") == key("1.2.0")
    assert key("v1.2.0+g123") == key("1.2.0")


def _zip_bytes(entries):
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zipped:
        for name, text in entries.items():
            zipped.writestr(name, text)
    return buffer.getvalue()


def test_gui_download_from_github_archive(tmp_path, monkeypatch):
    monkeypatch.setenv("PYSME_GUI_HOME", str(tmp_path / "home"))
    monkeypatch.delenv(envmanager.SOURCE_ENV, raising=False)
    archive = _zip_bytes({"PySME-GUI-desktop-app/pysme_gui/__init__.py": '__version__ = "9.9.9"\n'})
    urls = []
    monkeypatch.setattr(envmanager, "_download", lambda url, timeout=30: urls.append(url) or archive)
    source = envmanager.fetch_gui_source(str(tmp_path), log=lambda text: None)
    assert os.path.isfile(os.path.join(source, "pysme_gui", "__init__.py"))
    assert urls == ["https://codeload.github.com/SpectroscopyMadeEasy/PySME-GUI/zip/refs/heads/desktop-app"]


def test_gui_download_rejects_unsafe_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("PYSME_GUI_HOME", str(tmp_path / "home"))
    monkeypatch.delenv(envmanager.SOURCE_ENV, raising=False)
    archive = _zip_bytes({"../escape.txt": "x"})
    monkeypatch.setattr(envmanager, "_download", lambda url, timeout=30: archive)
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(RuntimeError, match="unsafe"):
        envmanager.fetch_gui_source(str(work), log=lambda text: None)
    assert not (work / "escape.txt").exists()
