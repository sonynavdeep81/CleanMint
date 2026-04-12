"""
tests/test_integration.py — Full end-to-end integration test

Covers: scan → dry-run clean → reporter (TXT/CSV/PDF).
All operations run against /tmp sandbox. Real system untouched.
"""

import sys
import shutil
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "cleanmint"))

from core.scanner import Scanner, ScanCategory
from core.cleaner import Cleaner
from core.reporter import export_txt, export_csv, export_pdf
from core.safety import validate_delete

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results = []

def check(label, cond, detail=""):
    status = PASS if cond else FAIL
    suffix = f" ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    results.append(cond)


print("\n=== CleanMint Integration Tests ===\n")

# ── Build sandbox that mimics real junk layout ──────────────────
sandbox = Path(tempfile.mkdtemp(prefix="cleanmint_integration_"))
home_sim = sandbox / "home"

def make(path: Path, size: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)

make(sandbox / ".cache/pip/wheels/pkg1.whl",        1024 * 200)
make(sandbox / ".cache/pip/wheels/pkg2.whl",        1024 * 300)
make(sandbox / ".cache/mozilla/profile/cache",      1024 * 512)
make(sandbox / ".local/share/Trash/files/old.iso",  1024 * 1024)
make(sandbox / ".local/share/thumbnails/img1.png",  1024 * 10)
make(sandbox / ".local/share/thumbnails/img2.png",  1024 * 10)
make(sandbox / "tmp/sess1.tmp",                     1024 * 5)
make(sandbox / "tmp/sess2.tmp",                     1024 * 5)
make(sandbox / ".npm/_cacache/content/pkg",         1024 * 100)
make(sandbox / "var/cache/apt/archives/libX.deb",   1024 * 400)
make(sandbox / "var/log/journal/session.log",       1024 * 1024)

print("1. Scan phase (sandbox)")
progress_msgs = []
scanner = Scanner(
    progress_callback=lambda m, p: progress_msgs.append(p),
    sandbox_root=sandbox,
)
categories = scanner.run_full_scan()

check("Returns list of categories",      isinstance(categories, list))
check("At least 6 categories",           len(categories) >= 6, f"{len(categories)} returned")
check("Final progress == 100",           progress_msgs[-1] == 100 if progress_msgs else False)
check("All categories have id+name",     all(c.id and c.name for c in categories))

cats_with_junk = [c for c in categories if c.size_bytes > 0]
check("Categories with junk > 0",        len(cats_with_junk) > 0, f"{len(cats_with_junk)} found")

total_junk = sum(c.size_bytes for c in categories)
check("Total junk > 0",                  total_junk > 0)

pip  = next((c for c in categories if c.id == "pip_cache"),    None)
trash= next((c for c in categories if c.id == "trash"),        None)
npm  = next((c for c in categories if c.id == "npm_cache"),    None)
browser = next((c for c in categories if c.id == "browser_cache"), None)

check("pip_cache size detected",         pip  and pip.size_bytes > 0,
      pip.size_human if pip else "missing")
check("trash size detected",             trash and trash.size_bytes > 0,
      trash.size_human if trash else "missing")
check("browser_cache size detected",     browser and browser.size_bytes > 0,
      browser.size_human if browser else "missing")


print("\n2. Dry-run clean phase")
low_risk = [c for c in categories if c.risk == "low" and c.size_bytes > 0]
check("Have low-risk categories",        len(low_risk) > 0, f"{len(low_risk)}")

cleaner = Cleaner(dry_run=True, log_to_disk=False)
dry_results = cleaner.clean_categories(low_risk)

check("Dry-run returns results",         len(dry_results) == len(low_risk))
total_would_free = sum(r.freed_bytes for r in dry_results)
check("Dry-run: freed_bytes > 0",        total_would_free > 0)
check("Dry-run: actions say 'Would delete'",
      any("Would delete" in a for r in dry_results for a in r.actions))

# Verify sandbox files still exist after dry-run
pip_file = sandbox / ".cache/pip/wheels/pkg1.whl"
check("Dry-run: sandbox files untouched", pip_file.exists())


print("\n3. Real clean phase (sandbox only)")
# Only clean /tmp-equivalent inside sandbox (safest subset)
tmp_cat = next((c for c in categories if c.id == "temp_files"), None)
if tmp_cat and tmp_cat.size_bytes > 0:
    # Patch paths to sandbox
    tmp_cat_copy = ScanCategory(
        id=tmp_cat.id, name=tmp_cat.name, description=tmp_cat.description,
        risk=tmp_cat.risk, recommended=tmp_cat.recommended,
        paths=[sandbox / "tmp"],
        size_bytes=tmp_cat.size_bytes,
    )
    real_cleaner = Cleaner(dry_run=False, log_to_disk=False)
    real_result = real_cleaner.clean_category(tmp_cat_copy)
    check("Real clean: files deleted",       real_result.deleted_count > 0,
          f"{real_result.deleted_count} deleted")
    check("Real clean: freed_bytes > 0",     real_result.freed_bytes > 0, real_result.freed_human)
    check("Real clean: no safety errors",    not any("BLOCKED" in e for e in real_result.errors))
    tmp_files = list((sandbox / "tmp").iterdir())
    check("Sandbox /tmp now empty",          len(tmp_files) == 0, f"{len(tmp_files)} remain")
else:
    check("Skipped (no temp_files found in sandbox)", True)


print("\n4. Reporter — TXT export")
report_dir = Path(tempfile.mkdtemp(prefix="cleanmint_reports_"))
txt_path = export_txt(
    report_dir / "scan_report.txt",
    categories=categories,
    results=dry_results,
    title="Integration Test Report",
)
check("TXT file created",                txt_path.exists())
check("TXT file non-empty",              txt_path.stat().st_size > 0)
content = txt_path.read_text()
check("TXT contains title",             "Integration Test Report" in content)
check("TXT contains category names",    any(c.name in content for c in categories))
check("TXT contains size data",         "MB" in content or "KB" in content or "GB" in content)


print("\n5. Reporter — CSV export")
csv_path = export_csv(
    report_dir / "scan_report.csv",
    categories=categories,
    results=dry_results,
)
check("CSV file created",                csv_path.exists())
check("CSV file non-empty",              csv_path.stat().st_size > 0)
csv_content = csv_path.read_text()
check("CSV has header row",             "Category" in csv_content)
check("CSV has data rows",              len(csv_content.splitlines()) > 5)


print("\n6. Reporter — PDF export")
try:
    pdf_path = export_pdf(
        report_dir / "scan_report.pdf",
        categories=categories,
        results=dry_results,
        title="Integration Test PDF",
    )
    check("PDF file created",            pdf_path.exists())
    check("PDF file > 1KB",              pdf_path.stat().st_size > 1024,
          f"{pdf_path.stat().st_size} bytes")
    check("PDF starts with %PDF",        pdf_path.read_bytes()[:4] == b"%PDF")
except ImportError as e:
    check(f"PDF skipped (reportlab missing): {e}", True)


print("\n7. Safety — integration guard")
system_paths = [Path("/etc/passwd"), Path("/usr/bin/python3"), Path("/boot")]
for p in system_paths:
    ok, reason = validate_delete(p)
    check(f"System path blocked: {p}", not ok)


# ── Cleanup ──────────────────────────────────────────────────────
shutil.rmtree(sandbox)
shutil.rmtree(report_dir)
check("Sandbox + reports cleaned up",    not sandbox.exists() and not report_dir.exists())


# ── Summary ──────────────────────────────────────────────────────
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
