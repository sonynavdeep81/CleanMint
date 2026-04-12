"""
core/scanner.py — CleanMint Disk Scanner

Scans for junk categories and returns size estimates.
Never deletes anything — read-only operations only.
Designed to run inside a QThread via signals.
"""

import os
import subprocess
from pathlib import Path
from dataclasses import dataclass, field
from typing import Callable

HOME = Path.home()


@dataclass
class ScanCategory:
    id: str
    name: str
    description: str
    risk: str          # "low" | "medium" | "expert"
    recommended: bool
    paths: list[Path] = field(default_factory=list)
    size_bytes: int = 0
    file_count: int = 0
    error: str = ""

    @property
    def size_human(self) -> str:
        return _human_size(self.size_bytes)


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _dir_size(path: Path) -> tuple[int, int]:
    """Return (total_bytes, file_count) for a directory tree. Never raises."""
    total = 0
    count = 0
    try:
        for entry in os.scandir(path):
            try:
                if entry.is_symlink():
                    continue
                if entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
                    count += 1
                elif entry.is_dir(follow_symlinks=False):
                    sub_bytes, sub_count = _dir_size(Path(entry.path))
                    total += sub_bytes
                    count += sub_count
            except (PermissionError, OSError):
                continue
    except (PermissionError, OSError):
        pass
    return total, count


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _apt_cache_size() -> tuple[int, int, str]:
    """Sum .deb file sizes directly — avoids the /partial permission error from du."""
    apt_path = Path("/var/cache/apt/archives")
    if not apt_path.exists():
        return 0, 0, ""
    try:
        total = 0
        count = 0
        for f in apt_path.glob("*.deb"):
            try:
                total += f.stat().st_size
                count += 1
            except OSError:
                pass
        return total, count, ""
    except (OSError, ValueError) as e:
        return 0, 0, str(e)


def _journal_size() -> tuple[int, int, str]:
    """Use journalctl --disk-usage for journal size."""
    try:
        result = subprocess.run(
            ["journalctl", "--disk-usage"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            # Output: "Archived and active journals take up X.XG (or similar) in the file system."
            line = result.stdout.strip()
            import re
            m = re.search(r"(\d+(?:\.\d+)?)\s*(B|K|M|G|T)", line)
            if m:
                val, unit = float(m.group(1)), m.group(2)
                mult = {"B": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
                return int(val * mult.get(unit, 1)), 0, ""
        return 0, 0, result.stderr.strip()
    except (subprocess.TimeoutExpired, OSError) as e:
        return 0, 0, str(e)


class Scanner:
    """
    Scans the system for junk and returns a list of ScanCategory results.

    Usage:
        scanner = Scanner(progress_callback=fn, sandbox_root=None)
        categories = scanner.run_full_scan()
    """

    def __init__(
        self,
        progress_callback: Callable[[str, int], None] | None = None,
        sandbox_root: Path | None = None,
    ):
        """
        progress_callback(message, percent) — called during scan for UI updates.
        sandbox_root — if set, all home-relative paths are remapped into this
                       directory for safe testing without touching real files.
        """
        self._progress = progress_callback or (lambda msg, pct: None)
        self._sandbox = sandbox_root

    def _resolve(self, path: Path) -> Path:
        """Remap path into sandbox if one is set."""
        if self._sandbox is None:
            return path
        # Remap home-relative paths into sandbox
        try:
            rel = path.relative_to(HOME)
            return self._sandbox / rel
        except ValueError:
            # Not a home path — remap from root
            rel = str(path).lstrip("/")
            return self._sandbox / rel

    def _scan_dirs(self, paths: list[Path]) -> tuple[int, int]:
        total, count = 0, 0
        for p in paths:
            rp = self._resolve(p)
            if rp.is_dir():
                b, c = _dir_size(rp)
                total += b
                count += c
            elif rp.is_file():
                total += _file_size(rp)
                count += 1
        return total, count

    def run_full_scan(self) -> list[ScanCategory]:
        categories = []
        steps = [
            ("Scanning APT cache...",        10,  self._scan_apt_cache),
            ("Scanning thumbnail cache...",  20,  self._scan_thumbnails),
            ("Scanning browser cache...",    32,  self._scan_browser_cache),
            ("Scanning trash...",            44,  self._scan_trash),
            ("Scanning temp files...",       55,  self._scan_temp_files),
            ("Scanning system logs...",      66,  self._scan_journal_logs),
            ("Scanning pip cache...",        76,  self._scan_pip_cache),
            ("Scanning npm cache...",        86,  self._scan_npm_cache),
            ("Scanning Snap revisions...",   93,  self._scan_snap_revisions),
            ("Scanning Flatpak unused...",   97,  self._scan_flatpak),
        ]

        for msg, pct, fn in steps:
            self._progress(msg, pct)
            cat = fn()
            categories.append(cat)

        self._progress("Scan complete.", 100)
        return categories

    # ------------------------------------------------------------------
    # Individual category scanners
    # ------------------------------------------------------------------

    def _scan_apt_cache(self) -> ScanCategory:
        cat = ScanCategory(
            id="apt_cache",
            name="APT Package Cache",
            description="Downloaded .deb packages kept by apt. Safe to clear anytime.",
            risk="low",
            recommended=True,
            paths=[Path("/var/cache/apt/archives")],
        )
        if self._sandbox:
            size, count = self._scan_dirs(cat.paths)
        else:
            size, count, err = _apt_cache_size()
            cat.error = err
        cat.size_bytes = size
        cat.file_count = count
        return cat

    def _scan_thumbnails(self) -> ScanCategory:
        paths = [
            HOME / ".thumbnails",
            HOME / ".local" / "share" / "thumbnails",
        ]
        cat = ScanCategory(
            id="thumbnails",
            name="Thumbnail Cache",
            description="Cached image previews. Regenerated automatically when needed.",
            risk="low",
            recommended=True,
            paths=paths,
        )
        cat.size_bytes, cat.file_count = self._scan_dirs(paths)
        return cat

    def _scan_browser_cache(self) -> ScanCategory:
        # Only target specific cache subdirectories inside each browser profile.
        # This ensures cookies, passwords, bookmarks, and login sessions
        # (stored in ~/.config/google-chrome/) are NEVER touched.
        #
        # Safe cache subdirectory names (temporary data only):
        CACHE_SUBDIRS = frozenset([
            "Cache", "cache2", "Code Cache", "GPUCache",
            "ScriptCache", "ShaderCache", "Application Cache",
            "Service Worker",
        ])

        browser_roots = [
            HOME / ".cache" / "mozilla",           # Firefox
            HOME / ".cache" / "google-chrome",     # Chrome
            HOME / ".cache" / "chromium",          # Chromium
            HOME / ".cache" / "BraveSoftware",     # Brave
        ]

        safe_paths: list[Path] = []
        for browser_root in browser_roots:
            rr = self._resolve(browser_root)
            if not rr.is_dir():
                continue

            # Firefox: cache is directly inside profile dirs
            if "mozilla" in browser_root.name:
                safe_paths.append(browser_root)   # mozilla cache structure is flat
                continue

            # Chrome-based: profile dirs contain Cache, Code Cache, etc.
            try:
                for profile_dir in rr.iterdir():
                    if not profile_dir.is_dir():
                        continue
                    for subdir in profile_dir.iterdir():
                        if subdir.is_dir() and subdir.name in CACHE_SUBDIRS:
                            # Map back to logical path (un-sandboxed)
                            try:
                                rel = profile_dir.relative_to(rr)
                                logical = browser_root / rel / subdir.name
                            except ValueError:
                                logical = Path(str(subdir))
                            safe_paths.append(logical)
            except PermissionError:
                continue

        cat = ScanCategory(
            id="browser_cache",
            name="Browser Cache",
            description="Temporary cache files (images, scripts, GPU cache) for Firefox, Chrome, Chromium, Brave. "
                        "Passwords, cookies, and login sessions are NOT touched.",
            risk="low",
            recommended=True,
            paths=safe_paths,
        )
        cat.size_bytes, cat.file_count = self._scan_dirs(safe_paths)
        return cat

    def _scan_trash(self) -> ScanCategory:
        paths = [HOME / ".local" / "share" / "Trash"]
        cat = ScanCategory(
            id="trash",
            name="Trash / Recycle Bin",
            description="Files you have already deleted. Emptying is permanent.",
            risk="low",
            recommended=True,
            paths=paths,
        )
        cat.size_bytes, cat.file_count = self._scan_dirs(paths)
        return cat

    def _scan_temp_files(self) -> ScanCategory:
        paths = [Path("/tmp"), Path("/var/tmp")]
        cat = ScanCategory(
            id="temp_files",
            name="Temporary Files",
            description="Short-lived system temp files. Most are recreated on demand.",
            risk="low",
            recommended=True,
            paths=paths,
        )
        # Only count files owned by the current user — root-owned systemd service
        # dirs are skipped by the cleaner and should not inflate the reported size.
        uid = os.getuid()
        total, count = 0, 0
        for tmp_dir in [self._resolve(p) for p in paths]:
            if not tmp_dir.exists():
                continue
            try:
                for entry in tmp_dir.iterdir():
                    try:
                        st = entry.stat(follow_symlinks=False)
                        if st.st_uid != uid:
                            continue
                        if entry.is_symlink():
                            continue  # skip symlinks
                        if entry.is_file():
                            total += st.st_size
                            count += 1
                        elif entry.is_dir():
                            b, c = _dir_size(entry)
                            total += b
                            count += c
                    except (PermissionError, OSError):
                        continue
            except (PermissionError, OSError):
                continue
        cat.size_bytes = total
        cat.file_count = count
        return cat

    def _scan_journal_logs(self) -> ScanCategory:
        cat = ScanCategory(
            id="journal_logs",
            name="System Journal Logs",
            description="systemd journal logs. CleanMint keeps the last 7 days / 50 MB.",
            risk="low",
            recommended=True,
            paths=[Path("/var/log/journal")],
        )
        if self._sandbox:
            cat.size_bytes, cat.file_count = self._scan_dirs(cat.paths)
        else:
            size, _, err = _journal_size()
            cat.size_bytes = size
            cat.error = err
        return cat

    def _scan_pip_cache(self) -> ScanCategory:
        paths = [HOME / ".cache" / "pip"]
        cat = ScanCategory(
            id="pip_cache",
            name="Python pip Cache",
            description="Cached pip downloads. Cleared automatically on reinstall.",
            risk="low",
            recommended=True,
            paths=paths,
        )
        cat.size_bytes, cat.file_count = self._scan_dirs(paths)
        return cat

    def _scan_npm_cache(self) -> ScanCategory:
        paths = [
            HOME / ".npm" / "_cacache",
            HOME / ".npm" / "_npx",
        ]
        cat = ScanCategory(
            id="npm_cache",
            name="Node / npm Cache",
            description="npm package download cache. Safe to remove.",
            risk="low",
            recommended=True,
            paths=paths,
        )
        cat.size_bytes, cat.file_count = self._scan_dirs(paths)
        return cat

    def _scan_snap_revisions(self) -> ScanCategory:
        """Detect old (non-active) Snap revisions."""
        cat = ScanCategory(
            id="snap_revisions",
            name="Old Snap Revisions",
            description="Previous Snap package versions kept for rollback. Usually safe to remove.",
            risk="medium",
            recommended=True,
            paths=[],
        )
        if self._sandbox:
            return cat  # Skip real snap commands in sandbox mode

        try:
            result = subprocess.run(
                ["snap", "list", "--all"],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode != 0:
                cat.error = "snap not available"
                return cat

            total_size = 0
            count = 0
            snap_base = Path("/var/lib/snapd/snaps")

            for line in result.stdout.splitlines()[1:]:  # skip header
                parts = line.split()
                if len(parts) >= 6 and "disabled" in parts[5]:
                    name = parts[0]
                    rev = parts[2]
                    snap_file = snap_base / f"{name}_{rev}.snap"
                    if snap_file.exists():
                        total_size += _file_size(snap_file)
                        count += 1
                        cat.paths.append(snap_file)

            cat.size_bytes = total_size
            cat.file_count = count
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            cat.error = str(e)

        return cat

    def _scan_flatpak(self) -> ScanCategory:
        cat = ScanCategory(
            id="flatpak_unused",
            name="Flatpak Unused Runtimes",
            description="Flatpak runtimes no longer needed by any installed app.",
            risk="medium",
            recommended=False,
            paths=[],
        )
        if self._sandbox:
            return cat

        try:
            # Pipe "n" so flatpak prompts but doesn't actually remove anything
            result = subprocess.run(
                ["flatpak", "uninstall", "--unused"],
                input="n\n", capture_output=True, text=True, timeout=15
            )
            if "Nothing unused" in result.stdout or "Nothing unused" in result.stderr:
                cat.size_bytes = 0
                cat.file_count = 0
            else:
                # Count ref lines (runtime/... or app/...)
                lines = [
                    l for l in result.stdout.splitlines()
                    if l.strip() and not l.startswith("Uninstall") and "unused" not in l.lower()
                ]
                cat.file_count = max(len(lines), 1) if lines else 0
                cat.size_bytes = 0  # size not available without removal
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            cat.error = str(e)

        return cat
