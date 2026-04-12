"""
core/apps.py — Installed Application Manager

Lists user-installed apps from APT, Snap, and Flatpak.
Safety-checks every removal before executing it.
Uninstalls via the pkexec helper — one password per session.
"""

import re
import subprocess
from dataclasses import dataclass, field
from typing import Callable

HELPER = "/usr/local/lib/cleanmint/cleanmint-helper"

# Packages that are critical system components — block removal entirely
_BLOCKED_PACKAGES = {
    "bash", "coreutils", "python3", "systemd", "apt", "dpkg",
    "ubuntu-minimal", "ubuntu-standard", "linux-base", "sudo",
    "init", "mount", "util-linux", "grep", "sed", "gawk",
    "tar", "gzip", "login", "passwd", "adduser",
}

# Prefixes that are almost always low-level libraries — hide from the list
# (user-visible apps don't start with these)
_HIDDEN_PREFIXES = ("lib",)
# ...except well-known user-facing packages that happen to start with "lib"
_HIDDEN_EXCEPTIONS = ("libreoffice",)


def _human_size(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


@dataclass
class InstalledApp:
    name: str
    package_id: str     # actual id used for uninstall
    version: str
    size_bytes: int
    description: str
    source: str         # "apt" | "snap" | "flatpak"

    @property
    def size_human(self) -> str:
        return _human_size(self.size_bytes) if self.size_bytes else "—"


@dataclass
class RemovalSafety:
    safe: bool
    blocked: bool                           # True = refuse removal entirely
    extra_removals: list[str] = field(default_factory=list)
    warning: str = ""


class AppManager:
    def __init__(self, progress_callback: Callable[[str, int], None] | None = None):
        self._progress = progress_callback or (lambda m, p: None)

    # ── Listing ────────────────────────────────────────────────────

    def list_apt_apps(self) -> list[InstalledApp]:
        apps = []
        try:
            result = subprocess.run(
                ["apt-mark", "showmanual"],
                capture_output=True, text=True, timeout=15
            )
            packages = [p.strip() for p in result.stdout.splitlines() if p.strip()]
            if not packages:
                return apps

            fields = "${Package}\t${Version}\t${Installed-Size}\t${Description}\n"
            r = subprocess.run(
                ["dpkg-query", "-W", f"--showformat={fields}"] + packages,
                capture_output=True, text=True, timeout=30
            )
            for line in r.stdout.splitlines():
                parts = line.split("\t", 3)
                if len(parts) < 4:
                    continue
                pkg, ver, size_kb, desc = parts
                pkg = pkg.strip()
                if not pkg:
                    continue
                # Hide low-level libraries unless they are user-facing exceptions
                if any(pkg.startswith(p) for p in _HIDDEN_PREFIXES):
                    if not any(pkg.startswith(e) for e in _HIDDEN_EXCEPTIONS):
                        continue
                try:
                    size = int(size_kb.strip() or 0) * 1024
                except ValueError:
                    size = 0
                apps.append(InstalledApp(
                    name=pkg,
                    package_id=pkg,
                    version=ver.strip(),
                    size_bytes=size,
                    description=desc.strip().split("\n")[0][:120],
                    source="apt",
                ))
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return sorted(apps, key=lambda a: a.name.lower())

    def list_snap_apps(self) -> list[InstalledApp]:
        apps = []
        _SNAP_RUNTIMES = {"core", "core18", "core20", "core22", "core24", "bare", "snapd"}
        try:
            result = subprocess.run(
                ["snap", "list"],
                capture_output=True, text=True, timeout=15
            )
            for line in result.stdout.splitlines()[1:]:
                parts = line.split()
                if len(parts) < 2:
                    continue
                name, version = parts[0], parts[1]
                if name in _SNAP_RUNTIMES:
                    continue
                apps.append(InstalledApp(
                    name=name,
                    package_id=name,
                    version=version,
                    size_bytes=0,
                    description="Snap package",
                    source="snap",
                ))
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return sorted(apps, key=lambda a: a.name.lower())

    def list_flatpak_apps(self) -> list[InstalledApp]:
        apps = []
        try:
            result = subprocess.run(
                ["flatpak", "list", "--app",
                 "--columns=name,application,version,size"],
                capture_output=True, text=True, timeout=15
            )
            for line in result.stdout.splitlines():
                parts = line.split("\t")
                if len(parts) < 2:
                    continue
                name    = parts[0].strip()
                app_id  = parts[1].strip()
                version = parts[2].strip() if len(parts) > 2 else ""
                size_str= parts[3].strip() if len(parts) > 3 else ""
                apps.append(InstalledApp(
                    name=name or app_id,
                    package_id=app_id,
                    version=version,
                    size_bytes=self._parse_flatpak_size(size_str),
                    description=f"Flatpak — {app_id}",
                    source="flatpak",
                ))
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return sorted(apps, key=lambda a: a.name.lower())

    def _parse_flatpak_size(self, s: str) -> int:
        m = re.match(r"([\d.]+)\s*(B|KB|MB|GB)", s, re.IGNORECASE)
        if not m:
            return 0
        val, unit = float(m.group(1)), m.group(2).upper()
        return int(val * {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}.get(unit, 1))

    # ── Safety check ───────────────────────────────────────────────

    def check_removal_safety(self, app: InstalledApp) -> RemovalSafety:
        """
        For APT packages: simulate removal with apt-get --dry-run and parse
        what else would be removed. Snaps and Flatpaks are isolated — always safe.
        """
        if app.source in ("snap", "flatpak"):
            return RemovalSafety(safe=True, blocked=False)

        # Hard-block known critical packages
        if app.package_id in _BLOCKED_PACKAGES:
            return RemovalSafety(
                safe=False, blocked=True,
                warning=f"'{app.package_id}' is a critical system component. "
                        "Removing it would break your system."
            )

        # Check Essential flag in dpkg database (no root needed)
        try:
            r = subprocess.run(
                ["dpkg-query", "-W", "-f=${Essential}", app.package_id],
                capture_output=True, text=True, timeout=5
            )
            if r.stdout.strip().lower() == "yes":
                return RemovalSafety(
                    safe=False, blocked=True,
                    warning=f"'{app.package_id}' is marked Essential by Debian/Ubuntu. "
                            "Removing it would break your system."
                )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Simulate removal — no root needed for dry-run
        try:
            r = subprocess.run(
                ["apt-get", "remove", "--dry-run", "--quiet", "--quiet",
                 app.package_id],
                capture_output=True, text=True, timeout=20
            )
            output = r.stdout

            # Collect every package listed under "will be REMOVED"
            extra: list[str] = []
            in_section = False
            for line in output.splitlines():
                if re.search(r"will be REMOVED", line):
                    in_section = True
                    continue
                if in_section:
                    if line.startswith("  "):
                        for token in line.split():
                            pkg = token.strip("*+")
                            if pkg and pkg != app.package_id:
                                extra.append(pkg)
                    else:
                        in_section = False

            if extra:
                return RemovalSafety(
                    safe=False, blocked=False,
                    extra_removals=extra,
                    warning=(
                        f"Removing '{app.package_id}' will also remove "
                        f"{len(extra)} other package(s) that depend on it."
                    )
                )
            return RemovalSafety(safe=True, blocked=False)

        except (FileNotFoundError, subprocess.TimeoutExpired):
            return RemovalSafety(
                safe=True, blocked=False,
                warning="Could not verify dependencies (apt not available)."
            )

    # ── Uninstall ──────────────────────────────────────────────────

    def uninstall(self, app: InstalledApp) -> tuple[bool, str]:
        """Uninstall via pkexec helper. Requires polkit policy installed."""
        op_map = {
            "apt":     ["apt-remove",        app.package_id],
            "snap":    ["snap-uninstall",     app.package_id],
            "flatpak": ["flatpak-uninstall",  app.package_id],
        }
        args = op_map.get(app.source)
        if not args:
            return False, f"Unknown source: {app.source}"
        try:
            r = subprocess.run(
                ["pkexec", HELPER] + args,
                capture_output=True, text=True, timeout=180
            )
            if r.returncode == 0:
                return True, f"'{app.name}' uninstalled successfully."
            return False, (r.stderr.strip() or r.stdout.strip() or "Unknown error")
        except subprocess.TimeoutExpired:
            return False, "Operation timed out."
        except FileNotFoundError:
            return False, "pkexec not found."
