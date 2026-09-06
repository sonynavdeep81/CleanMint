"""
tests/test_polkit_prompt.py — the polkit setup prompt is offered at most once
per version of the policy/helper assets.
"""

import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).parent.parent / "cleanmint"))

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results = []


def check(label, condition, detail=""):
    status = PASS if condition else FAIL
    suffix = f" ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    results.append(bool(condition))


print("\n=== polkit prompt — signature ===\n")

from core import installer  # noqa: E402

sig1 = installer.policy_signature()
sig2 = installer.policy_signature()
check("signature is stable", sig1 == sig2, sig1)
check("signature is short hex", len(sig1) == 16 and all(c in "0123456789abcdef" for c in sig1))

print("\n=== polkit prompt — asked at most once per version ===\n")

from PyQt6.QtWidgets import QApplication  # noqa: E402
app = QApplication.instance() or QApplication(sys.argv)

from config.settings import settings  # noqa: E402
from ui import main_window as mw  # noqa: E402

# Pretend the policy is NOT installed so the prompt logic runs.
mw.is_policy_installed = lambda: False

shown = {"count": 0}


class _FakeMsg:
    """Stand-in for QMessageBox that records that a prompt was shown and
    immediately answers "No" (Skip)."""

    Icon = type("Icon", (), {"Information": 0, "Warning": 1})
    StandardButton = type("SB", (), {"Yes": 0x4000, "No": 0x10000})

    def __init__(self, *a, **k):
        shown["count"] += 1

    def setWindowTitle(self, *a):
        pass

    setText = setIcon = setStandardButtons = setDefaultButton = setWindowTitle

    def button(self, *_a):
        return type("B", (), {"setText": lambda *_x: None})()

    def exec(self):
        return self.StandardButton.No


mw.QMessageBox = _FakeMsg

win = mw.MainWindow.__new__(mw.MainWindow)  # no full __init__ / no real window

settings.set("polkit_prompt_handled_for", "")
settings.set("polkit_setup_declined", False)

win._check_polkit_setup()
first = shown["count"]
check("prompt shown the first time", first == 1)
check("dismissal was remembered",
      settings.get("polkit_prompt_handled_for", "") == installer.policy_signature())

win._check_polkit_setup()
win._check_polkit_setup()
check("prompt NOT shown again for the same version", shown["count"] == first)

# A new version of the assets → offered once more.
settings.set("polkit_prompt_handled_for", "different-old-hash")
win._check_polkit_setup()
check("prompt shown again after assets change", shown["count"] == first + 1)

# reset so a real run of the app is unaffected by this test
settings.set("polkit_prompt_handled_for", "")
settings.set("polkit_setup_declined", False)

print(f"\n{sum(results)}/{len(results)} checks passed\n")
sys.exit(0 if all(results) else 1)
