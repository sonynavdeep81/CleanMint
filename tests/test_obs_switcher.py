"""
tests/test_obs_switcher.py — OBS Switcher engine tests (offline, sandboxed HOME).

No real OBS. HOME is redirected to a tmpdir; obs_switcher reads paths at call
time so the redirect takes effect.
"""

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "cleanmint"))

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results = []


def check(label, condition, detail=""):
    status = PASS if condition else FAIL
    suffix = f" ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    results.append(bool(condition))


def _fake_cp(stdout="", rc=0):
    import subprocess as sp
    return sp.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr="")


def new_home():
    """Fresh sandbox HOME; point every home-derived lookup at it."""
    d = Path(tempfile.mkdtemp(prefix="obsw_"))
    os.environ["HOME"] = str(d)
    return d


import core.obs_switcher as ob  # noqa: E402


print("\n=== OBS Switcher — pure helpers ===\n")

home = new_home()
p = ob._paths()
check("_paths().script under HOME", str(p.script).startswith(str(home)),
      str(p.script))
check("_paths().pw_file is ~/.config/obs-hotkeys/password",
      p.pw_file == home / ".config/obs-hotkeys/password")

src = ob.render_script("/x/venv/bin/python3", "/x/pw")
check("render_script shebang", src.splitlines()[0] == "#!/x/venv/bin/python3")
check("render_script embeds pw path", '"/x/pw"' in src)
check("render_script calls set_current_program_scene",
      "set_current_program_scene(scene)" in src)

print(f"\n{sum(results)}/{len(results)} checks passed\n")
sys.exit(0 if all(results) else 1)
