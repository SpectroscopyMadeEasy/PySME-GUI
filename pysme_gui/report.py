"""Problem reports: one plain-text file that a user can pass on.

Nothing is sent from here. The text is shown to the user, who may edit it,
then saves it and passes it on. Spectra and line lists are never included.
"""

import locale
import os
import platform
import re
import subprocess
import sys
import time
from importlib import metadata
from urllib.parse import urlencode

from . import __version__, envmanager

LOG_LINES = 400
APP_LOG_LINES = 150
PACKAGES = ("pysme-astro", "numpy", "scipy", "astropy")
SETTINGS = ("teff", "logg", "monh", "vmic", "vmac", "vsini", "vrad", "ipres", "iptype")
_VALUES = re.compile(r"(Synthesizing (?:residual|jacobian) with values ).*")
_OTHER_HOME = re.compile(r"(/Users/|/home/|[A-Za-z]:[\\/]Users[\\/])(?!Shared\b|<user>)[^\\/\n'\":]+")
_TOKEN = re.compile(r"(token=|X-PySME-Token['\"]?:\s*['\"]?)[A-Za-z0-9_\-]+")


def redact(text, home=None):
    """Remove user names from paths, and the access token."""
    home = home or os.path.expanduser("~")
    variants = set()
    for path in (home, os.path.realpath(home)):
        variants.update({path, path.replace("\\", "/")})
    for path in sorted(variants, key=len, reverse=True):
        if len(path.rstrip("/\\")) > 1:
            text = re.sub(re.escape(path) + r"(?=$|[\\/\s'\":,;)\]])", "~", text, flags=re.M)
    text = _OTHER_HOME.sub(r"\1<user>", text)
    return _TOKEN.sub(r"\1<hidden>", text)


def _safe(getter):
    """A report must still be produced when part of the program is broken."""
    try:
        return getter()
    except Exception as exc:
        return f"unavailable ({type(exc).__name__}: {exc})"


def _tail(path, count, limit=200_000):
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(handle.tell() - limit, 0))
            return handle.read().decode("utf-8", "replace").splitlines()[-count:]
    except OSError:
        return []


def _ascii(path):
    return "yes" if str(path).isascii() else "NO"


def system_name():
    if sys.platform == "darwin":
        return f"macOS {platform.mac_ver()[0]}"
    return f"{platform.system()} {platform.release()}"


def _system():
    home = os.path.expanduser("~")
    rows = [
        ("System", _safe(platform.platform)),
        ("Machine", platform.machine()),
        ("Locale", _safe(lambda: ".".join(str(x) for x in locale.getlocale()))),
        ("Text encoding", _safe(lambda: locale.getpreferredencoding(False))),
        ("File name encoding", sys.getfilesystemencoding()),
        ("LANG / LC_ALL", f"{os.environ.get('LANG', '-')} / {os.environ.get('LC_ALL', '-')}"),
        # Paths are redacted below, so record separately what redaction hides.
        ("Home path is plain ASCII", _ascii(home)),
        ("App data path is plain ASCII", _ascii(envmanager.app_home())),
        ("Python path is plain ASCII", _ascii(sys.executable)),
    ]
    if sys.platform == "win32":
        import ctypes

        rows.append(("Windows code page", _safe(lambda: ctypes.windll.kernel32.GetACP())))
    return rows


def _versions(smelib):
    rows = [("PySME-GUI", __version__), ("SMElib", smelib), ("Python", sys.version.split()[0])]
    rows += [(name, _safe(lambda n=name: metadata.version(n))) for name in PACKAGES]
    return rows


def _installation(managed, update_task):
    record = envmanager.current_record()
    rows = [
        ("Python used", sys.executable),
        ("App's own environment", "yes" if managed else "no"),
        ("App data", envmanager.app_home()),
        ("Custom Python setting", envmanager.settings().get("python") or "-"),
        ("In use", record.get("current") or "-"),
        ("Update channel", envmanager.channel()),
        ("Interface installed", envmanager.gui_info(envmanager.selection(record)[0]) or "-"),
        ("PySME installations", ", ".join(
            f"{i['pysme_version']} ({i['created']})" for i in envmanager.installed_envs()
        ) or "-"),
        ("Update task", update_task),
    ]
    return rows


def _settings(session):
    sme = session.sme
    rows = [(name, _safe(lambda n=name: getattr(sme, n))) for name in SETTINGS]
    rows += [
        ("Atmosphere grid", _safe(lambda: sme.atmo.source)),
        ("Wavelength ranges", session.ranges),
        ("Step", session.step),
        ("Free parameters", _safe(lambda: list(sme.fitparameters or []))),
        ("NLTE elements", _safe(lambda: list(sme.nlte.elements))),
        ("Lines in line list", _safe(lambda: 0 if sme.linelist is None else len(sme.linelist))),
        (
            "Observed pixels",
            _safe(lambda: sum(len(s["wave"]) for s in session.obs) if session.obs else 0),
        ),
    ]
    return rows


def build(description, session, job, log_lines, smelib, managed, update_task,
          client=None, include_settings=False):
    """Return the report text. ``session`` must not change while this runs."""
    out = [
        "PySME-GUI problem report",
        "Created: " + time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "",
        "== What happened ==",
        (description or "").strip() or "(not described)",
    ]

    def section(title, rows):
        out.extend(["", f"== {title} =="])
        out.extend(f"{key}: {value}" for key, value in rows)

    section("Versions", _safe_rows(lambda: _versions(smelib)))
    section("System", _safe_rows(_system))
    section("Installation", _safe_rows(lambda: _installation(managed, update_task)))
    # While fitting, the progress text holds the values being tried.
    section("Last run", [
        (k, v if include_settings or k != "progress" or not v else "(hidden)")
        for k, v in job.items() if k in ("state", "kind", "elapsed", "error", "progress")
    ])
    if include_settings:
        section("Current settings", _safe_rows(lambda: _settings(session)))

    client = client or {}
    out.extend(["", "== Window =="])
    out.append("Browser: " + str(client.get("agent", "-"))[:300])
    out.extend("Error shown: " + str(e)[:1000] for e in list(client.get("errors") or [])[-20:])

    log = list(log_lines)[-LOG_LINES:]
    app_log = _tail(os.path.join(envmanager.app_home(), "logs", "app.log"), APP_LOG_LINES)
    if not include_settings:
        log = [_VALUES.sub(r"\1(hidden)", line) for line in log]
        app_log = [_VALUES.sub(r"\1(hidden)", line) for line in app_log]
    out.extend(["", "== Log of this session =="] + (log or ["(empty)"]))
    if app_log:
        out.extend(["", "== Application log (may include earlier sessions) =="] + app_log)
    return redact("\n".join(out) + "\n")


def _safe_rows(getter):
    try:
        return getter()
    except Exception as exc:
        return [("unavailable", f"{type(exc).__name__}: {exc}")]


def save(text, now=None):
    """Write the report where a user can find it again; return the path."""
    stem = "PySME-report-" + time.strftime("%Y%m%d-%H%M%S", now or time.localtime())
    home = os.path.expanduser("~")
    folders = [os.path.join(home, "Downloads"), home, os.path.join(envmanager.app_home(), "reports")]
    failure = None
    for index, folder in enumerate(folders):
        if index == 2:
            os.makedirs(folder, exist_ok=True)
        if not os.path.isdir(folder):
            continue
        for number in range(1, 100):
            name = f"{stem}.txt" if number == 1 else f"{stem}-{number}.txt"
            path = os.path.join(folder, name)
            try:
                # utf-8-sig: old Windows editors need the mark to show Chinese.
                # Only this user may read it (the mode means nothing on Windows).
                with open(path, "x", encoding="utf-8-sig", errors="replace",
                          opener=lambda name, flags: os.open(name, flags, 0o600)) as handle:
                    handle.write(text)
                return path
            except FileExistsError:
                continue
            except OSError as exc:
                failure = exc
                break
    raise OSError(f"Could not save the report: {failure}")


def _version_only(name, value):
    """A version for a public address, never other text: where a part of the
    program is broken, what stands there is an error message, maybe with paths."""
    value = str(value)
    if name == "SMElib":                    # "SME Library version: 6.13.23, darwin [...]"
        found = re.search(r"version:?\s*(\d+(?:\.\d+)+)", value)
        value = found.group(1) if found else value
    return value if re.fullmatch(r"\d+(\.\d+)+[0-9A-Za-z.+\-]*", value) else "unknown"


def issue_url(repo, subject, filename, smelib):
    """A pre-filled new-issue page.

    Only versions and the system go into the address. What the user wrote and
    the log stay in the file, which they attach themselves.
    """
    versions = [f"{key}: {_version_only(key, value)}"
                for key, value in _safe_rows(lambda: _versions(smelib))
                if key in ("PySME-GUI", "SMElib", "Python", "pysme-astro")]
    body = "\n".join(
        ["**What happened?**", "", "",
         f"**Report file:** please drag `{filename}` into this box. Issues are public.", ""]
        + versions + [f"System: {system_name()} ({platform.machine()})"]
    )
    return f"https://github.com/{repo}/issues/new?" + urlencode({"title": subject, "body": body})


def reveal(path):
    """Show the saved file in the system's file manager."""
    if sys.platform == "darwin":
        command = ["open", "-R", path]
    elif sys.platform == "win32":
        command = ["explorer", "/select,", os.path.normpath(path)]
    else:
        command = ["xdg-open", os.path.dirname(path)]
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
