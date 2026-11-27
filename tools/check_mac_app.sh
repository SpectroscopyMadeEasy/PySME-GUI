#!/bin/bash
# Check a built "PySME.app" as a new user would meet it, without opening it:
# what it carries is this checkout's interface, and installing from it with its
# own "uv" gives a program that starts and synthesizes.
#
#   tools/check_mac_app.sh path/to/PySME.app
#
# Everything is installed into a temporary folder, also used as the home folder,
# so nothing of yours is touched. It downloads Python, PySME and a model grid.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$(cd "$1" && pwd)"
RES="$APP/Contents/Resources"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

codesign --verify --strict "$APP"
echo "ok      the signature matches the files"
file "$APP/Contents/MacOS/PySME" "$RES/bin/uv" | grep -c 'arm64' | grep -qx 2
echo "ok      built for Apple silicon"
# An app built earlier would install an earlier interface.
diff -r -x __pycache__ -x '*.pyc' "$ROOT/pysme_gui" "$RES/app/pysme_gui"
echo "ok      it carries the interface of this checkout"

# What the app does on its first launch (see PySMEApp.swift).
mkdir -p "$WORK/user home"
export HOME="$WORK/user home"
export PYSME_GUI_HOME="$WORK/PySME-GUI"
export PYSME_GUI_UV="$RES/bin/uv"
export UV_PYTHON_INSTALL_DIR="$PYSME_GUI_HOME/python"
export UV_CACHE_DIR="$PYSME_GUI_HOME/cache"
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MPLBACKEND=Agg
unset PYTHONHOME PYTHONPATH VIRTUAL_ENV CONDA_PREFIX
mkdir -p "$PYSME_GUI_HOME"
(cd "$PYSME_GUI_HOME" && "$PYSME_GUI_UV" run --no-project --managed-python --python 3.12 \
    "$RES/app/pysme_gui/envmanager.py" install --source "$RES/app" --bundle-id check)

chosen() {
    /usr/bin/python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["current"][sys.argv[2]])' \
        "$PYSME_GUI_HOME/current.json" "$1"
}
GUI_ID="$(chosen gui)"
ENV_ID="$(chosen env)"
cat > "$WORK/launch" <<LAUNCHER
#!/bin/sh
PYTHONSAFEPATH=1 PYTHONPATH="$PYSME_GUI_HOME/gui/$GUI_ID" \\
    exec "$PYSME_GUI_HOME/envs/$ENV_ID/venv/bin/python" -m pysme_gui "\$@"
LAUNCHER
chmod +x "$WORK/launch"
(cd "$WORK" && /usr/bin/python3 "$ROOT/tools/smoke.py" "$WORK/launch")

# Installing and running must not have written into the app.
codesign --verify --strict "$APP"
echo "ok      the app is unchanged afterwards"
