import json
import os
import threading
import urllib.request
from urllib.parse import parse_qs, unquote_plus, urlsplit

import pytest

from pysme_gui import report
from pysme_gui.server import create_server


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "张三"
    (home / "Downloads").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PYSME_GUI_HOME", str(home / "app"))
    return home


@pytest.fixture
def server(home):
    app, httpd, _ = create_server()
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}/api/"

    def post(route, body):
        request = urllib.request.Request(
            base + route, data=json.dumps(body).encode(), headers={"X-PySME-Token": app.token}
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    yield app, post
    httpd.shutdown()
    httpd.server_close()


def test_redaction_removes_user_names_and_token():
    text = report.redact(
        "File /Users/zhang/data/a.sme\n/Users/zhang\n/Users/zhangsan/x\n"
        "C:\\Users\\Li Lei\\AppData\\y\n/home/wang/z\nhttp://127.0.0.1:1/?token=abc_DEF-123\n"
        "/Users/Shared/ok\n",
        home="/Users/zhang",
    )
    for secret in ("zhang", "Li Lei", "wang", "abc_DEF-123"):
        assert secret not in text
    assert "~/data/a.sme" in text and "/Users/<user>/x" in text
    assert "C:\\Users\\<user>\\AppData\\y" in text and "/home/<user>/z" in text
    assert "/Users/Shared/ok" in text and "token=<hidden>" in text


def test_report_has_versions_log_and_path_flags(server, home):
    app, post = server
    app.log.add(f"Fe I opacity table not found at {home}/lib/table.dat")
    app.log.add("Synthesizing residual with values teff=5123.4")
    app.session.sme.teff = 5123.4
    app.session.path = str(home / "SecretStar.sme")
    status, data = post("report/preview", {
        "description": "合成失败 it crashed", "client": {"agent": "TestAgent", "errors": ["boom"]},
    })
    assert status == 200
    text = data["text"]
    for wanted in ("合成失败 it crashed", "PySME-GUI: ", "pysme-astro: ", "SMElib: ",
                   "Home path is plain ASCII: NO", "~/lib/table.dat", "TestAgent",
                   "Error shown: boom", "values (hidden)"):
        assert wanted in text
    for unwanted in ("张三", "5123.4", "Current settings", "SecretStar", app.token):
        assert unwanted not in text
    with_settings = post("report/preview", {"settings": True})[1]["text"]
    assert "== Current settings ==" in with_settings and "teff: 5123.4" in with_settings
    assert "values teff=5123.4" in with_settings and "SecretStar" not in with_settings


def test_report_includes_application_log_tail(server, home):
    _, post = server
    logs = home / "app" / "logs"
    logs.mkdir(parents=True)
    lines = [f"line {i}" for i in range(500)] + [f"open {home}/q"]
    (logs / "app.log").write_text("\n".join(lines), encoding="utf-8")
    text = post("report/preview", {})[1]["text"]
    assert "open ~/q" in text and "line 499" in text and "line 100\n" not in text


def test_report_survives_a_broken_session(server):
    app, post = server
    app.session.sme = None
    status, data = post("report/preview", {"settings": True})
    assert status == 200 and "unavailable" in data["text"]


def test_save_writes_edited_text_and_never_overwrites(server, home):
    app, post = server
    assert post("report/reveal", {})[0] == 400
    assert post("report/save", {"text": "  "})[0] == 400
    first = post("report/save", {"text": "edited 报告\n"})[1]
    second = post("report/save", {"text": "another\n"})[1]
    assert first["path"] != second["path"]
    assert os.path.dirname(first["path"]) == str(home / "Downloads")
    with open(first["path"], encoding="utf-8-sig") as handle:
        assert handle.read() == "edited 报告\n"
    assert os.path.basename(first["path"]) in first["body"]
    assert first["subject"].startswith("PySME-GUI problem report (GUI ")


def test_issue_link_carries_versions_only(server, home):
    app, post = server
    app.log.add("secret log line")
    text = post("report/preview", {"description": "my unpublished star HD1"})[1]["text"]
    saved = post("report/save", {"text": text})[1]
    url = urlsplit(saved["issue_url"])
    assert url.netloc == "github.com" and url.path.endswith("/PySME-GUI/issues/new")
    query = parse_qs(url.query)
    body = query["body"][0]
    assert os.path.basename(saved["path"]) in body and "pysme-astro: " in body
    for private in ("HD1", "secret log line", "张三", "Downloads", app.token):
        assert private not in unquote_plus(saved["issue_url"])
    assert len(saved["issue_url"]) < 4000


def test_save_falls_back_when_downloads_is_missing(server, home):
    (home / "Downloads").rmdir()
    _, post = server
    path = post("report/save", {"text": "x"})[1]["path"]
    assert os.path.dirname(path) == str(home)
