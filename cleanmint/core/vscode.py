"""
core/vscode.py — VS Code Profile Engine

Reads installed extensions and user settings from VS Code,
and generates a portable restore script.

Works even when VS Code is uninstalled — falls back to reading
~/.vscode/extensions/ and ~/.config/Code/User/ directly from disk.
"""

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class VSCodeExtension:
    ext_id: str        # e.g. ms-python.python
    publisher: str     # e.g. ms-python
    name: str          # e.g. python
    version: str       # e.g. 2024.0.1


@dataclass
class VSCodeProfile:
    extensions: list[VSCodeExtension]
    settings_text: str
    keybindings_text: str
    # How the data was loaded
    source: str        # "cli" | "disk" | "none"


def _vscode_user_dir() -> Path:
    """Return the VS Code User config directory."""
    return Path.home() / ".config" / "Code" / "User"


def _vscode_extensions_dir() -> Path:
    """Return the directory where VS Code stores extension folders."""
    return Path.home() / ".vscode" / "extensions"


def is_cli_available() -> bool:
    """Return True if the `code` CLI is on PATH."""
    try:
        return subprocess.run(
            ["which", "code"],
            capture_output=True,
        ).returncode == 0
    except Exception:
        return False


def is_data_available() -> bool:
    """
    Return True if any VS Code data exists on this machine —
    either the CLI is present OR the extensions/config folders exist.
    Covers the case where VS Code was uninstalled but data remains.
    """
    if is_cli_available():
        return True
    return (
        _vscode_extensions_dir().exists()
        or _vscode_user_dir().exists()
    )


def _extensions_from_cli() -> list[VSCodeExtension]:
    """Ask the `code` CLI for the installed extension list."""
    result = subprocess.run(
        ["code", "--list-extensions", "--show-versions"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    extensions = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # Format: publisher.name@version  OR  publisher.name
        if "@" in line:
            ext_id, version = line.rsplit("@", 1)
        else:
            ext_id, version = line, ""
        parts = ext_id.split(".", 1)
        publisher = parts[0] if len(parts) == 2 else ""
        name      = parts[1] if len(parts) == 2 else ext_id
        extensions.append(VSCodeExtension(
            ext_id=ext_id, publisher=publisher,
            name=name, version=version,
        ))
    return extensions


def _extensions_from_disk() -> list[VSCodeExtension]:
    """
    Read extensions directly from ~/.vscode/extensions/.
    Each extension folder contains a package.json with publisher/name/version.
    Works even when VS Code itself is uninstalled.
    """
    ext_dir = _vscode_extensions_dir()
    if not ext_dir.exists():
        return []

    extensions = []
    seen: set[str] = set()

    for pkg_json in ext_dir.glob("*/package.json"):
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8"))
            publisher = data.get("publisher", "")
            name      = data.get("name", "")
            version   = data.get("version", "")
            if not publisher or not name:
                continue
            ext_id = f"{publisher}.{name}"
            # Skip duplicates (VS Code keeps old versions during updates)
            if ext_id in seen:
                continue
            seen.add(ext_id)
            extensions.append(VSCodeExtension(
                ext_id=ext_id, publisher=publisher,
                name=name, version=version,
            ))
        except Exception:
            continue

    return extensions


def load_profile() -> VSCodeProfile:
    """
    Load the full VS Code profile (extensions + settings + keybindings).

    Strategy:
      1. If `code` CLI is available → ask it for the extension list (most accurate)
      2. If not → read ~/.vscode/extensions/*/package.json directly from disk
      3. Settings/keybindings are always read from disk (they stay after uninstall)
    """
    settings_text    = get_settings_text()
    keybindings_text = get_keybindings_text()

    if is_cli_available():
        try:
            exts = _extensions_from_cli()
            source = "cli"
        except Exception:
            exts   = _extensions_from_disk()
            source = "disk"
    elif _vscode_extensions_dir().exists():
        exts   = _extensions_from_disk()
        source = "disk"
    else:
        exts   = []
        source = "none"

    exts = sorted(exts, key=lambda e: (e.publisher.lower(), e.name.lower()))
    return VSCodeProfile(
        extensions=exts,
        settings_text=settings_text,
        keybindings_text=keybindings_text,
        source=source,
    )


# Keep these as standalone helpers (used by generate_restore_script)

def get_settings_text() -> str:
    """Return the raw text of settings.json, or an empty string if missing."""
    path = _vscode_user_dir() / "settings.json"
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except Exception as e:
        return f"// Error reading settings.json: {e}"


def get_keybindings_text() -> str:
    """Return the raw text of keybindings.json, or an empty string if missing."""
    path = _vscode_user_dir() / "keybindings.json"
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except Exception as e:
        return f"// Error reading keybindings.json: {e}"


def get_settings_text() -> str:
    """Return the raw text of settings.json, or an empty string if missing."""
    path = _vscode_user_dir() / "settings.json"
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except Exception as e:
        return f"// Error reading settings.json: {e}"


def get_keybindings_text() -> str:
    """Return the raw text of keybindings.json, or an empty string if missing."""
    path = _vscode_user_dir() / "keybindings.json"
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except Exception as e:
        return f"// Error reading keybindings.json: {e}"


def generate_restore_script(
    extensions: list[VSCodeExtension],
    dest: Path,
    include_settings: bool = True,
    include_keybindings: bool = True,
) -> None:
    """
    Write a portable shell script to `dest` that:
    - Installs VS Code if missing
    - Installs all extensions via `code --install-extension`
    - Copies settings.json and keybindings.json into place
    """
    settings_text = get_settings_text() if include_settings else ""
    keybindings_text = get_keybindings_text() if include_keybindings else ""

    lines = [
        "#!/usr/bin/env bash",
        "# VS Code Restore Script — generated by CleanMint",
        "# Run this on any fresh Ubuntu/Debian machine:",
        "#   bash vscode_restore.sh",
        "",
        'set -e',
        "",
        "# ── 1. Install VS Code if not present ──────────────────────────────",
        'if ! command -v code &>/dev/null; then',
        '    echo "Installing VS Code…"',
        '    wget -qO /tmp/vscode.deb '
        '"https://code.visualstudio.com/sha/download?build=stable&os=linux-deb-x64"',
        '    sudo dpkg -i /tmp/vscode.deb || sudo apt-get install -f -y',
        '    rm /tmp/vscode.deb',
        'fi',
        "",
        "# ── 2. Install extensions ───────────────────────────────────────────",
        f'echo "Installing {len(extensions)} extensions…"',
    ]

    for ext in extensions:
        lines.append(
            f'code --install-extension "{ext.ext_id}" --force || true'
        )

    lines += [
        "",
        "# ── 3. Restore settings ─────────────────────────────────────────────",
        'VSCODE_USER="$HOME/.config/Code/User"',
        'mkdir -p "$VSCODE_USER"',
        "",
    ]

    if settings_text:
        # Embed settings.json as a heredoc
        lines += [
            'cat > "$VSCODE_USER/settings.json" << \'SETTINGS_EOF\'',
            settings_text.rstrip(),
            "SETTINGS_EOF",
            "",
        ]
    else:
        lines.append('# settings.json was empty — skipping')
        lines.append("")

    if keybindings_text:
        lines += [
            'cat > "$VSCODE_USER/keybindings.json" << \'KEYBINDINGS_EOF\'',
            keybindings_text.rstrip(),
            "KEYBINDINGS_EOF",
            "",
        ]
    else:
        lines.append('# keybindings.json was empty — skipping')
        lines.append("")

    lines += [
        'echo ""',
        'echo "VS Code restore complete!"',
        'echo "  Extensions installed: ' + str(len(extensions)) + '"',
        'echo "  Restart VS Code to apply settings."',
    ]

    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    dest.chmod(0o755)
