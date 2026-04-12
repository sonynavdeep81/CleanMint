#!/usr/bin/env bash
# install.sh — CleanMint Installer
# Works on Ubuntu 22.04+ / any Debian-based distro with Python 3.10+
#
# Usage:
#   bash install.sh          # install
#   bash install.sh --remove # uninstall

set -e

APP_NAME="CleanMint"
INSTALL_DIR="$HOME/.local/share/cleanmint"
BIN_LINK="$HOME/.local/bin/cleanmint"
DESKTOP_FILE="$HOME/.local/share/applications/cleanmint.desktop"
POLICY_DEST="/usr/share/polkit-1/actions/org.cleanmint.policy"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$SCRIPT_DIR/cleanmint"

# ── Colours ──────────────────────────────────────────────────────
GREEN="\033[92m"; YELLOW="\033[93m"; RED="\033[91m"; RESET="\033[0m"
ok()   { echo -e "${GREEN}  ✓ $*${RESET}"; }
info() { echo -e "${YELLOW}  → $*${RESET}"; }
fail() { echo -e "${RED}  ✗ $*${RESET}"; exit 1; }

# ── Uninstall ────────────────────────────────────────────────────
if [[ "$1" == "--remove" ]]; then
    echo -e "\n${YELLOW}Uninstalling $APP_NAME…${RESET}"
    rm -rf "$INSTALL_DIR"
    rm -f  "$BIN_LINK"
    rm -f  "$DESKTOP_FILE"
    # Remove icons
    for size in 16 32 48 64 128 256 512; do
        rm -f "$HOME/.local/share/icons/hicolor/${size}x${size}/apps/cleanmint.png"
    done
    rm -f "$HOME/.local/share/icons/hicolor/scalable/apps/cleanmint.svg"
    gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor/" 2>/dev/null || true
    update-desktop-database "$HOME/.local/share/applications/" 2>/dev/null || true
    echo "Polkit policy not removed automatically (needs sudo)."
    echo "To fully remove, run: sudo rm $POLICY_DEST"
    ok "CleanMint uninstalled."
    exit 0
fi

echo -e "\n╔══════════════════════════════════════╗"
echo    "║   CleanMint — Installer               ║"
echo -e "╚══════════════════════════════════════╝\n"

# ── 1. Check Python ──────────────────────────────────────────────
info "Checking Python 3.10+…"
PYTHON=""
for py in python3.12 python3.11 python3.10 python3; do
    if command -v "$py" &>/dev/null; then
        VER=$("$py" -c "import sys; print(sys.version_info >= (3,10))")
        if [[ "$VER" == "True" ]]; then
            PYTHON="$py"
            break
        fi
    fi
done
[[ -z "$PYTHON" ]] && fail "Python 3.10+ not found. Install with: sudo apt install python3"
ok "Found $($PYTHON --version)"

# ── 2. Check pip / venv module ───────────────────────────────────
info "Checking pip and venv…"
$PYTHON -m venv --help &>/dev/null || fail "venv module missing. Run: sudo apt install python3-venv"
ok "venv available"

# ── 3. Copy app files ────────────────────────────────────────────
info "Installing app files to $INSTALL_DIR…"
mkdir -p "$INSTALL_DIR"
rsync -a --exclude='__pycache__' --exclude='*.pyc' --exclude='venv/' \
    "$SOURCE_DIR/" "$INSTALL_DIR/" 2>/dev/null \
    || cp -r "$SOURCE_DIR"/. "$INSTALL_DIR/"
ok "App files copied"

# ── 4. Create virtual environment ───────────────────────────────
info "Creating Python virtual environment…"
$PYTHON -m venv "$INSTALL_DIR/venv"
ok "venv created"

# ── 5. Install Python dependencies ──────────────────────────────
info "Installing dependencies (PyQt6, psutil, reportlab, send2trash)…"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"
ok "Dependencies installed"

# ── 6. Install polkit policy (one-time privilege setup) ─────────
info "Installing polkit policy (will ask for your password once)…"
POLICY_SRC="$INSTALL_DIR/assets/org.cleanmint.policy"
if [[ -f "$POLICY_SRC" ]]; then
    if cmp -s "$POLICY_SRC" "$POLICY_DEST" 2>/dev/null; then
        ok "Polkit policy already up to date"
    else
        pkexec /usr/bin/tee "$POLICY_DEST" < "$POLICY_SRC" > /dev/null \
            && ok "Polkit policy installed (journal, snap, apt-get, systemctl)" \
            || echo -e "${YELLOW}  ⚠ Could not install policy automatically.${RESET}
     Run manually: sudo cp \"$POLICY_SRC\" \"$POLICY_DEST\""
    fi
else
    echo -e "${YELLOW}  ⚠ Policy file not found — skipping${RESET}"
fi

# ── 7. Create launcher script ────────────────────────────────────
info "Creating launcher…"
mkdir -p "$HOME/.local/bin"
cat > "$BIN_LINK" << EOF
#!/usr/bin/env bash
exec "$INSTALL_DIR/venv/bin/python" "$INSTALL_DIR/main.py" "\$@"
EOF
chmod +x "$BIN_LINK"
ok "Launcher created at $BIN_LINK"

# ── 8. Install app icons ─────────────────────────────────────────
info "Installing app icons…"
for size in 16 32 48 64 128 256 512; do
    icon_dir="$HOME/.local/share/icons/hicolor/${size}x${size}/apps"
    mkdir -p "$icon_dir"
    src="$INSTALL_DIR/assets/icons/cleanmint_${size}.png"
    [[ -f "$src" ]] && cp "$src" "$icon_dir/cleanmint.png"
done
svg_src="$INSTALL_DIR/assets/icons/cleanmint.svg"
if [[ -f "$svg_src" ]]; then
    mkdir -p "$HOME/.local/share/icons/hicolor/scalable/apps"
    cp "$svg_src" "$HOME/.local/share/icons/hicolor/scalable/apps/cleanmint.svg"
fi
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor/" 2>/dev/null || true
ok "Icons installed"

# ── 9. Install desktop entry ─────────────────────────────────────
info "Installing app launcher entry…"
mkdir -p "$HOME/.local/share/applications"
cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=CleanMint
Comment=Linux System Cleaner for Ubuntu
Exec=$BIN_LINK
Icon=cleanmint
Terminal=false
Categories=System;Utility;
Keywords=clean;disk;junk;cache;optimize;system;
StartupWMClass=cleanmint
EOF
update-desktop-database "$HOME/.local/share/applications/" 2>/dev/null || true
ok "App launcher entry installed — search 'CleanMint' in your app launcher"

# ── Done ─────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════╗"
echo    "║   CleanMint installed successfully!   ║"
echo -e "╚══════════════════════════════════════╝${RESET}"
echo ""
echo "  Launch: press Super key, search 'CleanMint', click the icon."
echo "  Or run:  cleanmint"
echo ""
echo "  To uninstall: bash install.sh --remove"
echo ""
