"""
core/installer.py — CleanMint Polkit Policy + Helper Installer

Installs two files (both need root):
  1. /usr/local/lib/cleanmint/cleanmint-helper   — privileged helper script
  2. /usr/share/polkit-1/actions/org.cleanmint.policy — single polkit action

Having ONE polkit action for the helper means ONE password prompt covers
all privileged operations (journal, snap, apt-get, systemctl).
"""

import subprocess
from pathlib import Path

ASSETS        = Path(__file__).parent.parent / "assets"
POLICY_SRC    = ASSETS / "org.cleanmint.policy"
HELPER_SRC    = ASSETS / "cleanmint-helper"
POLICY_DEST   = Path("/usr/share/polkit-1/actions/org.cleanmint.policy")
HELPER_DEST   = Path("/usr/local/lib/cleanmint/cleanmint-helper")


def _write_as_root(src: Path, dest: Path) -> tuple[bool, str]:
    """Write src to dest using pkexec tee. Returns (ok, error_msg)."""
    try:
        # Ensure parent directory exists
        subprocess.run(
            ["pkexec", "/bin/mkdir", "-p", str(dest.parent)],
            capture_output=True, timeout=15,
        )
        r = subprocess.run(
            ["pkexec", "/usr/bin/tee", str(dest)],
            input=src.read_bytes(),
            capture_output=True,
            timeout=30,
        )
        if r.returncode == 0:
            return True, ""
        return False, r.stderr.decode(errors="replace").strip()
    except FileNotFoundError:
        return False, "pkexec not found."
    except subprocess.TimeoutExpired:
        return False, "pkexec timed out."
    except Exception as e:
        return False, str(e)


def _chmod_helper() -> None:
    """Make the helper executable."""
    try:
        subprocess.run(
            ["pkexec", "/bin/chmod", "+x", str(HELPER_DEST)],
            capture_output=True, timeout=15,
        )
    except Exception:
        pass


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


def install_policy() -> tuple[bool, str]:
    """
    Install the helper script and polkit policy using pkexec.
    Both files are written with a single pkexec auth — auth_admin_keep
    caches the credential so the second write doesn't prompt again.
    Returns (success, message).
    """
    if not POLICY_SRC.exists():
        return False, f"Policy source not found: {POLICY_SRC}"
    if not HELPER_SRC.exists():
        return False, f"Helper source not found: {HELPER_SRC}"

    ok, err = _write_as_root(HELPER_SRC, HELPER_DEST)
    if not ok:
        return False, f"Could not install helper: {err}"
    _chmod_helper()

    ok, err = _write_as_root(POLICY_SRC, POLICY_DEST)
    if not ok:
        return False, f"Helper installed but policy failed: {err}"

    return True, "CleanMint helper and policy installed successfully."


def uninstall_policy() -> tuple[bool, str]:
    """Remove both the helper and policy file (requires root)."""
    errors = []
    for path in (HELPER_DEST, POLICY_DEST):
        try:
            r = subprocess.run(
                ["pkexec", "/usr/bin/rm", "-f", str(path)],
                capture_output=True, timeout=30,
            )
            if r.returncode != 0:
                errors.append(r.stderr.decode(errors="replace").strip())
        except Exception as e:
            errors.append(str(e))
    if errors:
        return False, "; ".join(errors)
    return True, "CleanMint helper and policy removed."
