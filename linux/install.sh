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

mkdir -p "$DATA/bin" "$BIN" "$APPS"
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

cat > "$BIN/pysme-gui" <<LAUNCHER
#!/bin/sh
export PYSME_GUI_HOME="$DATA" PYSME_GUI_UV="$UV"
export UV_PYTHON_INSTALL_DIR="$DATA/python" UV_CACHE_DIR="$DATA/cache"
value() { [ -f "\$2" ] && sed -n "s/.*\"\$1\": *\"\([^\"]*\)\".*/\1/p" "\$2" | head -n 1; }
slot=\$(value current "$DATA/current.json")
python=\$(value python "$DATA/settings.json" || true)
[ -n "\$python" ] || python="$DATA/slots/\$slot/venv/bin/python"
PYTHONPATH="$DATA/slots/\$slot/app" exec "\$python" -m pysme_gui "\$@"
LAUNCHER
chmod +x "$BIN/pysme-gui"

cat > "$APPS/pysme-gui.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=PySME
Comment=Spectroscopy Made Easy
Exec=$BIN/pysme-gui %f
Icon=$ROOT/assets/pysme_logo-ver-light.png
Terminal=true
Categories=Science;Education;
DESKTOP

echo
echo "Done. Start it from the applications menu or run: $BIN/pysme-gui"
echo "It opens in your web browser; close the terminal window to quit."
