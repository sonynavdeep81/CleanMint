"""
core/health.py — CleanMint System Health Checker

Read-only checks: broken packages, failed services, disk risk,
old kernels, excessive logs. Returns structured results.
"""

import subprocess
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class HealthCheck:
    id: str
    title: str
    status: str         # "ok" | "warning" | "critical" | "info"
    detail: str
    fix_cmd: list[str] = field(default_factory=list)   # safe fix command if available
    fix_label: str = ""
    services: list[str] = field(default_factory=list)  # failed service names (for restart UI)
    packages: list[tuple] = field(default_factory=list)  # (name, new_ver) for pending updates


class HealthChecker:
    def __init__(self, progress_callback: Callable[[str, int], None] | None = None):
        self._progress = progress_callback or (lambda m, p: None)

    def run_all(self) -> list[HealthCheck]:
        checks = []
        steps = [
            ("Checking disk space…",         8,  self.check_disk_space),
            ("Checking broken packages…",    20, self.check_broken_packages),
            ("Checking failed services…",    34, self.check_failed_services),
            ("Checking old kernels…",        48, self.check_old_kernels),
            ("Checking journal size…",       60, self.check_journal_size),
            ("Checking APT updates…",        72, self.check_pending_updates),
            ("Checking Snap updates…",       84, self.check_snap_updates),
            ("Checking Flatpak updates…",    94, self.check_flatpak_updates),
        ]
        for msg, pct, fn in steps:
            self._progress(msg, pct)
            try:
                result = fn()
                checks.append(result)
            except Exception as e:
                checks.append(HealthCheck(
                    id="error",
                    title="Check error",
                    status="warning",
                    detail=str(e),
                ))
        self._progress("Health check complete.", 100)
        return checks

    # ── Individual checks ──────────────────────────────────────

    def check_disk_space(self) -> HealthCheck:
        import psutil
        usage = psutil.disk_usage("/")
        # Use used/total (same formula as dashboard) for consistency
        pct = round(usage.used / usage.total * 100, 1) if usage.total > 0 else 0

        from core.scanner import _human_size
        free_str = _human_size(usage.free)

        if pct >= 95:
            return HealthCheck(
                id="disk_space", title="Disk Space",
                status="critical",
                detail=f"Only {free_str} free ({pct:.0f}% used). Clean up urgently.",
            )
        elif pct >= 80:
            return HealthCheck(
                id="disk_space", title="Disk Space",
                status="warning",
                detail=f"{free_str} free ({pct:.0f}% used). Consider cleaning soon.",
            )
        return HealthCheck(
            id="disk_space", title="Disk Space",
            status="ok",
            detail=f"{free_str} free ({pct:.0f}% used). Disk space is healthy.",
        )

    def check_broken_packages(self) -> HealthCheck:
        try:
            result = subprocess.run(
                ["dpkg", "--audit"],
                capture_output=True, text=True, timeout=15
            )
            if result.stdout.strip():
                return HealthCheck(
                    id="broken_packages", title="Package Integrity",
                    status="warning",
                    detail=f"Broken packages found:\n{result.stdout.strip()[:300]}",
                    fix_cmd=["sudo", "apt", "--fix-broken", "install"],
                    fix_label="Fix with apt",
                )
            return HealthCheck(
                id="broken_packages", title="Package Integrity",
                status="ok",
                detail="No broken packages detected.",
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return HealthCheck(
                id="broken_packages", title="Package Integrity",
                status="info", detail=f"dpkg not available: {e}",
            )

    def check_failed_services(self) -> HealthCheck:
        try:
            result = subprocess.run(
                ["systemctl", "--failed", "--no-legend", "--plain"],
                capture_output=True, text=True, timeout=10
            )
            lines = [l for l in result.stdout.splitlines() if l.strip()]
            if lines:
                service_names = [l.split()[0] for l in lines]
                names = ", ".join(service_names[:5])
                extra = f" (+{len(lines)-5} more)" if len(lines) > 5 else ""
                return HealthCheck(
                    id="failed_services", title="Failed Services",
                    status="warning",
                    detail=f"{len(lines)} failed service(s): {names}{extra}",
                    services=service_names,
                    fix_label="Show & Restart",
                )
            return HealthCheck(
                id="failed_services", title="Failed Services",
                status="ok",
                detail="No failed systemd services.",
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return HealthCheck(
                id="failed_services", title="Failed Services",
                status="info", detail=f"systemctl not available: {e}",
            )

    def check_old_kernels(self) -> HealthCheck:
        try:
            import platform
            running = platform.release()

            result = subprocess.run(
                ["dpkg", "--list", "linux-image-*"],
                capture_output=True, text=True, timeout=15
            )
            installed = []
            for line in result.stdout.splitlines():
                if line.startswith("ii"):
                    parts = line.split()
                    if len(parts) >= 2 and "linux-image-" in parts[1]:
                        ver = parts[1].replace("linux-image-", "")
                        if ver != running and re.match(r"[\d\.\-]", ver):
                            installed.append(ver)

            if len(installed) > 1:
                return HealthCheck(
                    id="old_kernels", title="Old Kernels",
                    status="warning",
                    detail=f"{len(installed)} old kernel(s) installed. Running: {running}.\n"
                           f"Old: {', '.join(installed[:3])}",
                    fix_cmd=["sudo", "apt", "autoremove", "--purge"],
                    fix_label="Remove with apt autoremove",
                )
            elif len(installed) == 1:
                return HealthCheck(
                    id="old_kernels", title="Old Kernels",
                    status="info",
                    detail=f"1 previous kernel kept as fallback. Running: {running}.",
                )
            return HealthCheck(
                id="old_kernels", title="Old Kernels",
                status="ok",
                detail=f"Only current kernel installed ({running}).",
            )
        except Exception as e:
            return HealthCheck(
                id="old_kernels", title="Old Kernels",
                status="info", detail=f"Could not check kernels: {e}",
            )

    def check_journal_size(self) -> HealthCheck:
        try:
            result = subprocess.run(
                ["journalctl", "--disk-usage"],
                capture_output=True, text=True, timeout=10
            )
            line = result.stdout.strip()
            m = re.search(r"(\d+(?:\.\d+)?)\s*(B|K|M|G|T)", line)
            if m:
                val = float(m.group(1))
                unit = m.group(2)
                mult = {"B": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
                size_bytes = int(val * mult.get(unit, 1))
                from core.scanner import _human_size
                size_str = _human_size(size_bytes)

                if size_bytes > 2 * 1024**3:
                    return HealthCheck(
                        id="journal", title="System Journal",
                        status="warning",
                        detail=f"Journal is {size_str}. Consider vacuuming to 500 MB.",
                        fix_cmd=["sudo", "journalctl", "--vacuum-size=500M"],
                        fix_label="Vacuum journal",
                    )
                return HealthCheck(
                    id="journal", title="System Journal",
                    status="ok",
                    detail=f"Journal size is {size_str}. Within normal range.",
                )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            pass
        return HealthCheck(
            id="journal", title="System Journal",
            status="info", detail="Could not determine journal size.",
        )

    def check_snap_updates(self) -> HealthCheck:
        try:
            result = subprocess.run(
                ["snap", "refresh", "--list"],
                capture_output=True, text=True, timeout=30
            )
            packages: list[tuple] = []
            for line in result.stdout.splitlines():
                if not line.strip() or line.startswith("Name") or line.startswith("All"):
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    packages.append((parts[0], parts[1]))

            if not packages:
                return HealthCheck(
                    id="snap_updates", title="Snap Updates",
                    status="ok", detail="All snaps are up to date.",
                )
            names_preview = ", ".join(p[0] for p in packages[:3])
            extra = f" +{len(packages) - 3} more" if len(packages) > 3 else ""
            return HealthCheck(
                id="snap_updates", title="Snap Updates",
                status="warning" if len(packages) > 5 else "info",
                detail=f"{len(packages)} snap(s) can be refreshed: {names_preview}{extra}.",
                packages=packages,
            )
        except FileNotFoundError:
            return HealthCheck(
                id="snap_updates", title="Snap Updates",
                status="ok", detail="Snap is not installed.",
            )
        except Exception as e:
            return HealthCheck(
                id="snap_updates", title="Snap Updates",
                status="info", detail=f"Could not check snap updates: {e}",
            )

    def check_flatpak_updates(self) -> HealthCheck:
        try:
            remotes_result = subprocess.run(
                ["flatpak", "remotes", "--columns=name"],
                capture_output=True, text=True, timeout=10
            )
            remotes = [
                l.strip() for l in remotes_result.stdout.splitlines()
                if l.strip() and l.strip().lower() != "name"
            ]
            if not remotes:
                return HealthCheck(
                    id="flatpak_updates", title="Flatpak Updates",
                    status="ok", detail="No Flatpak remotes configured.",
                )

            packages: list[tuple] = []
            seen: set[str] = set()
            for remote in remotes:
                r = subprocess.run(
                    ["flatpak", "remote-ls", remote, "--updates",
                     "--columns=application,version"],
                    capture_output=True, text=True, timeout=20
                )
                for line in r.stdout.splitlines():
                    parts = line.split()
                    if parts and parts[0] not in seen:
                        seen.add(parts[0])
                        packages.append((parts[0], parts[1] if len(parts) > 1 else ""))

            if not packages:
                return HealthCheck(
                    id="flatpak_updates", title="Flatpak Updates",
                    status="ok", detail="All Flatpaks are up to date.",
                )
            names_preview = ", ".join(p[0].split(".")[-1] for p in packages[:3])
            extra = f" +{len(packages) - 3} more" if len(packages) > 3 else ""
            return HealthCheck(
                id="flatpak_updates", title="Flatpak Updates",
                status="warning" if len(packages) > 5 else "info",
                detail=f"{len(packages)} Flatpak(s) can be updated: {names_preview}{extra}.",
                packages=packages,
            )
        except FileNotFoundError:
            return HealthCheck(
                id="flatpak_updates", title="Flatpak Updates",
                status="ok", detail="Flatpak is not installed.",
            )
        except Exception as e:
            return HealthCheck(
                id="flatpak_updates", title="Flatpak Updates",
                status="info", detail=f"Could not check Flatpak updates: {e}",
            )

    def check_pending_updates(self) -> HealthCheck:
        try:
            result = subprocess.run(
                ["apt-get", "-s", "upgrade"],
                capture_output=True, text=True, timeout=20
            )
            m = re.search(r"(\d+) upgraded", result.stdout)
            count = int(m.group(1)) if m else 0

            packages: list[tuple] = []
            for line in result.stdout.splitlines():
                if line.startswith("Inst "):
                    parts = line.split()
                    if len(parts) < 2:
                        continue
                    name = parts[1]
                    new_ver = ""
                    for part in parts[2:]:
                        if part.startswith("("):
                            new_ver = part.lstrip("(")
                            break
                    packages.append((name, new_ver))

            if count > 50:
                return HealthCheck(
                    id="updates", title="Pending Updates",
                    status="warning",
                    detail=f"{count} packages can be upgraded.",
                    packages=packages,
                )
            elif count > 0:
                return HealthCheck(
                    id="updates", title="Pending Updates",
                    status="info",
                    detail=f"{count} packages can be upgraded.",
                    packages=packages,
                )
            return HealthCheck(
                id="updates", title="Pending Updates",
                status="ok",
                detail="System is up to date.",
            )
        except Exception as e:
            return HealthCheck(
                id="updates", title="Pending Updates",
                status="info", detail=f"Could not check updates: {e}",
            )
