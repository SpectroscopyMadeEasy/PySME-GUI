"""Releases are git tags; each user receives those of their update channel."""

import os
import shutil
import subprocess
import sys

import pytest

import pysme_gui
from pysme_gui import envmanager

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORDER = ["0.2.39", "0.3.0a1", "0.3.0a2", "0.3.0a10", "0.3.0b1", "0.3.0rc1", "0.3.0",
         "0.3.1", "0.4.0a1", "0.10.0", "1.0.0"]


def refs(*tags):
    """Git's list of references, in its packet format."""
    lines = ["001e# service=git-upload-pack\n0000"]
    lines.append("00aa" + "a" * 40 + " HEAD\0multi_ack agent=x\n")
    for tag in tags:
        lines.append("003f" + "b" * 40 + f" refs/tags/{tag}\n")
    return ("".join(lines) + "0000").encode()


def test_this_version_is_a_valid_release_number():
    assert envmanager.parse_tags(refs(f"v{pysme_gui.__version__}")) == [pysme_gui.__version__]


def test_only_release_tags_of_this_program_count():
    found = envmanager.parse_tags(refs(
        "v0.2.39", "v0.2.39^{}", "pysme-gui-1.9", "20200415174831-e9a9214", "v0.3.0a1",
        "v0.3.0a1^{}", "v0.3.0", "v0.3.0-rc1", "v0.3.0.dev1", "v0.3.0b2", "v9.9.9x", "nightly",
    ))
    assert found == ["0.3.0", "0.3.0b2", "0.3.0a1"]


def test_stages_and_channels():
    assert [envmanager.stage(v) for v in ("0.3.0a1", "0.3.0b1", "0.3.0rc1", "0.3.0")] == [
        "alpha", "beta", "beta", "stable"]
    receives = {
        channel: [v for v in ("0.4.0a1", "0.3.1b1", "0.3.1rc1", "0.3.0")
                  if envmanager.in_channel(v, channel)]
        for channel in envmanager.CHANNELS
    }
    assert receives == {
        "stable": ["0.3.0"],
        "beta": ["0.3.1b1", "0.3.1rc1", "0.3.0"],
        "alpha": ["0.4.0a1", "0.3.1b1", "0.3.1rc1", "0.3.0"],
    }


@pytest.fixture
def published(tmp_path, monkeypatch):
    """A pretend GitHub with the given tags."""
    monkeypatch.setenv("PYSME_GUI_HOME", str(tmp_path))
    monkeypatch.delenv(envmanager.SOURCE_ENV, raising=False)
    tags, claimed, urls = [], {}, []

    def download(url, timeout=30):
        urls.append(url)
        if url.endswith("info/refs?service=git-upload-pack"):
            return refs(*(f"v{v}" for v in tags))
        tag = url.split("/")[-3]
        return f'__version__ = "{claimed.get(tag, tag[1:])}"\n'.encode()

    monkeypatch.setattr(envmanager, "_download", download)
    return tags, claimed, urls


@pytest.mark.parametrize("channel, expected", [
    ("stable", "0.3.0"), ("beta", "0.3.1b1"), ("alpha", "0.4.0a1"),
])
def test_each_channel_gets_its_newest_tag(published, channel, expected):
    tags, _, urls = published
    tags.extend(["0.3.0b1", "0.3.0", "0.3.1b1", "0.4.0a1"])
    envmanager.set_channel(channel)
    found = envmanager.latest_gui()
    assert found["version"] == expected and found["tag"] == f"v{expected}"
    assert urls[-1].endswith(f"/v{expected}/pysme_gui/__init__.py")


def test_nothing_published_for_the_channel(published):
    published[0].extend(["0.2.39", "0.3.0a1"])
    envmanager.set_channel("stable")
    with pytest.raises(envmanager.NoRelease):
        envmanager.latest_gui()


def test_tag_whose_code_says_something_else_is_refused(published):
    tags, claimed, _ = published
    tags.append("0.3.0a2")
    claimed["v0.3.0a2"] = "0.3.0a1"
    envmanager.set_channel("alpha")
    with pytest.raises(RuntimeError, match="faulty"):
        envmanager.latest_gui()


def test_channel_comes_from_the_first_installation_and_then_stays(tmp_path, monkeypatch):
    monkeypatch.setenv("PYSME_GUI_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(envmanager, "smoke_test", lambda *a: {})

    def create_env(version, gui_id, lock=None, log=print):
        env_id = envmanager._new_id()
        os.makedirs(os.path.dirname(envmanager.env_python(env_id)))
        open(envmanager.env_python(env_id), "w").close()
        envmanager._write_json(os.path.join(envmanager.env_dir(env_id), "env.json"), {
            "pysme_version": version, "smelib_version": "x", "python_version": "3.12",
            "created": ""})
        return env_id

    monkeypatch.setattr(envmanager, "create_env", create_env)

    def source(version):
        package = tmp_path / version / "pysme_gui"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(
            f'__version__ = "{version}"\nPYSME_RECOMMENDED = "1.2.0"\n'
            'PYSME_MINIMUM = "1.2.0"\nPYSME_BELOW = "2.0"\n')
        return str(package.parent)

    envmanager.setup(source("0.3.0b1"), log=lambda t: None)
    assert envmanager.channel() == "beta"
    envmanager.setup(source("0.3.0"), log=lambda t: None)
    assert envmanager.channel() == "beta"
    with pytest.raises(RuntimeError, match="channel"):
        envmanager.set_channel("nightly")


def test_python_orders_versions_as_expected():
    assert sorted(reversed(ORDER), key=envmanager.version_key) == ORDER


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("swiftc"),
                    reason="needs the Swift compiler")
def test_the_mac_app_orders_versions_like_python(tmp_path):
    with open(os.path.join(REPO, "macos", "PySMEApp.swift"), encoding="utf-8") as handle:
        text = handle.read()
    part = text[text.index("// BEGIN version order"):text.index("// END version order")]
    listed = ", ".join(f'"{v}"' for v in ORDER)
    program = f"""import Foundation
{part}
let order = [{listed}]
for (i, a) in order.enumerated() {{
    for (j, b) in order.enumerated() where isOlder(a, than: b) != (i < j) {{
        print("wrong:", a, b)
    }}
}}
print("checked", order.count * order.count)
"""
    script = tmp_path / "order.swift"
    script.write_text(program)
    result = subprocess.run(["swift", str(script)], capture_output=True, text=True, timeout=180)
    assert result.stdout == f"checked {len(ORDER) ** 2}\n", result.stdout + result.stderr
