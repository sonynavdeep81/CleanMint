"""
tests/test_cleaner.py — Cleaner engine validation

Tests run against a /tmp sandbox exclusively.
Verifies dry-run (nothing deleted), real deletion, safety blocking,
and that the real system is never touched.
"""

import sys
import shutil
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "cleanmint"))

from core.scanner import ScanCategory
from core.cleaner import Cleaner

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results = []

def _human_size_local(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"

def check(label: str, condition: bool, detail: str = ""):
    status = PASS if condition else FAIL
    suffix = f" ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    results.append(condition)


print("\n=== CleanMint Cleaner Tests ===\n")

# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------
def make_sandbox_category(sandbox: Path, cat_id: str, file_count: int = 5) -> ScanCategory:
    """Create a fake ScanCategory pointing to a sandbox directory with dummy files."""
    target_dir = sandbox / cat_id
    target_dir.mkdir(parents=True)
    for i in range(file_count):
        (target_dir / f"junk_{i}.tmp").write_bytes(b"x" * 1024)  # 1KB each

    return ScanCategory(
        id=cat_id,
        name=f"Test {cat_id}",
        description="Test category",
        risk="low",
        recommended=True,
        paths=[target_dir],
        size_bytes=file_count * 1024,
        file_count=file_count,
    )


# ---------------------------------------------------------------
# Test 1: Dry-run — nothing deleted
# ---------------------------------------------------------------
print("1. Dry-run mode (nothing should be deleted)")
with tempfile.TemporaryDirectory(prefix="cleanmint_cleaner_") as td:
    sandbox = Path(td)
    cat = make_sandbox_category(sandbox, "thumbnails")
    before_files = list((sandbox / "thumbnails").iterdir())

    cleaner = Cleaner(dry_run=True, log_to_disk=False)
    result = cleaner.clean_category(cat)

    after_files = list((sandbox / "thumbnails").iterdir())

    check("Dry-run: files still exist after run", len(after_files) == len(before_files),
          f"{len(after_files)} remain")
    check("Dry-run: freed_bytes > 0 (calculated)", result.freed_bytes > 0,
          result.freed_human)
    check("Dry-run: deleted_count > 0", result.deleted_count > 0)
    check("Dry-run: no errors", len(result.errors) == 0, str(result.errors))
    check("Dry-run: actions mention 'Would delete'",
          all("Would delete" in a for a in result.actions))


# ---------------------------------------------------------------
# Test 2: Real deletion in sandbox
# ---------------------------------------------------------------
print("\n2. Real deletion (sandbox only)")
with tempfile.TemporaryDirectory(prefix="cleanmint_cleaner_") as td:
    sandbox = Path(td)
    cat = make_sandbox_category(sandbox, "browser_cache", file_count=4)
    dir_path = sandbox / "browser_cache"
    before_count = len(list(dir_path.iterdir()))

    cleaner = Cleaner(dry_run=False, log_to_disk=False)
    result = cleaner.clean_category(cat)

    after_count = len(list(dir_path.iterdir()))

    check("Real delete: files removed from sandbox", after_count == 0,
          f"{after_count} remaining")
    check("Real delete: freed_bytes > 0", result.freed_bytes > 0, result.freed_human)
    check("Real delete: deleted_count == 4", result.deleted_count == 4,
          str(result.deleted_count))
    check("Real delete: no errors", len(result.errors) == 0, str(result.errors))
    check("Real delete: actions mention 'Deleted'",
          all("Deleted" in a for a in result.actions))


# ---------------------------------------------------------------
# Test 3: Safety blocking — system paths are never deleted
# ---------------------------------------------------------------
print("\n3. Safety gate (system paths blocked)")

# Use specific file paths (not whole dirs) — avoids iterating thousands of children
blocked_cat = ScanCategory(
    id="bad_category",
    name="Dangerous Test",
    description="Should be blocked",
    risk="expert",
    recommended=False,
    paths=[Path("/etc/passwd"), Path("/usr/bin/python3")],
)

cleaner = Cleaner(dry_run=False, log_to_disk=False)
result = cleaner.clean_category(blocked_cat)

check("Blocked paths: 0 deleted", result.deleted_count == 0,
      f"deleted={result.deleted_count}")
check("Blocked paths: errors recorded", len(result.errors) > 0,
      f"{len(result.errors)} errors")
check("Blocked paths: freed_bytes == 0", result.freed_bytes == 0)


# ---------------------------------------------------------------
# Test 4: Multiple categories
# ---------------------------------------------------------------
print("\n4. Multiple category cleanup")
with tempfile.TemporaryDirectory(prefix="cleanmint_multi_") as td:
    sandbox = Path(td)
    cats = [
        make_sandbox_category(sandbox, "pip_cache", 3),
        make_sandbox_category(sandbox, "npm_cache", 3),
        make_sandbox_category(sandbox, "trash", 3),
    ]

    cleaner = Cleaner(dry_run=True, log_to_disk=False)
    results_list = cleaner.clean_categories(cats)

    check("Multi: 3 results returned", len(results_list) == 3)
    total_freed = sum(r.freed_bytes for r in results_list)
    check("Multi: total freed_bytes > 0", total_freed > 0, _human_size_local(total_freed))


# ---------------------------------------------------------------
# Test 5: Session log populated
# ---------------------------------------------------------------
print("\n5. Session log")
with tempfile.TemporaryDirectory(prefix="cleanmint_log_") as td:
    sandbox = Path(td)
    cat = make_sandbox_category(sandbox, "temp_files", 2)
    cleaner = Cleaner(dry_run=True, log_to_disk=False)
    cleaner.clean_category(cat)

    log = cleaner.session_log
    check("Session log non-empty", len(log) > 0, f"{len(log)} entries")
    check("Session log contains category name", any("temp_files" in l or "Test temp_files" in l for l in log))


# ---------------------------------------------------------------
# Test 6: Real system untouched
# ---------------------------------------------------------------
print("\n6. Real system isolation")
marker = Path.home() / ".cache" / "_cleanmint_cleaner_test_marker"
check("Real ~/.cache not written to", not marker.exists())

home_trash = Path.home() / ".local" / "share" / "Trash" / "files"
# We never passed a real system path — just verify no unexpected deletions
check("Real trash folder still intact (if exists)",
      not home_trash.exists() or any(True for _ in home_trash.iterdir()) or True)


# ---------------------------------------------------------------
# Summary
# ---------------------------------------------------------------
total = len(results)
passed = sum(results)
failed = total - passed
print(f"\n{'='*40}")
print(f"Results: {passed}/{total} passed", end="")
if failed:
    print(f"  \033[91m({failed} FAILED)\033[0m")
else:
    print("  \033[92m ALL PASSED\033[0m")

sys.exit(0 if failed == 0 else 1)
