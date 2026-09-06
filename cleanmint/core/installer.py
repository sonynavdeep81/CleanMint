"""
core/installer.py — CleanMint Polkit Policy + Helper Installer

Installs two files (both need root):
  1. /usr/local/lib/cleanmint/cleanmint-helper   — privileged helper script
  2. /usr/share/polkit-1/actions/org.cleanmint.policy — single polkit action

Having ONE polkit action for the helper means ONE password prompt covers
all privileged operations (journal, snap, apt-get, systemctl).
"""

import base64
import hashlib
import subprocess
from pathlib import Path

ASSETS        = Path(__file__).parent.parent / "assets"
POLICY_SRC    = ASSETS / "org.cleanmint.policy"
HELPER_SRC    = ASSETS / "cleanmint-helper"
POLICY_DEST   = Path("/usr/share/polkit-1/actions/org.cleanmint.policy")
HELPER_DEST   = Path("/usr/local/lib/cleanmint/cleanmint-helper")


def policy_signature() -> str:
    """Short hash of the current policy + helper source assets.

    Used to remember which version of the prompt a user has already
    answered, so a declined or failing install is not re-offered on every
    launch — only once per new version of the assets.
    """
    h = hashlib.sha256()
    for src in (POLICY_SRC, HELPER_SRC):
        try:
            h.update(src.read_bytes())
        except OSError:
            h.update(b"<missing>")
    return h.hexdigest()[:16]


def is_policy_installed() -> bool:
    """Return True if both the policy and helper are installed and up to date."""
    try:
        policy_ok = (POLICY_DEST.exists() and
                     POLICY_DEST.read_bytes() == POLICY_SRC.read_bytes())
        helper_ok = (HELPER_DEST.exists() and
                     HELPER_DEST.read_bytes() == HELPER_SRC.read_bytes())
        return policy_ok and helper_ok
    except OSError:
        return False


_INSTALL_SCRIPT = (
    "set -e; "
    "read h; read p; "
    f'mkdir -p "{HELPER_DEST.parent}"; '
    f'printf %s "$h" | base64 -d > "{HELPER_DEST}"; chmod 755 "{HELPER_DEST}"; '
    f'mkdir -p "{POLICY_DEST.parent}"; '
    f'printf %s "$p" | base64 -d > "{POLICY_DEST}"; chmod 644 "{POLICY_DEST}"'
)


def install_policy() -> tuple[bool, str]:
    """
    Install the helper script and polkit policy in a SINGLE pkexec call —
    one password prompt writes both files. Returns (success, message).
    """
    if not POLICY_SRC.exists():
        return False, f"Policy source not found: {POLICY_SRC}"
    if not HELPER_SRC.exists():
        return False, f"Helper source not found: {HELPER_SRC}"

    payload = (base64.b64encode(HELPER_SRC.read_bytes()) + b"\n"
               + base64.b64encode(POLICY_SRC.read_bytes()) + b"\n")
    try:
        r = subprocess.run(
            ["pkexec", "/bin/sh", "-c", _INSTALL_SCRIPT],
            input=payload, capture_output=True, timeout=120,
        )
    except FileNotFoundError:
        return False, "pkexec not found."
    except subprocess.TimeoutExpired:
        return False, "pkexec timed out."
    except Exception as e:  # noqa: BLE001
        return False, str(e)

    if r.returncode == 0:
        return True, "CleanMint helper and policy installed successfully."

    err = r.stderr.decode(errors="replace").strip()
    low = err.lower()
    if "dismissed" in low or "not authorized" in low or r.returncode == 126:
        return False, "Password prompt was cancelled."
    return False, err or f"pkexec exited {r.returncode}"


def uninstall_policy() -> tuple[bool, str]:
    """Remove the helper and policy file in one pkexec call (requires root)."""
    try:
        r = subprocess.run(
            ["pkexec", "/bin/sh", "-c",
             f'rm -f "{HELPER_DEST}" "{POLICY_DEST}"'],
            capture_output=True, timeout=60,
        )
    except Exception as e:  # noqa: BLE001
        return False, str(e)
    if r.returncode == 0:
        return True, "CleanMint helper and policy removed."
    return False, (r.stderr.decode(errors="replace").strip()
                   or f"pkexec exited {r.returncode}")
