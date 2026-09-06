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

print("\n=== OBS Switcher — plan_shortcut_slots ===\n")

_BASE = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
SCRIPT = "/home/u/.local/bin/obs-scene"

# custom0 = user's unrelated shortcut, custom1 = an existing obs Tablet binding
existing = [_BASE + "custom0/", _BASE + "custom1/"]
existing_cmds = {
    _BASE + "custom0/": "'/usr/bin/flameshot gui'",
    _BASE + "custom1/": f"'{SCRIPT} Tablet'",
}
plan, final_list = ob.plan_shortcut_slots(existing, existing_cmds, SCRIPT)

by_scene = {entry[3].split()[-1]: entry for entry in plan}
check("reuses custom1 for Tablet",
      by_scene["Tablet"][0] == _BASE + "custom1/")
check("allocates a fresh slot for Laptop (not custom0/custom1)",
      by_scene["Laptop"][0] not in (_BASE + "custom0/", _BASE + "custom1/"))
check("keeps user's custom0 in the final list",
      _BASE + "custom0/" in final_list)
check("final list contains both obs slots",
      by_scene["Laptop"][0] in final_list and by_scene["Tablet"][0] in final_list)
check("plan carries correct accelerator for Laptop",
      by_scene["Laptop"][1] == "<Control><Alt>1")
check("plan command is absolute script + scene",
      by_scene["Laptop"][3] == f"{SCRIPT} Laptop")

# empty starting point
plan2, final2 = ob.plan_shortcut_slots([], {}, SCRIPT)
check("from empty: two slots allocated", len(plan2) == 2 and len(final2) == 2)
check("from empty: slots are custom0 and custom1",
      sorted(final2) == [_BASE + "custom0/", _BASE + "custom1/"])


print("\n=== OBS Switcher — environment probes ===\n")

home = new_home()
p = ob._paths()

# read_obs_password: no config yet
check("read_obs_password None when config missing", ob.read_obs_password() is None)

# create a fake OBS websocket config
p.obs_ws_config.parent.mkdir(parents=True)
p.obs_ws_config.write_text(json.dumps({
    "server_enabled": True, "server_port": 4455,
    "auth_required": True, "server_password": "s3cr3t",
}))
check("read_obs_password reads server_password", ob.read_obs_password() == "s3cr3t")

# malformed config -> None, no raise
p.obs_ws_config.write_text("{ not json")
check("read_obs_password None on bad json", ob.read_obs_password() is None)

# websocket_reachable: nothing listening on 4455 in CI
check("websocket_reachable False when nothing listens",
      ob.websocket_reachable(timeout=0.2) is False)

# obs_running returns a bool and does not raise
check("obs_running returns bool", isinstance(ob.obs_running(), bool))


print("\n=== OBS Switcher — set_password / _write_script ===\n")

home = new_home()
p = ob._paths()

ob.set_password("hunter2")
check("password file created", p.pw_file.is_file())
check("password file mode 600",
      stat.S_IMODE(p.pw_file.stat().st_mode) == 0o600)
check("password dir mode 700",
      stat.S_IMODE(p.pw_dir.stat().st_mode) == 0o700)
check("password content", p.pw_file.read_text() == "hunter2")

ob._write_script()
check("script created", p.script.is_file())
check("script executable", os.access(p.script, os.X_OK))
check("script shebang points at venv py",
      p.script.read_text().splitlines()[0] == f"#!{p.venv_py}")
check("script embeds pw file path", f'"{p.pw_file}"' in p.script.read_text())


print(f"\n{sum(results)}/{len(results)} checks passed\n")
sys.exit(0 if all(results) else 1)
