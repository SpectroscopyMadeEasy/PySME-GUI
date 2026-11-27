"""The interface and PySME are installed and updated separately."""

import json
import os
import re
import sys
import threading
import urllib.error
import urllib.request

import pytest

from pysme_gui import envmanager
from pysme_gui.server import Updater, create_server
from pysme_gui.jobs import LogBuffer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def make_source(folder, version, recommended="1.2.0", minimum="1.2.0", below="2.0"):
    package = folder / f"src-{version}" / "pysme_gui"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text(
        f'__version__ = "{version}"\nUPDATE_REPO = "o/r"\nUPDATE_REF = "b"\n'
        f'PYSME_RECOMMENDED = "{recommended}"\nPYSME_MINIMUM = "{minimum}"\n'
        f'PYSME_BELOW = "{below}"\n'
    )
    return str(package.parent)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An app folder where building and checking environments is simulated."""
    monkeypatch.setenv("PYSME_GUI_HOME", str(tmp_path / "home"))
    monkeypatch.delenv(envmanager.SOURCE_ENV, raising=False)
    calls = []

    class Home:
        pass

    result = Home()
    result.calls = calls
    result.source = lambda version, **kw: make_source(tmp_path, version, **kw)

    def create_env(version, gui_id, lock=None, log=print):
        calls.append(("create", version, lock))
        env_id = envmanager._new_id()
        python = envmanager.env_python(env_id)
        os.makedirs(os.path.dirname(python))
        open(python, "w").close()
        envmanager._write_text(
            os.path.join(envmanager.env_dir(env_id), "lock.txt"),
            lock or f"numpy==2.0.0\npysme-astro=={version}\n",
        )
        envmanager._write_json(os.path.join(envmanager.env_dir(env_id), "env.json"), {
            "pysme_version": version, "smelib_version": "6.13", "python_version": "3.12.9",
            "created": "2026-01-01 00:00:00",
        })
        return env_id

    def smoke(gui_id, python, log):
        calls.append(("smoke", envmanager.gui_info(gui_id)["version"]))
        return {}

    monkeypatch.setattr(envmanager, "create_env", create_env)
    monkeypatch.setattr(envmanager, "smoke_test", smoke)
    return result


def quiet(text):
    pass


def in_use():
    gui_id, env_id = envmanager.selection()
    return envmanager.gui_info(gui_id)["version"], envmanager.env_info(env_id)["pysme_version"]


def test_this_interface_declares_a_consistent_range():
    constants = envmanager.package_constants(os.path.join(REPO, "pysme_gui"))
    assert envmanager.supports(constants, constants["recommended"])
    assert envmanager.supports(constants, constants["minimum"])


def test_supported_range():
    constants = {"minimum": "1.2.0", "below": "2.0"}
    for good in ("1.2.0", "1.2.1", "1.3.0rc1", "1.99"):
        assert envmanager.supports(constants, good), good
    for bad in ("1.1.1", "1.2.0rc1", "2.0.0", "2.0.0rc1", "0.7.0"):
        assert not envmanager.supports(constants, bad), bad


@pytest.mark.parametrize("name, system, ok", [
    ("pysme_astro-1.2.0-cp312-cp312-macosx_14_0_arm64.whl", ("darwin", "arm64", "15.1"), True),
    ("pysme_astro-1.2.0-cp312-cp312-macosx_14_0_arm64.whl", ("darwin", "arm64", "13.6"), False),
    ("pysme_astro-1.2.0-cp311-cp311-macosx_14_0_arm64.whl", ("darwin", "arm64", "15.1"), False),
    ("pysme_astro-1.2.0-cp312-cp312-macosx_14_0_arm64.whl", ("darwin", "x86_64", "15.1"), False),
    ("pysme_astro-1.2.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
     ("linux", "x86_64", "2.35"), True),
    ("pysme_astro-1.2.0-cp312-cp312-manylinux_2_34_x86_64.whl", ("linux", "x86_64", "2.28"), False),
    ("pysme_astro-1.2.0-cp312-cp312-win_amd64.whl", ("win32", "AMD64", ""), True),
    ("pysme_astro-1.2.0.tar.gz", ("linux", "x86_64", "2.35"), False),
])
def test_only_installable_wheels_count(name, system, ok):
    assert envmanager.compatible_wheel(name, *system) is ok


def test_release_list_skips_source_only_and_yanked():
    wheel = "pysme_astro-{}-cp312-cp312-macosx_14_0_arm64.whl"
    entry = lambda name, **kw: dict(filename=name, upload_time="2026-01-02T00:00:00", **kw)
    data = {"releases": {
        "1.2.0": [entry(wheel.format("1.2.0"))],
        "1.10.0": [entry(wheel.format("1.10.0"))],
        "1.3.0rc1": [entry(wheel.format("1.3.0rc1"))],
        "1.4.0": [entry("pysme_astro-1.4.0.tar.gz")],
        "1.5.0": [entry(wheel.format("1.5.0"), yanked=True)],
        "1.6.0": [],
    }}
    found = envmanager.parse_releases(data, ("darwin", "arm64", "15.0"))
    assert [r["version"] for r in found] == ["1.10.0", "1.3.0rc1", "1.2.0"]
    assert [r["prerelease"] for r in found] == [False, True, False]


def test_first_launch_installs_the_recommended_version(home):
    envmanager.setup(home.source("1.0.0"), bundle_id="a", log=quiet)
    assert in_use() == ("1.0.0", "1.2.0")
    assert envmanager.current_record()["bundle_id"] == "a"


def test_installing_a_version_does_not_switch_and_nothing_is_pruned(home):
    envmanager.setup(home.source("1.0.0"), log=quiet)
    first = envmanager.selection()[1]
    second = envmanager.install_pysme("1.3.0", log=quiet)
    third = envmanager.install_pysme("1.4.0", log=quiet)
    assert in_use() == ("1.0.0", "1.2.0")
    envmanager.use_env(second, log=quiet)
    envmanager.use_env(third, log=quiet)
    assert in_use() == ("1.0.0", "1.4.0")
    assert {i["id"] for i in envmanager.installed_envs()} == {first, second, third}
    with pytest.raises(RuntimeError, match="already installed"):
        envmanager.install_pysme("1.3.0", log=quiet)
    with pytest.raises(RuntimeError, match="needs PySME"):
        envmanager.install_pysme("1.1.1", log=quiet)


def test_removing_versions(home):
    envmanager.setup(home.source("1.0.0"), log=quiet)
    first = envmanager.selection()[1]
    other = envmanager.install_pysme("1.3.0", log=quiet)
    with pytest.raises(RuntimeError, match="in use"):
        envmanager.remove_env(first, log=quiet)
    envmanager.use_env(other, log=quiet)
    envmanager.remove_env(first, log=quiet)
    assert [i["id"] for i in envmanager.installed_envs()] == [other]
    assert envmanager.current_record()["previous"] is None


def test_new_disk_image_keeps_the_chosen_pysme(home):
    envmanager.setup(home.source("1.0.0"), bundle_id="a", log=quiet)
    envmanager.use_env(envmanager.install_pysme("1.3.0", log=quiet), log=quiet)
    chosen = envmanager.selection()[1]
    home.calls.clear()
    envmanager.setup(home.source("1.1.0", recommended="1.4.0"), bundle_id="b", log=quiet)
    assert in_use() == ("1.1.0", "1.3.0") and envmanager.selection()[1] == chosen
    assert not [c for c in home.calls if c[0] == "create"]
    assert envmanager.current_record()["notice"] is None


def test_new_disk_image_that_drops_the_chosen_pysme_says_so_and_keeps_it(home):
    envmanager.setup(home.source("1.0.0"), log=quiet)
    old = envmanager.selection()[1]
    envmanager.setup(home.source("2.0.0", recommended="1.5.0", minimum="1.5.0"), log=quiet)
    assert in_use() == ("2.0.0", "1.5.0")
    assert "1.2.0 is still installed" in envmanager.current_record()["notice"]
    assert envmanager.env_info(old)
    with pytest.raises(RuntimeError, match="needs PySME"):
        envmanager.use_env(old, log=quiet)


def test_repair_rebuilds_from_the_package_list(home):
    envmanager.setup(home.source("1.0.0"), log=quiet)
    old = envmanager.selection()[1]
    lock = "numpy==1.0.0\npysme-astro==1.2.0\n"
    envmanager._write_text(os.path.join(envmanager.env_dir(old), "lock.txt"), lock)
    home.calls.clear()
    envmanager.setup(home.source("1.0.0"), repair=True, log=quiet)
    assert ("create", "1.2.0", lock) in home.calls
    assert envmanager.selection()[1] != old and not os.path.exists(envmanager.env_dir(old))
    assert envmanager.current_record()["previous"] is None


def test_failed_setup_leaves_current_untouched(home, monkeypatch):
    envmanager.setup(home.source("1.0.0"), log=quiet)
    before = envmanager.current_record()
    guis = os.listdir(os.path.join(envmanager.app_home(), "gui"))

    def broken(gui_id, python, log):
        raise RuntimeError("does not start")

    monkeypatch.setattr(envmanager, "smoke_test", broken)
    with pytest.raises(RuntimeError, match="does not start"):
        envmanager.setup(home.source("1.1.0"), log=quiet)
    assert envmanager.current_record() == before
    assert os.listdir(os.path.join(envmanager.app_home(), "gui")) == guis


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in uv is a shell script")
def test_failed_download_leaves_no_half_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("PYSME_GUI_HOME", str(tmp_path / "home"))
    fake_uv = tmp_path / "uv"
    fake_uv.write_text("#!/bin/sh\necho no network >&2\nexit 1\n")
    fake_uv.chmod(0o755)
    monkeypatch.setenv("PYSME_GUI_UV", str(fake_uv))
    with pytest.raises(RuntimeError, match="no network"):
        envmanager.setup(REPO, log=quiet)
    assert envmanager.current_record() == {}
    assert envmanager.installed_envs() == []
    assert os.listdir(tmp_path / "home" / "envs") == []
    assert os.listdir(tmp_path / "home" / "gui") == []


def test_interface_update_and_back_never_change_pysme(home, monkeypatch):
    envmanager.setup(home.source("1.0.0"), log=quiet)
    envmanager.use_env(envmanager.install_pysme("1.3.0", log=quiet), log=quiet)
    chosen = envmanager.selection()[1]
    monkeypatch.setenv(envmanager.SOURCE_ENV, home.source("1.1.0", recommended="1.4.0"))
    envmanager.update_gui(log=quiet)
    assert in_use() == ("1.1.0", "1.3.0")
    # Switching PySME in between must not lose the way back to the old interface.
    envmanager.use_env(envmanager.install_pysme("1.4.0", log=quiet), log=quiet)
    envmanager.use_env(chosen, log=quiet)
    envmanager.rollback_gui(log=quiet)
    assert in_use() == ("1.0.0", "1.3.0")
    envmanager.rollback_gui(log=quiet)
    assert in_use() == ("1.1.0", "1.3.0")


def test_interface_update_that_drops_the_pysme_in_use_is_refused(home, monkeypatch):
    envmanager.setup(home.source("1.0.0"), log=quiet)
    before = envmanager.current_record()
    monkeypatch.setenv(envmanager.SOURCE_ENV, home.source("3.0.0", minimum="1.5.0"))
    with pytest.raises(RuntimeError, match="stays installed"):
        envmanager.update_gui(log=quiet)
    assert envmanager.current_record() == before


def test_update_check_reports_both_separately(home, monkeypatch):
    envmanager.setup(home.source("1.0.0"), log=quiet)
    monkeypatch.setattr("pysme_gui.server.is_managed", lambda: True)
    monkeypatch.setattr("pysme_gui.server.pysme_version", lambda: "1.2.0")
    monkeypatch.setattr(envmanager, "latest_gui", lambda: envmanager.read_constants(
        '__version__ = "1.1.0"\nPYSME_MINIMUM = "1.5.0"\nPYSME_BELOW = "2.0"\n'))
    releases = [{"version": v, "prerelease": envmanager.is_prerelease(v), "date": ""}
                for v in ("2.1.0", "1.4.0rc1", "1.3.0", "1.2.0", "1.1.0")]
    monkeypatch.setattr(envmanager, "available_pysme", lambda: releases)
    updater = Updater(LogBuffer())
    result = updater.check()
    assert result["gui_newer"] and "1.5.0 or newer" in result["gui_blocked"]
    assert [r["version"] for r in result["releases"]] == ["1.4.0rc1", "1.3.0", "1.2.0"]
    assert result["pysme_new"] == "1.3.0"
    # Once installed it is not announced again, even while an older one is used.
    envmanager.install_pysme("1.3.0", log=quiet)
    assert updater.check()["pysme_new"] is None


def test_package_list_export(home, tmp_path):
    envmanager.setup(home.source("1.0.0"), log=quiet)
    target = tmp_path / "list.txt"
    envmanager.export_lock(envmanager.selection()[1], str(target))
    text = target.read_text()
    assert "# PySME 1.2.0, SMElib: 6.13" in text and "pysme-astro==1.2.0" in text
    assert all(re.match(r"#|[A-Za-z0-9_.-]+==", line) for line in text.splitlines())


def test_version_actions_need_the_apps_own_environment():
    app, httpd, _ = create_server()
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        for route, body in (("pysme/install", {"version": "1.2.0"}), ("pysme/use", {"id": "x"}),
                            ("pysme/remove", {"id": "../.."}), ("pysme/export", {"id": "x"})):
            request = urllib.request.Request(
                f"http://127.0.0.1:{httpd.server_address[1]}/api/{route}",
                data=json.dumps(body).encode(), headers={"X-PySME-Token": app.token})
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request, timeout=30)
            assert error.value.code == 400, route
            assert "your own Python" in json.loads(error.value.read())["error"]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_exported_spectrum_records_the_versions(tmp_path):
    from pysme_gui.session import Session

    session = Session()
    session.load_example()
    session.sme.synth = [session.sme.wran[0] * 0 + 1]
    session.sme.wave = [session.sme.wran[0]]
    session.computed_with = {"gui": "1.0.0", "pysme": "9.9", "smelib": "6.13"}
    path = session.export_csv(str(tmp_path / "s.csv"))
    first = open(path, encoding="utf-8").readline()
    assert first == "# synth computed with PySME 9.9, SMElib 6.13, PySME-GUI 1.0.0\n"
    saved = session.save(str(tmp_path / "s.sme"))
    again = Session()
    again.load_sme(saved)
    assert again.computed_with == session.computed_with
