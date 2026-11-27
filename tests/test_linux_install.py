"""The Linux installer and the launcher it writes use nothing from a folder that
others can write into. Run with a stand-in for "uv", so nothing is installed."""

import os
import stat
import subprocess

import pytest

grp = pytest.importorskip("grp", reason="a POSIX shell script")    # not on Windows

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALLER = os.path.join(REPO, "linux", "install.sh")


def script(path, body):
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return str(path)


@pytest.fixture
def machine(tmp_path):
    """An empty computer; ``run`` runs a shell script on it."""
    class Machine:
        data = tmp_path / "data folder"             # a space, as users have
        bin = tmp_path / "bin"
        used = tmp_path / "uv-was-run"
        ran = tmp_path / "python-was-run"
        tools = tmp_path / "tools"

    machine = Machine()
    machine.tools.mkdir()
    (tmp_path / "home").mkdir()

    def run(command, uv=None):
        env = {
            "PATH": f"{machine.tools}:/usr/bin:/bin", "HOME": str(tmp_path / "home"),
            "PYSME_GUI_HOME": str(machine.data), "PYSME_GUI_BIN": str(machine.bin),
            "XDG_DATA_HOME": str(tmp_path / "share"),
        }
        if uv:
            env["PYSME_GUI_UV"] = uv
        return subprocess.run(["/bin/sh", *command], env=env, capture_output=True, text=True,
                              timeout=60)

    machine.run = run
    machine.uv = script(tmp_path / "uv", f'echo x > "{machine.used}"\n')
    return machine


def planted_uv(machine):
    (machine.data / "bin").mkdir(parents=True)
    script(machine.data / "bin" / "uv", f'echo x > "{machine.used}"\n')


def say_the_group_is(machine, name):
    """Make ``id -un`` answer ``name``: the user's own group bears the user's name."""
    script(machine.tools / "id", f'echo "{name}"\n')


@pytest.mark.parametrize("mode", [0o777, 0o757])
def test_nothing_is_run_from_a_folder_that_anyone_can_write_into(machine, mode):
    planted_uv(machine)
    machine.data.chmod(mode)
    result = machine.run([INSTALLER])
    assert result.returncode != 0 and "others can write into it" in result.stderr
    assert not machine.used.exists()


def test_a_folder_a_shared_group_can_write_into_is_refused_too(machine):
    planted_uv(machine)
    machine.data.chmod(0o775)
    say_the_group_is(machine, "somebody-else")
    result = machine.run([INSTALLER])
    assert result.returncode != 0 and not machine.used.exists()


@pytest.mark.parametrize("mode", [0o700, 0o755, 0o775])
def test_ones_own_folder_is_used_and_then_closed(machine, mode):
    planted_uv(machine)
    machine.data.chmod(mode)
    if mode & 0o020:        # writable by the group that bears the user's name
        say_the_group_is(machine, grp.getgrgid(machine.data.stat().st_gid).gr_name)
    machine.run([INSTALLER])
    assert machine.used.exists()
    assert stat.S_IMODE(machine.data.stat().st_mode) == 0o700


def test_a_link_is_judged_by_where_it_leads(machine, tmp_path):
    target = tmp_path / "shared place"
    (target / "bin").mkdir(parents=True)
    script(target / "bin" / "uv", f'echo x > "{machine.used}"\n')
    target.chmod(0o777)
    machine.data.symlink_to(target)
    assert machine.run([INSTALLER]).returncode != 0 and not machine.used.exists()


def test_the_launcher_looks_before_it_uses_anything(machine):
    assert machine.run([INSTALLER], uv=machine.uv).returncode == 0
    launcher = machine.bin / "pysme-gui"
    python = script(machine.tools / "python", f'echo x > "{machine.ran}"\n')
    (machine.data / "current.sh").write_text("gui=g\nenv=e\n")
    (machine.data / "settings.json").write_text(f'{{"python": "{python}"}}')
    assert machine.run([str(launcher)]).returncode == 0 and machine.ran.exists()
    machine.ran.unlink()
    machine.data.chmod(0o777)
    result = machine.run([str(launcher)])
    assert result.returncode != 0 and "others can write into it" in result.stderr
    assert not machine.ran.exists()
