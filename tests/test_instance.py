"""The browser version: quitting from the page, and finding the one that runs."""

import json
import os
import stat
import threading

import pytest

from pysme_gui import instance
from pysme_gui.server import create_server

from test_server_and_env import request


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PYSME_GUI_HOME", str(tmp_path / "app home"))
    return tmp_path / "app home"


@pytest.fixture
def served():
    app, server, url = create_server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield app, server, url, url.split("/?")[0]
    server.shutdown()
    server.server_close()


def test_the_page_can_quit_only_where_it_is_allowed(home, served):
    app, server, url, base = served
    # As in the Mac app, which quits by its own menu.
    status, body = request(base, "/api/state", token=app.token)
    assert json.loads(body)["can_quit"] is False
    status, body = request(base, "/api/quit", token=app.token, body={})
    assert status == 400 and "own menu" in json.loads(body)["error"]

    asked = threading.Event()
    app.on_quit = asked.set
    assert json.loads(request(base, "/api/state", token=app.token)[1])["can_quit"] is True
    assert request(base, "/api/quit", body={})[0] == 403 and not asked.wait(0.6)
    status, body = request(base, "/api/quit", token=app.token, body={})
    assert status == 200 and json.loads(body) == {"quitting": True}
    assert asked.wait(5)


def test_a_running_interface_is_found_and_a_gone_one_is_not(home, served):
    app, server, url, base = served
    assert instance.running() is None
    instance.register(url)
    record = home / "running.json"
    # It holds the access token: nobody else on the computer may read it.
    assert stat.S_IMODE(os.stat(record).st_mode) == 0o600
    assert instance.running() == url

    # A second interface does not take the place of the one that runs.
    instance.register("http://127.0.0.1:1/?token=other")
    assert instance.running() == url
    instance.unregister("http://127.0.0.1:1/?token=other")
    assert instance.running() == url

    # A record left by a crash, with a wrong token, or pointing elsewhere.
    for stale in ("http://127.0.0.1:1/?token=x", url + "wrong", "http://example.org/?token=x"):
        record.write_text(json.dumps({"url": stale}))
        assert instance.running() is None, stale
    record.write_text("not json")
    assert instance.running() is None
    instance.register(url)
    assert instance.running() == url
    instance.unregister(url)
    assert not record.exists()
