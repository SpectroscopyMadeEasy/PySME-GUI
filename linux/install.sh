#!/bin/sh
# Install PySME-GUI for the current user on Linux (x86_64). No root needed.
#
#   linux/install.sh            install, or reinstall from this checkout
#   linux/install.sh --uninstall
#
# Python and PySME go into ~/.local/share/pysme-gui, a "pysme-gui" command into
# ~/.local/bin, and an entry into the applications menu.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
DATA=${PYSME_GUI_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/pysme-gui}
BIN=${PYSME_GUI_BIN:-$HOME/.local/bin}
APPS=${XDG_DATA_HOME:-$HOME/.local/share}/applications
UV_VERSION=0.13.10

if [ "${1:-}" = "--uninstall" ]; then
    rm -rf "$DATA" "$BIN/pysme-gui" "$APPS/pysme-gui.desktop"
    echo "Removed PySME-GUI. Model data in ~/.sme was kept."
    exit 0
fi

# Programs are run from the app's folder. Before anything in it is used, it has
# to be this user's own, with nobody else able to write into it. The launcher
# gets the same check.
CHECK_FOLDER=$(cat <<'CHECK'
safe_folder() {
    [ -e "$1" ] || return 0
    if [ ! -O "$1" ]; then
        echo "PySME-GUI runs programs from $1, which belongs to another user." >&2
        return 1
    fi
    # "/." so that what a link leads to is judged. Writing by the group is let
    # through for the user's own group (named like the user), as many Linux
    # systems set it up.
    if ! ls -ld "$1/." | awk -v me="$(id -un)" '{
            exit (substr($1, 9, 1) == "w" || (substr($1, 6, 1) == "w" && $4 != me)) ? 1 : 0 }'; then
        echo "PySME-GUI runs programs from $1, but others can write into it." >&2
        echo "Make sure nobody has changed what is in it (or remove it), then run:" >&2
        echo "    chmod go-w \"$1\"" >&2
        return 1
    fi
}
CHECK
)
eval "$CHECK_FOLDER"
safe_folder "$DATA" || exit 1

mkdir -p "$DATA/bin" "$BIN" "$APPS"
chmod 700 "$DATA"
UV=${PYSME_GUI_UV:-}
[ -n "$UV" ] || UV=$(command -v uv || true)
if [ -z "$UV" ]; then
    UV="$DATA/bin/uv"
    if [ ! -x "$UV" ]; then
        name=uv-x86_64-unknown-linux-gnu
        url="https://github.com/astral-sh/uv/releases/download/$UV_VERSION/$name.tar.gz"
        work=$(mktemp -d)
        echo "Downloading uv $UV_VERSION"
        curl -fsSL "$url" -o "$work/$name.tar.gz"
        curl -fsSL "$url.sha256" -o "$work/$name.sha256"
        (cd "$work" && sha256sum -c "$name.sha256" >/dev/null)
        tar -xzf "$work/$name.tar.gz" -C "$work"
        mv "$work/$name/uv" "$UV"
        rm -rf "$work"
    fi
fi

export PYSME_GUI_HOME="$DATA" PYSME_GUI_UV="$UV"
export UV_PYTHON_INSTALL_DIR="$DATA/python" UV_CACHE_DIR="$DATA/cache"
PYTHONDONTWRITEBYTECODE=1 "$UV" run --no-project --managed-python --python 3.12 \
    "$ROOT/pysme_gui/envmanager.py" install --source "$ROOT"

cp "$ROOT/assets/pysme-icon.png" "$DATA/icon.png"      # stays if the download is removed

cat > "$BIN/pysme-gui" <<LAUNCHER
#!/bin/sh
$CHECK_FOLDER
safe_folder "$DATA" || exit 1
# The menu entry has no terminal: what would be printed there goes to a file.
if [ "\${1:-}" = "--from-menu" ]; then
    shift
    # Only this user may read the log; it names files and folders.
    ( umask 077; : >> "$DATA/pysme-gui.log" ) && chmod 600 "$DATA/pysme-gui.log"
    exec >> "$DATA/pysme-gui.log" 2>&1
fi
export PYSME_GUI_HOME="$DATA" PYSME_GUI_UV="$UV"
export UV_PYTHON_INSTALL_DIR="$DATA/python" UV_CACHE_DIR="$DATA/cache"
value() { [ -f "\$2" ] && sed -n "s/.*\"\$1\": *\"\([^\"]*\)\".*/\1/p" "\$2" | head -n 1; }
gui=\$(sed -n 's/^gui=//p' "$DATA/current.sh")
env=\$(sed -n 's/^env=//p' "$DATA/current.sh")
python=\$(value python "$DATA/settings.json" || true)
[ -n "\$python" ] || python="$DATA/envs/\$env/venv/bin/python"
# Without PYTHONSAFEPATH, -m looks in the current folder before PYTHONPATH.
PYTHONSAFEPATH=1 PYTHONPATH="$DATA/gui/\$gui" exec "\$python" -m pysme_gui "\$@"
LAUNCHER
chmod +x "$BIN/pysme-gui"

cat > "$APPS/pysme-gui.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=PySME
Comment=Spectroscopy Made Easy
Exec="$BIN/pysme-gui" --from-menu %f
Icon=$DATA/icon.png
Terminal=false
Categories=Science;Education;
DESKTOP

echo
echo "Done. Start it from the applications menu or run: $BIN/pysme-gui"
echo "It opens in your web browser. To quit: ⋯ → Quit PySME. Closing the tab leaves"
echo "it running, and starting PySME again shows it as you left it."
