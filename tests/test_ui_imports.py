"""
tests/test_ui_imports.py — Validate all modules import without errors.

Runs headlessly (no display needed) using QT_QPA_PLATFORM=offscreen.
Does NOT launch any windows.
"""

import sys
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "cleanmint"))

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results = []

def check(label, fn):
    try:
        fn()
        print(f"  [{PASS}] {label}")
        results.append(True)
    except Exception as e:
        print(f"  [{FAIL}] {label} — {e}")
        results.append(False)

print("\n=== CleanMint UI Import Tests (offscreen) ===\n")

print("1. Core modules")
check("core.safety",   lambda: __import__("core.safety"))
check("core.scanner",  lambda: __import__("core.scanner"))
check("core.cleaner",  lambda: __import__("core.cleaner"))
check("config.settings", lambda: __import__("config.settings"))

print("\n2. PyQt6 + theme")
check("PyQt6.QtWidgets", lambda: __import__("PyQt6.QtWidgets"))
check("ui.theme",        lambda: __import__("ui.theme"))

def assert_(cond):
    if not cond:
        raise AssertionError("assertion failed")

print("\n3. UI pages (import only, no window shown)")
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication(sys.argv)

check("ui.main_window",  lambda: __import__("ui.main_window"))
check("ui.dashboard",    lambda: __import__("ui.dashboard"))
check("ui.cleaner_page", lambda: __import__("ui.cleaner_page"))

print("\n4. Theme palette sanity")
from ui.theme import Theme, DARK, LIGHT
check("DARK palette has accent", lambda: assert_(DARK.accent.startswith("#")))
check("LIGHT palette has accent", lambda: assert_(LIGHT.accent.startswith("#")))
check("Theme.stylesheet() non-empty", lambda: assert_(len(Theme.stylesheet()) > 100))
check("Theme dark/light toggle",
      lambda: (Theme.set_light(), assert_(not Theme.is_dark()),
               Theme.set_dark(), assert_(Theme.is_dark())))

print("\n5. Settings load/save roundtrip")
from config.settings import Settings
def _settings_roundtrip():
    s = Settings()
    s.set("dark_mode", False)
    s2 = Settings()
    assert s2.get("dark_mode") == False
    s.set("dark_mode", True)
check("Settings roundtrip", _settings_roundtrip)

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
