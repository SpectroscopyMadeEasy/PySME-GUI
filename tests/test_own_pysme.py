"""Running the interface with a PySME the user installed themselves."""

import json
import os
import stat
import sys

import pytest

from pysme_gui import envmanager
from pysme_gui.server import App, set_custom_python
from pysme_gui.session import UserError


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PYSME_GUI_HOME", str(tmp_path / "app"))
    envmanager.write_settings({"theme": "dark"})
    return tmp_path


def fake_python(folder, version=None, error=None):
    """A stand-in interpreter that answers the start check."""
    path = folder / f"python-{version or 'broken'}"
    if error:
        body = f"echo 'Traceback ...' >&2\necho \"{error}\" >&2\nexit 1\n"
    else:
        answer = json.dumps({"pysme": version, "smelib": "v6", "gui": "x", "python": "3.12"})
        body = f"echo 'some warning'\necho 'SMOKE {answer}'\n"
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def test_a_real_python_is_checked_with_the_interface_itself():
    import pysme

    found = set_custom_python(sys.executable)
    assert found["saved"] and found["pysme"] == pysme.__version__
    assert envmanager.settings() == {
        "theme": "dark", "python": os.path.abspath(sys.executable), "python_pysme": pysme.__version__,
    }


def test_pasted_quotes_and_spaces_are_forgiven(home):
    python = fake_python(home, "1.2.0")
    assert set_custom_python(f'  "{python}"  ')["saved"]
    assert envmanager.settings()["python"] == python


def test_mistakes_are_explained_and_nothing_is_stored(home):
    (home / "env" / "bin").mkdir(parents=True)
    (home / "env" / "bin" / "python").write_text("")
    (home / "plain.txt").write_text("")
    for value, words in (
        (str(home / "env"), "Did you mean " + str(home / "env" / "bin" / "python")),
        (str(home / "plain.txt"), "Not an executable Python"),
        ("python", "Enter its full path"),
        (fake_python(home, error="ModuleNotFoundError: No module named 'pysme'"),
         "cannot start with that Python: ModuleNotFoundError: No module named 'pysme'"),
    ):
        with pytest.raises(UserError) as refusal:
            set_custom_python(value)
        assert words in str(refusal.value)
    assert envmanager.settings() == {"theme": "dark"}


def test_a_version_outside_the_range_needs_a_confirmation(home):
    old = fake_python(home, "1.1.0")
    found = set_custom_python(old)
    assert not found["saved"] and not found["supported"] and "1.2.0 or newer" in found["range"]
    assert envmanager.settings() == {"theme": "dark"}
    assert set_custom_python(old, accept_unsupported=True)["saved"]
    info = App().updater.info()
    assert info["custom_pysme"] == "1.1.0" and info["custom_supported"] is False
    assert info["restart"]                      # this process still runs another Python


def test_clearing_returns_to_the_app_and_keeps_other_settings(home):
    set_custom_python(fake_python(home, "1.2.1.dev3"))
    assert set_custom_python("   ") is None
    assert envmanager.settings() == {"theme": "dark"}


def test_the_settings_request_reports_what_was_found(home):
    app = App()
    answer = app.post("settings/python", {"python": fake_python(home, "1.3.0rc1")})
    assert answer["checked"]["saved"] and answer["custom_pysme"] == "1.3.0rc1"
    assert answer["custom_supported"] is True
    assert app.post("settings/python", {"python": ""})["checked"] is None


def test_settings_changed_at_the_same_time_are_all_kept():
    import threading

    errors = []

    def change(number):
        try:
            for i in range(15):
                envmanager.change_settings(**{f"key{number}_{i}": number})
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=change, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    stored = envmanager.settings()
    assert len(stored) == 1 + 8 * 15 and stored["theme"] == "dark"
    assert [n for n in os.listdir(envmanager.app_home()) if n.endswith(".tmp")] == []


def test_a_path_through_a_linked_folder_is_still_the_same_python(home):
    from pysme_gui.server import same_program

    (home / "real" / "bin").mkdir(parents=True)
    (home / "base").write_text("")
    for environment in ("real", "other"):
        (home / environment / "bin").mkdir(parents=True, exist_ok=True)
        os.symlink(home / "base", home / environment / "bin" / "python")
    os.symlink(home / "real", home / "link")
    assert same_program(str(home / "link" / "bin" / "python"), str(home / "real" / "bin" / "python"))
    # Two environments built on one interpreter are different programs.
    assert not same_program(str(home / "real" / "bin" / "python"), str(home / "other" / "bin" / "python"))
