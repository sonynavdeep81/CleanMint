"""
core/safety.py — CleanMint Safety Layer

All deletions in CleanMint MUST pass through this module.
Nothing is deleted unless it is explicitly on the ALLOWED list
AND not on the BLOCKED list.
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Absolute paths that are NEVER touched under any circumstances
# ---------------------------------------------------------------------------
BLOCKED_ROOTS = frozenset([
    "/boot",
    "/etc",
    "/usr",
    "/lib",
    "/lib32",
    "/lib64",
    "/libx32",
    "/bin",
    "/sbin",
    "/dev",
    "/proc",
    "/sys",
    "/root",
    "/run",
    "/snap/bin",
])

# User directories that are never cleaned by default
BLOCKED_USER_DIRS = frozenset([
    "Documents",
    "Desktop",
    "Pictures",
    "Videos",
    "Music",
    ".ssh",
    ".gnupg",
    ".config/google-chrome/Default/Login Data",
    ".config/chromium/Default/Login Data",
])

# ---------------------------------------------------------------------------
# Allowlisted target directories / patterns (only these are ever cleaned)
# ---------------------------------------------------------------------------
HOME = Path.home()

ALLOWED_CLEAN_TARGETS = [
    # apt / dpkg cache
    Path("/var/cache/apt/archives"),

    # systemd journal logs (safe portion — not full /var/log)
    Path("/var/log/journal"),

    # user-space targets
    HOME / ".cache",
    HOME / ".local/share/Trash",
    HOME / ".thumbnails",
    HOME / ".local/share/thumbnails",
    HOME / ".npm/_npx",
    HOME / ".npm/_cacache",
    HOME / ".cache/pip",
    HOME / ".cache/mozilla",
    HOME / ".cache/google-chrome",
    HOME / ".cache/chromium",
    HOME / ".cache/BraveSoftware",
    HOME / ".local/share/recently-used.xbel",  # handled separately
    Path("/tmp"),  # only files, never /tmp itself
    Path("/var/tmp"),
]


# ---------------------------------------------------------------------------
# Core validation functions
# ---------------------------------------------------------------------------

def is_blocked(path: Path) -> bool:
    """Return True if path is inside a blocked root — must never be deleted."""
    resolved = path.resolve()
    path_str = str(resolved)

    for blocked in BLOCKED_ROOTS:
        if path_str == blocked or path_str.startswith(blocked + "/"):
            return True

    # Block user sensitive dirs
    for blocked_rel in BLOCKED_USER_DIRS:
        blocked_abs = HOME / blocked_rel
        if path_str == str(blocked_abs) or path_str.startswith(str(blocked_abs) + "/"):
            return True

    return False


def is_allowed_target(path: Path) -> bool:
    """Return True only if path lives inside an allowlisted clean target."""
    resolved = path.resolve()
    path_str = str(resolved)

    for allowed in ALLOWED_CLEAN_TARGETS:
        allowed_str = str(allowed.resolve())
        if path_str == allowed_str or path_str.startswith(allowed_str + "/"):
            return True

    return False


def validate_delete(path: Path) -> tuple[bool, str]:
    """
    Gate every deletion through this function.

    Returns:
        (True, "") if safe to delete
        (False, reason) if deletion must be blocked
    """
    if not path.exists() and not path.is_symlink():
        return False, f"Path does not exist: {path}"

    if is_blocked(path):
        return False, f"BLOCKED: {path} is inside a protected system directory."

    if not is_allowed_target(path):
        return False, f"NOT ALLOWED: {path} is not within an approved cleanup target."

    # Extra guard: never delete a path that is a mount point
    if path.is_mount():
        return False, f"BLOCKED: {path} is a mount point."

    return True, ""


def validate_delete_batch(paths: list[Path]) -> tuple[list[Path], list[tuple[Path, str]]]:
    """
    Validate a list of paths for deletion.

    Returns:
        approved: list of paths safe to delete
        rejected: list of (path, reason) tuples
    """
    approved = []
    rejected = []

    for p in paths:
        ok, reason = validate_delete(p)
        if ok:
            approved.append(p)
        else:
            rejected.append((p, reason))

    return approved, rejected
