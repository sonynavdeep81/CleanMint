# STATE — resume pointer

**Focus:** OBS Switcher feature — DONE, merged to `main` (merge commit a7c166b).
`main` is 2 commits ahead of `origin/main` (not pushed — push when ready).

**What shipped:** `core/obs_switcher.py` + `ui/obs_switcher_page.py` +
`cleanmint-helper` obs-lock/obs-unlock + sidebar entry. Spec/plan in
`docs/superpowers/`. Tests: `test_obs_switcher.py` 85/85, `test_ui_imports.py` 15/15.

**Pre-existing failures (NOT this work, also on old main):**
`test_backend_phase5.py` "Source field valid"; `test_integration.py`
"Would delete" wording (+ a /tmp race from a concurrent process).

**Next action:** User runs the interactive smoke test — launch app (accept the
polkit helper update prompt), OBS Switcher page: with OBS open click
"Test Switching"; "Protect Files" then confirm `rm ~/.local/bin/obs-scene` is
refused; "Unprotect Files"; "Check & Restore". Then `git push` if all good.

**Hints:** —
