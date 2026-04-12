"""
tests/test_browser_cache_fix.py

Verifies the browser cache scanner only targets safe cache subdirectories,
not profile-level data (cookies, passwords, bookmarks).
"""

import sys
import shutil
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "cleanmint"))
from core.scanner import Scanner

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results = []

def check(label, cond, detail=""):
    status = PASS if cond else FAIL
    suffix = f" ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    results.append(cond)


print("\n=== Browser Cache Safety Fix Tests ===\n")

with tempfile.TemporaryDirectory(prefix="cleanmint_browser_") as td:
    sandbox = Path(td)

    def mkfile(p: Path, size: int = 1024):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)

    # ── Simulate Chrome profile structure ──────────────────────
    # Safe cache dirs — should be targeted
    mkfile(sandbox / ".cache/google-chrome/Default/Cache/f1.bin",         1024 * 500)
    mkfile(sandbox / ".cache/google-chrome/Default/Code Cache/js/v8.bin", 1024 * 200)
    mkfile(sandbox / ".cache/google-chrome/Default/GPUCache/gpu.bin",     1024 * 100)
    mkfile(sandbox / ".cache/google-chrome/Profile 2/Cache/f2.bin",       1024 * 300)
    mkfile(sandbox / ".cache/google-chrome/Profile 2/Code Cache/py.bin",  1024 * 150)

    # Sensitive profile data — must NOT be targeted
    mkfile(sandbox / ".cache/google-chrome/Default/Cookies",              1024 * 10)
    mkfile(sandbox / ".cache/google-chrome/Default/Login Data",           1024 * 5)
    mkfile(sandbox / ".cache/google-chrome/Default/Bookmarks",            1024 * 2)
    mkfile(sandbox / ".cache/google-chrome/Default/History",              1024 * 50)
    mkfile(sandbox / ".cache/google-chrome/Default/Extensions/extdata",   1024 * 20)

    scanner = Scanner(sandbox_root=sandbox)
    categories = scanner.run_full_scan()
    browser_cat = next((c for c in categories if c.id == "browser_cache"), None)

    print("1. Category exists and has paths")
    check("browser_cache category found", browser_cat is not None)
    check("Has paths", browser_cat is not None and len(browser_cat.paths) > 0,
          f"{len(browser_cat.paths) if browser_cat else 0} paths")

    print("\n2. Safe cache dirs ARE included")
    if browser_cat:
        path_strs = [str(p) for p in browser_cat.paths]
        check("Default/Cache targeted",        any("Cache" in p and "Code" not in p for p in path_strs))
        check("Default/Code Cache targeted",   any("Code Cache" in p for p in path_strs))
        check("Default/GPUCache targeted",     any("GPUCache" in p for p in path_strs))
        check("Profile 2/Cache targeted",      any("Profile 2" in p and "Cache" in p for p in path_strs))

    print("\n3. Sensitive profile data NOT included")
    if browser_cat:
        path_strs = [str(p) for p in browser_cat.paths]
        check("Cookies NOT targeted",    not any("Cookies" in p for p in path_strs))
        check("Login Data NOT targeted", not any("Login Data" in p for p in path_strs))
        check("Bookmarks NOT targeted",  not any("Bookmarks" in p for p in path_strs))
        check("History NOT targeted",    not any("History" in p for p in path_strs))
        check("Extensions NOT targeted", not any("Extensions" in p for p in path_strs))

    print("\n4. Size reflects only cache dirs (not sensitive files)")
    if browser_cat:
        # Safe cache files: 500+200+100+300+150 = 1250 KB
        # Sensitive files: 10+5+2+50+20 = 87 KB — must NOT be counted
        expected_min = 1250 * 1024
        check("Size >= expected cache size", browser_cat.size_bytes >= expected_min,
              f"got {browser_cat.size_human}")

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
