"""Who may talk to the interface, and what it gives away on the same computer."""

import http.client
import os
import socket
import stat
import sys
import threading
from urllib.parse import parse_qs, unquote_plus, urlsplit

import pytest

from pysme_gui import envmanager, localpeer, report, server as server_module
from pysme_gui.server import create_server

posix = pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
little = pytest.mark.skipif(sys.byteorder != "little", reason="written for little-endian")

SERVER, CLIENT = ("127.0.0.1", 8080), ("127.0.0.1", 40000)
HEADER = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n"


def line(local, remote, uid, inode="5555"):
    return f"   0: {local} {remote} 01 00000000:00000000 00:00000000 00000000 {uid:>5} 0 {inode} 1\n"


def tables(tmp_path, v4="", v6=""):
    first, second = tmp_path / "tcp", tmp_path / "tcp6"
    first.write_text(HEADER + v4)
    second.write_text(HEADER + v6)
    return str(first), str(second)


@little
def test_addresses_are_written_as_the_kernel_does():
    assert localpeer.encoded("127.0.0.1", 8080) == "0100007F:1F90"
    assert localpeer.encoded("127.0.0.1", 8080, ipv6=True) == "0000000000000000FFFF00000100007F:1F90"


def test_the_owner_of_the_other_end_is_found(tmp_path):
    ours, theirs = localpeer.encoded(*SERVER), localpeer.encoded(*CLIENT)
    # Our own end of the same connection comes first and must not be taken.
    found = tables(tmp_path, line(ours, theirs, 1000) + line(theirs, ours, 1234))
    assert localpeer.owner(CLIENT, SERVER, found) == 1234
    ours6, theirs6 = (localpeer.encoded(*a, ipv6=True) for a in (SERVER, CLIENT))
    found = tables(tmp_path, v6=line(theirs6, ours6, 77))
    assert localpeer.owner(CLIENT, SERVER, found) == 77


def test_a_closed_or_unlisted_connection_has_no_owner(tmp_path):
    ours, theirs = localpeer.encoded(*SERVER), localpeer.encoded(*CLIENT)
    # A socket closed already is listed as root's, without an inode.
    assert localpeer.owner(CLIENT, SERVER, tables(tmp_path, line(theirs, ours, 0, "0"))) is None
    assert localpeer.owner(CLIENT, SERVER, tables(tmp_path)) is None
    with pytest.raises(OSError):
        localpeer.owner(CLIENT, SERVER, (str(tmp_path / "none"), str(tmp_path / "none6")))
    first, _ = tables(tmp_path, line(theirs, ours, 8))
    assert localpeer.owner(CLIENT, SERVER, (first, str(tmp_path / "no-ipv6"))) == 8


@posix
@pytest.mark.parametrize("found, accepted", [
    (lambda: os.getuid(), True),
    (lambda: os.getuid() + 1, False),
    (lambda: None, False),
])
def test_only_this_user_passes(monkeypatch, found, accepted):
    monkeypatch.setattr(localpeer, "owner", lambda *a, **k: found())
    assert localpeer.is_this_user(CLIENT, SERVER) is accepted


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux tells who connects")
def test_linux_tells_who_connects():
    assert localpeer.can_tell()


@pytest.fixture
def running(tmp_path, monkeypatch):
    monkeypatch.setenv("PYSME_GUI_HOME", str(tmp_path / "app"))
    app, httpd, _ = create_server()
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield app, httpd
    httpd.shutdown()
    httpd.server_close()


def get_state(app, httpd):
    connection = http.client.HTTPConnection(*httpd.server_address, timeout=60)
    try:
        connection.request("GET", "/api/state", headers={"X-PySME-Token": app.token})
        response = connection.getresponse()
        response.read()                 # hanging up mid-answer would trip the server
        return response.status
    finally:
        connection.close()


def test_another_user_is_refused_even_with_the_token(running, monkeypatch):
    app, httpd = running
    handled = []
    monkeypatch.setattr(app, "get", lambda *a: handled.append(a) or {})
    httpd.check_user = True
    monkeypatch.setattr(localpeer, "is_this_user", lambda client, server: False)
    with pytest.raises((http.client.HTTPException, OSError)):
        get_state(app, httpd)
    assert not handled
    seen = []
    monkeypatch.setattr(localpeer, "is_this_user",
                        lambda client, server: seen.append((client, server)) or True)
    assert get_state(app, httpd) == 200 and handled
    (client, server), = seen
    assert client[0] == "127.0.0.1" and server == httpd.server_address


def answer_to(httpd, headers):
    """Send only the head of a POST; return the status line, without waiting long."""
    with socket.create_connection(httpd.server_address, timeout=10) as connection:
        port = httpd.server_address[1]
        head = [f"POST /api/params HTTP/1.1", f"Host: 127.0.0.1:{port}", *headers, "", ""]
        connection.sendall("\r\n".join(head).encode())
        return connection.recv(200).split(b"\r\n")[0]


@pytest.mark.parametrize("headers, status", [
    (["Content-Length: 100000"], b"403"),                       # no token: no waiting
    (["X-PySME-Token: wrong", "Content-Length: 100000"], b"403"),
    (["Content-Length: -1", "TOKEN"], b"400"),
    (["Content-Length: many", "TOKEN"], b"400"),
    (["Content-Length: 999999999", "TOKEN"], b"413"),
])
def test_a_request_is_judged_before_its_body_is_waited_for(running, headers, status):
    app, httpd = running
    headers = [f"X-PySME-Token: {app.token}" if h == "TOKEN" else h for h in headers]
    assert status in answer_to(httpd, headers)


def test_a_silent_connection_does_not_stay_for_ever(running):
    assert 0 < running[1].RequestHandlerClass.timeout <= 300


def test_the_address_is_given_before_any_file_is_opened(monkeypatch):
    """The Mac app takes the first address printed; a file's name is printed too."""
    order = []

    class Session:
        def open_path(self, path):
            order.append("open")

    class App:
        session = Session()

        class jobs:
            @staticmethod
            def shutdown():
                pass

    class Server:
        def serve_forever(self):
            order.append("serve")

        def server_close(self):
            pass

    monkeypatch.setattr(server_module, "create_server", lambda port=0: (App(), Server(), "url"))
    monkeypatch.setattr(server_module, "announce", lambda url, wanted: order.append("announce"))
    monkeypatch.setattr(server_module.signal, "signal", lambda *a: None)
    server_module.main(["--app-mode", "x.sme"])
    assert order == ["announce", "open", "serve"]


@posix
def test_the_app_folder_is_for_this_user_alone(tmp_path, monkeypatch):
    home = tmp_path / "app"
    home.mkdir(mode=0o777)
    home.chmod(0o775)
    monkeypatch.setenv("PYSME_GUI_HOME", str(home))
    envmanager.keep_private()
    assert stat.S_IMODE(home.stat().st_mode) == 0o700
    mine = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: mine + 1)
    with pytest.raises(RuntimeError, match="another user"):
        envmanager.keep_private()


def test_a_report_without_settings_leaves_out_the_values_being_fitted(running):
    app, _ = running
    job = {"state": "running", "kind": "fit", "progress": "residual with values teff=5123.4"}

    def build(include):
        return report.build("", app.session, job, [], "x", True, "idle", include_settings=include)

    assert "5123.4" not in build(False) and "progress: (hidden)" in build(False)
    assert "teff=5123.4" in build(True)


@pytest.mark.parametrize("smelib, shown", [
    ("SME Library version: 6.13.23, darwin [SMElib ref: v6.13.23]", "SMElib: 6.13.23\n"),
    # A development build must not pass for the release it comes after.
    ("SME Library version: 6.13.23-4-g1a2b3c4-dirty, linux [SMElib ref: x]",
     "SMElib: 6.13.23-4-g1a2b3c4-dirty\n"),
    ("not loadable: /Users/somebody/secret/libsme.so: no such file", "SMElib: unknown"),
    ("unavailable (OSError: C:\\Users\\somebody\\x)", "SMElib: unknown"),
])
def test_the_issue_address_carries_a_version_or_nothing(smelib, shown):
    url = report.issue_url("o/r", "subject", "report.txt", smelib)
    body = parse_qs(urlsplit(url).query)["body"][0]
    assert shown in body
    assert "somebody" not in unquote_plus(url) and "libsme" not in unquote_plus(url)


@posix
def test_a_saved_report_is_for_this_user_alone(tmp_path, monkeypatch):
    (tmp_path / "Downloads").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PYSME_GUI_HOME", str(tmp_path / "app"))
    previous = os.umask(0o022)
    try:
        path = report.save("text\n")
    finally:
        os.umask(previous)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_a_refused_connection_leaves_a_trace_but_not_one_per_try(running, monkeypatch):
    app, httpd = running
    httpd.check_user = True
    monkeypatch.setattr(localpeer, "is_this_user", lambda client, server: False)
    for _ in range(6):
        with pytest.raises((http.client.HTTPException, OSError)):
            get_state(app, httpd)
    lines = [line for line in app.log.since(0)["lines"] if "Refused a connection" in line]
    assert len(lines) == 3 and app.token not in "".join(lines)


@pytest.mark.parametrize("platform, tells, through_file", [
    ("linux", True, False), ("linux", False, True), ("darwin", False, False), ("win32", False, False),
])
def test_when_the_browser_is_given_a_file(monkeypatch, platform, tells, through_file):
    monkeypatch.setattr(server_module.sys, "platform", platform)
    monkeypatch.setattr(localpeer, "can_tell", lambda: tells)
    assert server_module.cannot_tell_users() is through_file


@pytest.mark.parametrize("through_file", [False, True])
def test_the_token_is_not_on_the_browsers_command_line_where_it_matters(
        tmp_path, monkeypatch, through_file):
    monkeypatch.setenv("PYSME_GUI_HOME", str(tmp_path / "an app folder"))
    opened = []
    monkeypatch.setattr(server_module.webbrowser, "open", lambda target: opened.append(target) or True)
    url = "http://127.0.0.1:1234/?token=SECRET_abc-123"
    for _ in range(2):                      # opening it again writes it anew
        server_module.show(url, through_file)
    if not through_file:
        assert opened == [url, url]
        return
    assert all(t.startswith("file://") and "SECRET" not in t for t in opened)
    page = tmp_path / "an app folder" / "open.html"
    assert url in page.read_text(encoding="utf-8")
    if os.name == "posix":
        assert stat.S_IMODE(page.stat().st_mode) == 0o600
