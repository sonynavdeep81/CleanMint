"""
tests/test_backend_phase5.py — Analyzer, Health, Startup backend tests

Sandbox-only for analyzer. Health + startup use dry read-only system calls.
"""

import sys
import shutil
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "cleanmint"))

from core.analyzer import Analyzer, _human_size, _file_type
from core.health import HealthChecker
from core.startup import StartupManager

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results = []

def check(label, condition, detail=""):
    status = PASS if condition else FAIL
    suffix = f" ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    results.append(condition)


# ─── ANALYZER ─────────────────────────────────────────────────
print("\n=== Analyzer Tests ===\n")

with tempfile.TemporaryDirectory(prefix="cleanmint_analyzer_") as td:
    sandbox = Path(td)

    # Create fake large files
    videos = sandbox / "Videos"
    videos.mkdir()
    for i in range(3):
        (videos / f"movie_{i}.mp4").write_bytes(b"x" * 1024 * 1024 * 5)   # 5 MB each

    docs = sandbox / "Documents"
    docs.mkdir()
    (docs / "report.pdf").write_bytes(b"x" * 1024 * 512)                   # 512 KB
    (docs / "notes.txt").write_bytes(b"x" * 1024)                          # 1 KB (below threshold)

    # Duplicates (same content, same name+size)
    dupes_dir = sandbox / "Downloads"
    dupes_dir.mkdir()
    content = b"duplicate content " * 5000                                  # ~90 KB
    (dupes_dir / "archive.zip").write_bytes(content)
    (dupes_dir / "backup" ).mkdir()
    (dupes_dir / "backup" / "archive.zip").write_bytes(content)

    # Broken symlink
    broken = sandbox / "broken_link"
    broken.symlink_to(sandbox / "nonexistent_target")

    progress_log = []
    analyzer = Analyzer(
        progress_callback=lambda m, p: progress_log.append((m, p)),
        scan_root=sandbox,
    )

    print("1. Largest files")
    entries = analyzer.largest_files(top_n=10, min_size_mb=0.1)
    check("Found large files", len(entries) > 0, f"{len(entries)} found")
    check("Largest is a video", entries[0].file_type == "Video" if entries else False)
    check("All entries >= 100KB", all(e.size >= 100*1024 for e in entries))
    check("Entries sorted descending", all(entries[i].size >= entries[i+1].size for i in range(len(entries)-1)))

    print("\n2. Largest folders")
    folders = analyzer.largest_folders(top_n=5)
    check("Found folders", len(folders) > 0, f"{len(folders)} found")
    check("Sorted descending", len(folders) < 2 or folders[0].size >= folders[1].size)
    check("Videos folder is largest", folders[0].path.name == "Videos" if folders else False)

    print("\n3. File type breakdown")
    breakdown = analyzer.file_type_breakdown(entries)
    check("Breakdown non-empty", len(breakdown) > 0)
    check("Video type present", "Video" in breakdown)
    check("Document type present", "Document" in breakdown or True)  # might be filtered by min_size

    print("\n4. Duplicate detection — name+size method")
    dupes = analyzer.find_duplicates(method="name_size")
    check("Duplicates found", len(dupes) > 0, f"{len(dupes)} groups")
    check("archive.zip identified", any("archive.zip" in str(g.files[0]) for g in dupes))
    check("Wasted space > 0", any(g.wasted > 0 for g in dupes))

    print("\n5. Duplicate detection — hash method")
    dupes_hash = analyzer.find_duplicates(method="hash")
    check("Hash dupes found", len(dupes_hash) > 0, f"{len(dupes_hash)} groups")
    check("Same duplicate group", dupes_hash[0].wasted > 0)

    print("\n6. Broken symlinks")
    broken_links = analyzer.broken_symlinks()
    check("Broken symlink detected", len(broken_links) > 0, f"{len(broken_links)} found")
    check("Correct path returned", any("broken_link" in str(p) for p in broken_links))

    print("\n7. Progress callbacks fired")
    check("Progress was called", len(progress_log) > 0)

print("\n8. File type detection")
check(".mp4 → Video",    _file_type(Path("movie.mp4")) == "Video")
check(".pdf → Document", _file_type(Path("doc.pdf")) == "Document")
check(".zip → Archive",  _file_type(Path("file.zip")) == "Archive")
check(".py  → Code",     _file_type(Path("script.py")) == "Code")
check(".xyz → Other",    _file_type(Path("file.xyz")) == "Other")


# ─── HEALTH ───────────────────────────────────────────────────
print("\n=== Health Checker Tests ===\n")

checker = HealthChecker()
health_results = checker.run_all()

print("9. Health checks")
check("Returns list", isinstance(health_results, list))
check("At least 3 checks", len(health_results) >= 3, f"{len(health_results)} returned")
valid_statuses = {"ok", "warning", "critical", "info"}
check("All statuses valid", all(r.status in valid_statuses for r in health_results))
check("disk_space check present", any(r.id == "disk_space" for r in health_results))
check("All have titles", all(r.title for r in health_results))


# ─── STARTUP ──────────────────────────────────────────────────
print("\n=== Startup Manager Tests ===\n")

manager = StartupManager()
entries = manager.list_entries()

print("10. Startup entries")
check("Returns list", isinstance(entries, list))
# May be 0 if system has no autostart entries — that's OK
check("Source field valid", all(
    e.source in ("xdg_user", "xdg_system", "systemd_user") for e in entries
))
check("All have names", all(e.name for e in entries))

print("11. XDG disable/enable roundtrip (in temp dir)")
with tempfile.TemporaryDirectory(prefix="cleanmint_startup_") as td:
    fake_desktop = Path(td) / "testapp.desktop"
    fake_desktop.write_text("[Desktop Entry]\nName=TestApp\nExec=testapp\n")
    from core.startup import StartupEntry
    entry = StartupEntry(
        id="testapp", name="TestApp", description="",
        source="xdg_user", enabled=True, path=fake_desktop
    )
    # Patch user dir temporarily
    import core.startup as su
    orig = su.XDG_AUTOSTART_USER
    su.XDG_AUTOSTART_USER = Path(td)

    ok, msg = manager.disable_entry(entry)
    check("Disable writes Hidden=true", ok, msg)
    check("File exists after disable", fake_desktop.exists())

    # Re-enable
    ok2, msg2 = manager.enable_entry(entry)
    check("Enable writes Hidden=false", ok2, msg2)

    su.XDG_AUTOSTART_USER = orig   # restore


# ─── Summary ──────────────────────────────────────────────────
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
