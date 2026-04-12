"""
tests/test_scanner.py — Scanner validation

All tests run against a /tmp sandbox — nothing on the real system is touched.
The sandbox mimics the structure of real junk directories.
"""

import sys
import shutil
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "cleanmint"))

from core.scanner import Scanner, _human_size

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results = []

def check(label: str, condition: bool, detail: str = ""):
    status = PASS if condition else FAIL
    suffix = f" ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    results.append(condition)


# ----------------------------------------------------------------
# Build sandbox mimicking real junk structure
# ----------------------------------------------------------------
sandbox = Path(tempfile.mkdtemp(prefix="cleanmint_scanner_test_"))
HOME_REL = Path.home()

def make_fake_files(directory: Path, count: int, size_bytes: int = 1024):
    directory.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        f = directory / f"fake_{i}.tmp"
        f.write_bytes(b"x" * size_bytes)

print(f"\n=== CleanMint Scanner Tests ===")
print(f"Sandbox: {sandbox}\n")

# Thumbnails
make_fake_files(sandbox / ".local/share/thumbnails/normal", 5, 2048)
make_fake_files(sandbox / ".thumbnails", 3, 1024)

# Browser cache
make_fake_files(sandbox / ".cache/mozilla/firefox/profile/cache2", 10, 4096)
make_fake_files(sandbox / ".cache/google-chrome/Default/Cache", 8, 4096)

# Trash
make_fake_files(sandbox / ".local/share/Trash/files", 4, 8192)

# pip cache
make_fake_files(sandbox / ".cache/pip/wheels", 6, 16384)

# npm cache
make_fake_files(sandbox / ".npm/_cacache/content-v2", 3, 8192)

# APT cache (simulated)
apt_dir = sandbox / "var/cache/apt/archives"
apt_dir.mkdir(parents=True, exist_ok=True)
for i in range(3):
    (apt_dir / f"package_{i}.deb").write_bytes(b"x" * 1024 * 512)  # 512KB each

# Journal logs (simulated)
journal_dir = sandbox / "var/log/journal"
make_fake_files(journal_dir, 2, 1024 * 1024)  # 1MB each

# Temp files
make_fake_files(sandbox / "tmp", 5, 512)


# ----------------------------------------------------------------
# Run scanner against sandbox
# ----------------------------------------------------------------
progress_log = []
def on_progress(msg, pct):
    progress_log.append((msg, pct))

scanner = Scanner(progress_callback=on_progress, sandbox_root=sandbox)
categories = scanner.run_full_scan()

# ----------------------------------------------------------------
# Assertions
# ----------------------------------------------------------------
print("1. Progress callbacks fired")
check("At least 5 progress updates", len(progress_log) >= 5, f"got {len(progress_log)}")
check("Final progress is 100%", progress_log[-1][1] == 100)

print("\n2. Category structure")
cat_ids = {c.id for c in categories}
required = {"thumbnails", "browser_cache", "trash", "pip_cache", "npm_cache", "temp_files"}
for cid in required:
    check(f"Category '{cid}' present", cid in cat_ids)

print("\n3. Size detection")
by_id = {c.id: c for c in categories}

thumb = by_id["thumbnails"]
check("Thumbnails: detected files", thumb.file_count > 0, f"{thumb.file_count} files")
check("Thumbnails: size > 0", thumb.size_bytes > 0, thumb.size_human)

browser = by_id["browser_cache"]
check("Browser cache: detected files", browser.file_count > 0, f"{browser.file_count} files")
check("Browser cache: size > 0", browser.size_bytes > 0, browser.size_human)

trash = by_id["trash"]
check("Trash: detected files", trash.file_count > 0, f"{trash.file_count} files")
check("Trash: size > 0", trash.size_bytes > 0, trash.size_human)

pip = by_id["pip_cache"]
check("pip cache: detected files", pip.file_count > 0, f"{pip.file_count} files")
check("pip cache: size > 0", pip.size_bytes > 0, pip.size_human)

print("\n4. Human-readable sizes")
check("_human_size(0) = '0.0 B'", _human_size(0) == "0.0 B")
check("_human_size(1024) = '1.0 KB'", _human_size(1024) == "1.0 KB")
check("_human_size(1048576) = '1.0 MB'", _human_size(1048576) == "1.0 MB")
check("_human_size(1073741824) = '1.0 GB'", _human_size(1073741824) == "1.0 GB")

print("\n5. Risk levels")
for cat in categories:
    check(f"'{cat.id}' has valid risk level", cat.risk in ("low", "medium", "expert"))

print("\n6. Sandbox isolation (real system untouched)")
real_thumbnail = Path.home() / ".local" / "share" / "thumbnails" / "_cleanmint_test_marker"
check("Real ~/.local/share/thumbnails NOT written to", not real_thumbnail.exists())

# ----------------------------------------------------------------
# Cleanup sandbox
# ----------------------------------------------------------------
shutil.rmtree(sandbox)
check("Sandbox cleaned up", not sandbox.exists())

# ----------------------------------------------------------------
# Summary
# ----------------------------------------------------------------
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
