# STATE — resume pointer

**Focus:** OBS Switcher feature — code complete on branch `feat/obs-switcher`.

**Last decision:** Implemented per plan
`docs/superpowers/plans/2026-09-06-obs-switcher.md` (Tasks 1–12).
Engine `core/obs_switcher.py`, page `ui/obs_switcher_page.py`, helper
`obs-lock`/`obs-unlock`, sidebar entry. `tests/test_obs_switcher.py` 85/85,
`test_ui_imports.py` 15/15. Real-machine check_status all green (tablet=info,
backup created). Offscreen page smoke passed.

**Pre-existing failures (NOT from this work, present on main):**
`test_backend_phase5.py` "Source field valid"; `test_integration.py`
"Would delete" wording + a /tmp race from a concurrent process.

**Next action:** User to run the interactive smoke test (Task 12 step 4):
launch app → OBS Switcher page; with OBS open click "Test Switching";
click "Protect Files" (pkexec) then confirm `rm ~/.local/bin/obs-scene` is
refused; "Unprotect Files"; "Check & Restore". Then merge (finishing-a-branch).
NOTE: helper changed → app will prompt to update the polkit helper on launch.

**Hints:** —
