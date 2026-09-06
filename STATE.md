# STATE — resume pointer

**Focus:** OBS Switcher feature — DONE, merged to `main` (merge commit a7c166b).
`main` is 2 commits ahead of `origin/main` (not pushed — push when ready).

**What shipped:** `core/obs_switcher.py` + `ui/obs_switcher_page.py` +
`cleanmint-helper` obs-lock/obs-unlock + sidebar entry. Spec/plan in
`docs/superpowers/`. Tests: `test_obs_switcher.py` 85/85, `test_ui_imports.py` 15/15.

**Pre-existing failures (NOT this work, also on old main):**
`test_backend_phase5.py` "Source field valid"; `test_integration.py`
"Would delete" wording (+ a /tmp race from a concurrent process).

**Also fixed (merge c8e4c95):** polkit setup prompt nagged on every launch when
an update install failed/was cancelled — `_check_polkit_setup` now records
`policy_signature()` on skip/fail and re-asks only when the assets change.
On this machine `is_policy_installed()` is already True + helper current
(verified lock/unlock work end-to-end) → no prompt should appear.

**Also fixed (merge 2f54a09):** Build/Repair failed with "Operation not
permitted" when protection was ON (chattr +i). `build()` now returns
`needs_unlock`; the page offers unprotect→repair→re-protect in one step.
`websocket_reachable(port=…)` added so tests don't assume 4455 is free.
User's files are currently LOCKED (they clicked Protect) — expected.
Their status list is all-green; Build/Repair is only for a broken setup.

**Also fixed (merge cb15b9f):** `obs_running()` only matched
"com.obsproject.Studio" — Flatpak OBS is a bare `obs` process, so Test
Switching wrongly said "Start OBS first". Now also `pgrep -x obs`;
self_test precondition is just `websocket_reachable()`. VERIFIED end-to-end
against live OBS: Connect/Laptop/Tablet/Restore all pass. Scene switching works.

**Next action:** User relaunches app → Test Switching should pass now.
Then `git push` (main ahead of origin/main ~9 commits). Feature is done + verified.

**Hints:** —
