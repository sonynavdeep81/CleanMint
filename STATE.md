# STATE — resume pointer

**Focus:** OBS Switcher feature — DONE + verified end-to-end on this machine.
All merged to `main`; `main` is ~13 commits ahead of `origin/main` (not pushed).

**What shipped:** `core/obs_switcher.py` + `ui/obs_switcher_page.py` +
`cleanmint-helper` obs-lock/obs-unlock + sidebar entry. Spec/plan in
`docs/superpowers/`. `test_obs_switcher.py` 95/95, `test_ui_imports.py` 15/15.

**Fixes made during user testing (all merged):**
- c8e4c95 — polkit prompt nagged every launch on failed/cancelled install;
  now `policy_signature()`-gated, asked once per asset version.
- 2f54a09 — Build failed "Operation not permitted" when protection (chattr +i)
  was on; `build()` returns `needs_unlock`, page offers unprotect→repair→re-protect.
- cb15b9f — `obs_running()` missed Flatpak OBS (bare `obs` process); now
  `pgrep -x obs` too; self_test precondition is just `websocket_reachable()`.
- (latest) — Build no longer warns about WebSocket when it is already enabled;
  only warns when genuinely off + OBS running (can't safely patch a live OBS).

**Verified:** Test Switching passes in the app (Connect/Laptop/Tablet/Restore ✓).
Protect/Unprotect work (rm refused while locked). check_status all green.

**Pre-existing failures (NOT this work, also on old main):**
`test_backend_phase5.py` "Source field valid"; `test_integration.py`
"Would delete" wording (+ a /tmp race from a concurrent process).

**Added (merged):** "Set Up Scenes" button — creates Laptop/Tablet scenes +
`pipewire-screen-capture-source` sources via WebSocket API, launches scrcpy
first, backs up config, leaves existing scenes untouched. Opt-in + confirm.
Scope chosen by user: scenes + sources + auto-launch scrcpy. Verified
idempotent against live OBS (everything present → "left as-is").
`test_obs_switcher.py` now 104 checks.

**Next action:** Nothing required. `git push` when ready
(main ~16 commits ahead of origin/main).

**Hints:** —
