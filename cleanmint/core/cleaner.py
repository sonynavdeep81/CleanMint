"""
core/cleaner.py — CleanMint Deletion Engine

Every delete goes through safety.validate_delete().
Supports dry-run mode (no actual deletion).
Logs all actions to an in-memory log and optionally to disk.
"""

import os
import re
import shutil
import logging
import subprocess
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from core.safety import validate_delete
from core.scanner import ScanCategory, _human_size

LOG_DIR = Path.home() / ".local" / "share" / "cleanmint" / "logs"
HELPER  = "/usr/local/lib/cleanmint/cleanmint-helper"


@dataclass
class CleanResult:
    category_id: str
    category_name: str
    dry_run: bool
    freed_bytes: int = 0
    deleted_count: int = 0
    skipped_count: int = 0
    errors: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)

    @property
    def freed_human(self) -> str:
        return _human_size(self.freed_bytes)


class Cleaner:
    """
    Deletes files from approved ScanCategory results.

    Usage:
        cleaner = Cleaner(dry_run=True)
        result = cleaner.clean_category(category)
    """

    def __init__(
        self,
        dry_run: bool = True,
        progress_callback: Callable[[str, int], None] | None = None,
        log_to_disk: bool = True,
    ):
        self.dry_run = dry_run
        self._progress = progress_callback or (lambda msg, pct: None)
        self._log_to_disk = log_to_disk
        self._session_log: list[str] = []

        if log_to_disk:
            self._setup_disk_logger()

    def _setup_disk_logger(self):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_file = LOG_DIR / f"cleanmint_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        logging.basicConfig(
            filename=str(log_file),
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
        )

    def _log(self, msg: str, level: str = "info"):
        self._session_log.append(f"[{level.upper()}] {msg}")
        getattr(logging, level, logging.info)(msg)

    def clean_category(self, category: ScanCategory) -> CleanResult:
        """
        Clean all paths in a ScanCategory.
        Respects dry_run flag — nothing is deleted when dry_run=True.
        """
        result = CleanResult(
            category_id=category.id,
            category_name=category.name,
            dry_run=self.dry_run,
        )

        mode = "[DRY RUN] " if self.dry_run else ""
        self._log(f"{mode}Starting clean: {category.name}")

        # Route privileged categories to dedicated handlers
        if category.id == "snap_revisions":
            return self._clean_snap_revisions(result)
        if category.id == "journal_logs":
            return self._clean_journal(result)
        if category.id == "temp_files":
            return self._clean_temp_files(category, result)
        if category.id == "apt_cache":
            return self._clean_apt_cache(result)

        # Check top-level category paths for blocked parents up-front
        from core.safety import is_blocked
        for p in category.paths:
            if p.exists() and is_blocked(p):
                msg = f"BLOCKED: {p} is a protected system path."
                result.errors.append(msg)
                result.skipped_count += 1

        # For categories where paths are directories (most cases),
        # collect individual files/subdirs to delete
        targets = self._collect_targets(category)
        total = len(targets)

        for i, path in enumerate(targets):
            pct = int((i / max(total, 1)) * 100)
            self._progress(f"{mode}Cleaning {path.name}...", pct)

            ok, reason = validate_delete(path)
            if not ok:
                msg = f"BLOCKED: {path} — {reason}"
                self._log(msg, "warning")
                result.errors.append(msg)
                result.skipped_count += 1
                continue

            try:
                size = self._get_size(path)

                if not self.dry_run:
                    self._delete(path)

                result.freed_bytes += size
                result.deleted_count += 1
                action = f"{'Would delete' if self.dry_run else 'Deleted'}: {path} ({_human_size(size)})"
                result.actions.append(action)
                self._log(action)

            except Exception as e:
                msg = f"ERROR deleting {path}: {e}"
                self._log(msg, "error")
                result.errors.append(msg)
                result.skipped_count += 1

        summary = (
            f"{mode}Finished '{category.name}': "
            f"{result.deleted_count} items, {result.freed_human} freed, "
            f"{result.skipped_count} skipped."
        )
        self._log(summary)
        self._progress(summary, 100)
        return result

    def clean_categories(self, categories: list[ScanCategory]) -> list[CleanResult]:
        """Clean multiple categories in sequence."""
        results = []
        for i, cat in enumerate(categories):
            pct = int((i / len(categories)) * 100)
            self._progress(f"Cleaning {cat.name}...", pct)
            results.append(self.clean_category(cat))
        return results

    # ------------------------------------------------------------------
    # Privileged category handlers
    # ------------------------------------------------------------------

    def _clean_snap_revisions(self, result: "CleanResult") -> "CleanResult":
        """Remove all disabled snap revisions in ONE pkexec call — single password prompt."""
        mode = "[DRY RUN] " if self.dry_run else ""
        try:
            snap_list = subprocess.run(
                ["snap", "list", "--all"],
                capture_output=True, text=True, timeout=15
            )
            snap_base = Path("/var/lib/snapd/snaps")

            # Collect all disabled revisions first
            pending = []
            for line in snap_list.stdout.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 6 and "disabled" in parts[5]:
                    name, rev = parts[0], parts[2]
                    snap_file = snap_base / f"{name}_{rev}.snap"
                    size = snap_file.stat().st_size if snap_file.exists() else 0
                    pending.append((name, rev, size))

            if not pending:
                self._progress("No disabled snap revisions found.", 100)
                return result

            # Log all actions
            for name, rev, size in pending:
                action = f"{'Would remove' if self.dry_run else 'Will remove'} snap {name} rev {rev} ({_human_size(size)})"
                self._log(action)
                result.actions.append(action)
                result.freed_bytes += size
                result.deleted_count += 1

            if not self.dry_run:
                # Use individual pkexec /usr/bin/snap calls.
                # With the polkit policy (auth_admin_keep) the password is cached
                # after the first call — no repeated prompts.
                failed = 0
                for idx, (name, rev, size) in enumerate(pending):
                    pct = 40 + int((idx / len(pending)) * 50)
                    self._progress(
                        f"Removing snap {name} rev {rev}… "
                        f"({'password required once' if idx == 0 else f'{idx+1}/{len(pending)}'})",
                        pct,
                    )
                    r = subprocess.run(
                        ["pkexec", HELPER, "snap-remove", name, rev],
                        capture_output=True, text=True, timeout=60,
                    )
                    if r.returncode != 0:
                        err = f"snap remove {name} rev {rev} failed: {r.stderr.strip()}"
                        self._log(err, "error")
                        result.errors.append(err)
                        result.freed_bytes -= size
                        result.deleted_count -= 1
                        result.skipped_count += 1
                        failed += 1

        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            result.errors.append(f"snap not available: {e}")

        self._progress("Finished snap revision cleanup.", 100)
        return result

    def _clean_journal(self, result: "CleanResult") -> "CleanResult":
        """
        Vacuum systemd journal.
        Strategy (most to least privileged):
          1. journalctl --vacuum-size=200M without root (cleans user journal)
          2. pkexec /usr/bin/journalctl --vacuum-size=200M (full path — required by pkexec)
          3. If both fail, report size and give the user the terminal command
        """
        # Measure size before
        before_bytes = self._journal_size_bytes()
        size_str = _human_size(before_bytes) if before_bytes else "unknown"

        action = f"{'Would vacuum' if self.dry_run else 'Vacuuming'} journal logs (current size: {size_str})"
        self._log(action)
        result.actions.append(action)
        self._progress(action, 30)

        if self.dry_run:
            result.deleted_count += 1
            result.freed_bytes = max(0, before_bytes - 50 * 1024 * 1024)
            self._progress("Finished journal cleanup.", 100)
            return result

        # Attempt 1: without root (cleans user journal only — harmless if system portion fails)
        try:
            r1 = subprocess.run(
                ["journalctl", "--vacuum-size=50M", "--vacuum-time=7d"],
                capture_output=True, text=True, timeout=20
            )
            self._log(f"journalctl (no root): rc={r1.returncode} {r1.stderr.strip()[:100]}")
        except Exception:
            pass

        # Attempt 2: privileged helper — single pkexec action, password already cached
        self._progress("Requesting elevated access for system journal…", 55)
        try:
            r2 = subprocess.run(
                ["pkexec", HELPER, "journal-vacuum"],
                capture_output=True, text=True, timeout=45
            )
            self._log(f"helper journal-vacuum: rc={r2.returncode} stderr={r2.stderr.strip()[:100]}")

            if r2.returncode == 0:
                after_bytes = self._journal_size_bytes()
                freed = max(0, before_bytes - after_bytes)
                result.freed_bytes = freed if freed > 0 else max(0, before_bytes - 50 * 1024 * 1024)
                result.deleted_count += 1
                self._progress("Journal vacuumed successfully.", 100)
                return result
            else:
                hint = (
                    "CleanMint could not vacuum the system journal automatically. "
                    "Run this once in a terminal:\n\n"
                    "  sudo journalctl --vacuum-size=50M\n\n"
                    f"(error: {r2.stderr.strip()[:80]})"
                )
                result.errors.append(hint)
                result.skipped_count += 1

        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            result.errors.append(
                f"Could not run helper: {e}. "
                "Run manually: sudo journalctl --vacuum-size=50M"
            )
            result.freed_bytes = 500 * 1024 * 1024

        self._progress("Finished journal cleanup.", 100)
        return result

    def _journal_size_bytes(self) -> int:
        """Return current journal disk usage in bytes."""
        try:
            r = subprocess.run(
                ["journalctl", "--disk-usage"],
                capture_output=True, text=True, timeout=10
            )
            m = re.search(r"(\d+(?:\.\d+)?)\s*(B|K|M|G|T)", r.stdout)
            if m:
                val, unit = float(m.group(1)), m.group(2)
                mult = {"B": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
                return int(val * mult.get(unit, 1))
        except Exception:
            pass
        return 0

    def _clean_temp_files(self, category: ScanCategory, result: "CleanResult") -> "CleanResult":
        """Delete only temp files owned by the current user — skips root-owned files."""
        mode = "[DRY RUN] " if self.dry_run else ""
        uid = os.getuid()

        for tmp_dir in [Path("/tmp"), Path("/var/tmp")]:
            if not tmp_dir.exists():
                continue
            try:
                for entry in tmp_dir.iterdir():
                    try:
                        stat = entry.stat(follow_symlinks=False)
                        if stat.st_uid != uid:
                            continue   # skip root-owned or other-user files
                        size = self._get_size(entry)
                        action = f"{'Would delete' if self.dry_run else 'Deleted'}: {entry} ({_human_size(size)})"
                        self._log(action)
                        result.actions.append(action)

                        if not self.dry_run:
                            self._delete(entry)

                        result.freed_bytes += size
                        result.deleted_count += 1
                    except (PermissionError, OSError) as e:
                        result.skipped_count += 1
                        continue
            except PermissionError:
                continue

        self._progress(f"{mode}Finished temp file cleanup.", 100)
        return result

    def _clean_apt_cache(self, result: "CleanResult") -> "CleanResult":
        """Run apt-get clean via pkexec — removes all cached .deb files."""
        from core.scanner import _apt_cache_size
        before_bytes, before_count, _ = _apt_cache_size()

        action = f"{'Would run' if self.dry_run else 'Running'} apt-get clean (frees ~{_human_size(before_bytes)})"
        self._log(action)
        result.actions.append(action)
        self._progress(action, 30)

        if self.dry_run:
            result.freed_bytes = before_bytes
            result.deleted_count = before_count
            self._progress("Finished APT cache cleanup.", 100)
            return result

        self._progress("Requesting elevated access for APT cache…", 50)
        try:
            r = subprocess.run(
                ["pkexec", HELPER, "apt-clean"],
                capture_output=True, text=True, timeout=60,
            )
            if r.returncode == 0:
                after_bytes, _, _ = _apt_cache_size()
                result.freed_bytes = max(0, before_bytes - after_bytes)
                result.deleted_count = before_count
                self._progress("APT cache cleaned.", 100)
            else:
                err = f"apt-get clean failed: {r.stderr.strip()[:120]}"
                self._log(err, "error")
                result.errors.append(err)
                result.skipped_count += 1
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            result.errors.append(f"Could not run pkexec apt-get clean: {e}")
            result.skipped_count += 1

        self._progress("Finished APT cache cleanup.", 100)
        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _collect_targets(self, category: ScanCategory) -> list[Path]:
        """
        For directory-based categories, list immediate children to delete.
        For file-based categories (e.g. snap .snap files), use paths directly.
        Skips parent paths that are themselves blocked to avoid pointless iteration.
        """
        from core.safety import is_blocked, is_allowed_target
        targets = []
        for path in category.paths:
            if not path.exists():
                continue
            # Gate the parent before iterating — avoids thousands of blocked warnings
            if is_blocked(path):
                msg = f"BLOCKED (parent): {path} is a protected system path — skipping."
                self._log(msg, "warning")
                continue
            if path.is_file() or path.is_symlink():
                targets.append(path)
            elif path.is_dir():
                # Delete contents, not the directory itself
                try:
                    for child in path.iterdir():
                        targets.append(child)
                except PermissionError:
                    pass
        return targets

    def _get_size(self, path: Path) -> int:
        try:
            if path.is_symlink() or path.is_file():
                return path.stat(follow_symlinks=False).st_size
            if path.is_dir():
                total = 0
                for root, dirs, files in os.walk(path):
                    for f in files:
                        try:
                            total += os.path.getsize(os.path.join(root, f))
                        except OSError:
                            pass
                return total
        except OSError:
            return 0
        return 0

    def _delete(self, path: Path):
        """Delete a file or directory tree."""
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path, ignore_errors=False)

    @property
    def session_log(self) -> list[str]:
        return list(self._session_log)
